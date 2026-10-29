"""OpenCode V2 worker over the locally authenticated CLI and its live OpenAPI.

Only operation identifiers have semantic meaning here; all request routes,
parameters and body fields must be published by the active instance.
The schema-3 shell transport is intentionally untouched.
"""

import json
import re
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from .base import ProviderAdapter, ProviderError, json_object, positive_seconds


class Contract:
    """Resolve the JSON-schema subset needed for the published V2 operations."""

    def __init__(self, document):
        if not str(document.get("openapi", "")).startswith("3.") or not isinstance(document.get("paths"), dict):
            raise ProviderError("active /openapi.json is not an OpenAPI 3 document")
        self.document = document
        self.operations = {}
        for path, item in document["paths"].items():
            if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
                raise ProviderError("OpenAPI contains an invalid local route")
            if not isinstance(item, dict):
                continue
            for method in ("get", "post"):
                operation = item.get(method)
                if not isinstance(operation, dict) or not operation.get("operationId"):
                    continue
                name = operation["operationId"]
                if name in self.operations:
                    raise ProviderError("ambiguous OpenAPI operation identifier")
                self.operations[name] = (method.upper(), path, operation,
                                         item.get("parameters", []))

    def resolve(self, schema, depth=0):
        if not isinstance(schema, dict) or depth > 30:
            raise ProviderError("unsupported or recursive OpenAPI schema")
        if "$ref" in schema:
            ref = schema["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/"):
                raise ProviderError("external OpenAPI references are unsupported")
            target = self.document
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return self.resolve(target, depth + 1)
        if "allOf" in schema:
            merged = dict(schema)
            merged.pop("allOf")
            for part in schema["allOf"]:
                part = self.resolve(part, depth + 1)
                merged["properties"] = {**merged.get("properties", {}), **part.get("properties", {})}
                merged["required"] = list(set(merged.get("required", []) + part.get("required", [])))
                if "type" in part:
                    merged["type"] = part["type"]
            return merged
        for union in ("anyOf", "oneOf"):
            if union in schema:
                choices = [self.resolve(item, depth + 1) for item in schema[union]]
                choices = [item for item in choices if item.get("type") != "null"]
                if len(choices) == 1:
                    return choices[0]
        return schema

    def operation(self, name, method=None):
        if name not in self.operations:
            raise ProviderError(f"active OpenAPI lacks required operation {name}")
        result = self.operations[name]
        if method and result[0] != method:
            raise ProviderError(f"unexpected method for operation {name}")
        return result

    def body(self, name):
        operation = self.operation(name)[2]
        request = self.resolve(operation.get("requestBody", {}))
        schema = request.get("content", {}).get("application/json", {}).get("schema")
        if not schema:
            raise ProviderError(f"{name} has no published JSON request body")
        return self.resolve(schema)

    def parameters(self, name):
        _, _, operation, inherited = self.operation(name)
        parameters = [self.resolve(item) for item in [*inherited, *operation.get("parameters", [])]]
        return {(item.get("in"), item.get("name")): item for item in parameters}

    def validate(self, value, schema, depth=0):
        if depth > 30:
            raise ProviderError("request schema nesting is unsupported")
        schema = self.resolve(schema)
        for union in ("oneOf", "anyOf"):
            if union in schema:
                for choice in schema[union]:
                    try:
                        self.validate(value, choice, depth + 1)
                        return
                    except ProviderError:
                        pass
                raise ProviderError("request does not match a published schema variant")
        if "const" in schema and value != schema["const"]:
            raise ProviderError("request does not match a published constant")
        if "enum" in schema and value not in schema["enum"]:
            raise ProviderError("request identifier or option is not in the published enum")
        kind = schema.get("type")
        kinds = kind if isinstance(kind, list) else [kind]
        actual = ("null" if value is None else "boolean" if isinstance(value, bool)
                  else "string" if isinstance(value, str) else "object" if isinstance(value, dict)
                  else "array" if isinstance(value, list) else "integer" if isinstance(value, int)
                  else "number" if isinstance(value, float) else "unsupported")
        if kind and actual not in kinds and not (actual == "integer" and "number" in kinds):
            raise ProviderError("request field type differs from active schema")
        if isinstance(value, str):
            if len(value) < schema.get("minLength", 0) or (
                    "pattern" in schema and not re.search(schema["pattern"], value)):
                raise ProviderError("request identifier does not match active schema")
        if isinstance(value, dict):
            properties = schema.get("properties", {})
            if any(key not in value for key in schema.get("required", [])):
                raise ProviderError("request lacks fields required by active schema")
            # Do not guess undocumented body fields, even if additionalProperties permits them.
            if any(key not in properties for key in value):
                raise ProviderError("request contains fields not published by active schema")
            for key, item in value.items():
                self.validate(item, properties[key], depth + 1)
        if isinstance(value, list):
            for item in value:
                self.validate(item, schema.get("items", {}), depth + 1)


def data_object(record):
    data = record.get("data", record)
    if not isinstance(data, dict):
        raise ProviderError("OpenCode response lacks an object")
    return data


class OpenCodeAdapter(ProviderAdapter):
    provider = "opencode"

    def _api(self, method, path, workspace, *, body=None, trace=None, deadline=None):
        args = ["api", method, path]
        if body is not None:
            args.extend(["--data", json.dumps(body, ensure_ascii=False)])
        timeout = min(20, self.timeout)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("OpenCode deadline expired; reconcile before retrying")
            timeout = min(timeout, remaining)
        if trace:
            trace.append({"request": {"method": method, "route": path}})
            if method == "POST":
                trace.dispatched = True
        output = self._invoke(args, workspace, timeout=timeout, stdin="", trace=trace)
        if output.timed_out:
            raise ProviderError("OpenCode API timeout; request effects are unknown; reconcile before retrying")
        if output.returncode != 0:
            raise ProviderError("OpenCode API request failed; inspect the redacted trace and reconcile before retrying")
        return json_object(output.stdout) if output.stdout.strip() else {}

    def _request(self, contract, name, workspace, *, body=None, session_id=None,
                 query=None, trace=None, deadline=None):
        method, route, _, _ = contract.operation(name)
        parameters = contract.parameters(name)
        path_values = {} if session_id is None else {"sessionID": session_id}
        for key, value in path_values.items():
            parameter = parameters.get(("path", key))
            if parameter is None or "{" + key + "}" not in route:
                raise ProviderError("active operation has no published sessionID path parameter")
            contract.validate(value, parameter.get("schema", {}))
            route = route.replace("{" + key + "}", quote(value, safe=""))
        if "{" in route:
            raise ProviderError("operation needs unconfigured path parameters")
        query = query or {}
        for key, value in query.items():
            parameter = parameters.get(("query", key))
            if parameter is None:
                raise ProviderError("operation has no published query parameter")
            contract.validate(value, parameter.get("schema", {}))
        for (where, key), parameter in parameters.items():
            supplied = path_values if where == "path" else query if where == "query" else {}
            if parameter.get("required") and key not in supplied:
                raise ProviderError("operation needs unconfigured required parameters")
        if body is not None:
            contract.validate(body, contract.body(name))
        if query:
            route += "?" + urlencode(query)
        return self._api(method, route, workspace, body=body, trace=trace, deadline=deadline)

    def _discover(self, workspace, trace=None, deadline=None, *, require_workspace=True):
        document = self._api("GET", "/openapi.json", workspace, trace=trace, deadline=deadline)
        contract = Contract(document)
        for name, method in (("location.get", "GET"), ("agent.list", "GET"),
                             ("model.list", "GET"), ("session.create", "POST"),
                             ("session.prompt", "POST"), ("session.get", "GET"),
                             ("session.message.list", "GET")):
            contract.operation(name, method)
        create = contract.body("session.create")
        prompt = contract.body("session.prompt")
        if not {"location", "agent", "model"} <= create.get("properties", {}).keys():
            raise ProviderError("session creation cannot explicitly select workspace, agent and model")
        if "text" not in prompt.get("properties", {}):
            raise ProviderError("active prompt schema has no supported text field")
        location = data_object(self._request(contract, "location.get", workspace,
                                            trace=trace, deadline=deadline))
        directory = location.get("directory")
        if not isinstance(directory, str) or not directory or (
                require_workspace and Path(directory).resolve() != workspace.resolve()):
            raise ProviderError("OpenCode server default location differs from the task workspace")
        agents_record = self._request(contract, "agent.list", workspace, trace=trace, deadline=deadline)
        models_record = self._request(contract, "model.list", workspace, trace=trace, deadline=deadline)
        agents = agents_record.get("data")
        models = models_record.get("data")
        if not isinstance(agents, list) or not isinstance(models, list):
            raise ProviderError("active OpenCode catalogs have unsupported shapes")
        agents = tuple(item["id"] for item in agents
                       if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"])
        models = tuple({"id": item["id"], "providerID": item["providerID"]} for item in models
                       if isinstance(item, dict) and isinstance(item.get("id"), str)
                       and isinstance(item.get("providerID"), str) and item["id"] and item["providerID"])
        if not agents or not models:
            raise ProviderError("OpenCode has no available agents or provider/model pairs")
        return contract, directory, agents, models

    def _preflight(self, workspace):
        _, _, agents, models = self._discover(workspace, require_workspace=False)
        return self._status(True, True, agents=agents,
                            models=[f"{item['providerID']}/{item['id']}" for item in models])

    def _messages(self, contract, workspace, session_id, trace, deadline):
        parameters = contract.parameters("session.message.list")
        query = {}
        if ("query", "order") in parameters:
            query["order"] = "desc"
        if ("query", "limit") in parameters:
            query["limit"] = "100"
        messages = []
        cursors = set()
        # Bounded pagination; exceeding it is uncertainty, never implicit success.
        for _ in range(20):
            record = self._request(contract, "session.message.list", workspace,
                                   session_id=session_id, query=query, trace=trace, deadline=deadline)
            data = record.get("data")
            if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
                raise ProviderError("unsupported OpenCode message response")
            messages.extend(data)
            cursor = record.get("cursor")
            cursor = cursor.get("next") if isinstance(cursor, dict) else None
            if not cursor:
                return messages
            if not isinstance(cursor, str) or cursor in cursors or ("query", "cursor") not in parameters:
                raise ProviderError("OpenCode message pagination cannot be reconciled")
            cursors.add(cursor)
            query.pop("order", None)
            query["cursor"] = cursor
        raise ProviderError("OpenCode message read exceeded the bounded pagination window")

    def _run(self, task, prompt_text, workspace, trace, started):
        deadline = time.monotonic() + positive_seconds(task.get("timeout_seconds", self.timeout))
        interval = positive_seconds(task.get("poll_interval_seconds", 2))
        contract, directory, agents, models = self._discover(workspace, trace, deadline)
        agent = task.get("agent_id") or self.agent_id
        model_id = task.get("model_id") or self.model_id
        candidates = [item for item in models if model_id in {
            item["id"], f"{item['providerID']}/{item['id']}"}]
        if agent not in agents or len(candidates) != 1:
            raise ProviderError("configure an exact OpenCode agent_id and unambiguous provider/model identifier")
        model = candidates[0]
        body = {"location": {"directory": directory}, "agent": agent, "model": model}
        if "title" in contract.body("session.create").get("properties", {}):
            body["title"] = str(task.get("task_id", "worker"))
        # Validate every outbound body before creating a session.
        contract.validate(body, contract.body("session.create"))
        contract.validate({"text": prompt_text}, contract.body("session.prompt"))
        created = data_object(self._request(contract, "session.create", workspace,
                                           body=body, trace=trace, deadline=deadline))
        session_id = created.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise ProviderError("session creation lacks a runtime reference; reconcile before retrying")
        trace.runtime_ref = session_id
        info = data_object(self._request(contract, "session.get", workspace,
                                        session_id=session_id, trace=trace, deadline=deadline))
        if info.get("id") != session_id or info.get("parentID") is not None:
            raise ProviderError("created worker identity or root ownership cannot be confirmed")
        location = info.get("location", {})
        if not isinstance(location, dict) or location.get("directory") != directory:
            raise ProviderError("created worker location differs from the confirmed workspace")
        if info.get("agent") != agent or not isinstance(info.get("model"), dict) or any(
                info["model"].get(key) != value for key, value in model.items()):
            raise ProviderError("created worker did not confirm the configured agent/model")
        prior_idle = info.get("time", {}).get("idle")
        if prior_idle is not None and (not isinstance(prior_idle, (int, float)) or isinstance(prior_idle, bool)):
            raise ProviderError("initial session idle timestamp is malformed")
        prior_ids = {item.get("id") for item in self._messages(
            contract, workspace, session_id, trace, deadline)}
        self._request(contract, "session.prompt", workspace, body={"text": prompt_text},
                      session_id=session_id, trace=trace, deadline=deadline)
        while time.monotonic() < deadline:
            info = data_object(self._request(contract, "session.get", workspace,
                                            session_id=session_id, trace=trace, deadline=deadline))
            if info.get("id") != session_id:
                raise ProviderError("OpenCode returned another session during result reconciliation")
            location = info.get("location", {})
            if not isinstance(location, dict) or location.get("directory") != directory:
                raise ProviderError("worker location changed during execution")
            idle = info.get("time", {}).get("idle")
            if isinstance(idle, (int, float)) and not isinstance(idle, bool) and (
                    prior_idle is None or isinstance(prior_idle, (int, float)) and idle > prior_idle):
                status = info.get("outcome")
                if isinstance(status, str):
                    trace.runtime_status = status
                if status not in {"succeeded", "failed", "interrupted", "cancelled"}:
                    raise ProviderError("new idle timestamp lacks a supported terminal outcome")
                messages = self._messages(contract, workspace, session_id, trace, deadline)
                assistants = [item for item in messages
                              if isinstance(item.get("id"), str) and item["id"] not in prior_ids
                              and item.get("type") == "assistant"
                              and isinstance(item.get("time"), dict)
                              and isinstance(item["time"].get("created"), (int, float))
                              and isinstance(item["time"].get("completed"), (int, float))
                              and not item.get("error")]
                assistants.sort(key=lambda item: item.get("time", {}).get("created", 0), reverse=True)
                response = ""
                for message in assistants:
                    content = message.get("content")
                    if not isinstance(content, list):
                        continue
                    response = "\n".join(part["text"] for part in content
                                         if isinstance(part, dict) and part.get("type") == "text"
                                         and isinstance(part.get("text"), str))
                    if response:
                        break
                if status == "succeeded" and not response:
                    raise ProviderError("successful runtime outcome lacks a new assistant response")
                return self._result(started, trace, runtime_ref=session_id, runtime_status=status,
                                    execution_outcome=status, response=response,
                                    error=None if status == "succeeded" else f"OpenCode ended with {status}")
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        raise ProviderError("OpenCode wait deadline expired; worker may continue; reconcile before retrying")
