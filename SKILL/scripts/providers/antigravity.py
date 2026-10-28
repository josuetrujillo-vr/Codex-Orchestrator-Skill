"""Antigravity print-mode worker using the documented stream-json envelope."""

from .base import ProviderAdapter, ProviderError, json_events, json_value, positive_seconds


class AntigravityAdapter(ProviderAdapter):
    provider = "antigravity"

    def _command(self):
        if not self.command:
            # The provider name and installed CLI command intentionally differ.
            self.command = ["agy"]
        return super()._command()

    def _catalog(self, name, workspace, trace=None):
        output = self._invoke(["--output-format", "json", name], workspace,
                              timeout=20, stdin="", trace=trace)
        if output.timed_out or output.returncode != 0:
            raise ProviderError(f"Antigravity {name} catalog unavailable; configure CLI authentication locally")
        record = json_value(output.stdout)
        items = record
        if isinstance(record, dict):
            if record.get("status", "SUCCESS") != "SUCCESS":
                raise ProviderError(f"Antigravity {name} catalog reported an error")
            command = record.get("command", {})
            data = command.get("data", {}) if isinstance(command, dict) else {}
            items = record.get(name, record.get("data"))
            if not isinstance(items, list) and isinstance(data, dict):
                items = data.get(name)
        if not isinstance(items, list):
            raise ProviderError(f"Antigravity {name} structured catalog unsupported")
        ids = tuple(item.get("id") or item.get("name") for item in items
                    if isinstance(item, dict) and
                    isinstance(item.get("id") or item.get("name"), str)
                    and (item.get("id") or item.get("name")))
        if not ids:
            raise ProviderError(f"Antigravity {name} catalog contains no usable identifiers")
        return ids

    def _preflight(self, workspace):
        agents = self._catalog("agents", workspace)
        models = self._catalog("models", workspace)
        return self._status(True, True, agents=agents, models=models)

    def _run(self, task, prompt_text, workspace, trace, started):
        model = task.get("model_id") or self.model_id
        agent = task.get("agent_id") or self.agent_id
        # Discover in the task workspace, whose local CLI config can differ.
        agents = self._catalog("agents", workspace, trace)
        models = self._catalog("models", workspace, trace)
        if model not in models or agent not in agents:
            raise ProviderError("configure exact Antigravity model_id and agent_id from its catalogs")
        timeout = positive_seconds(task.get("timeout_seconds", self.timeout))
        args = ["-p", prompt_text, "--output-format", "stream-json",
                "--print-timeout", f"{timeout:g}s", "--model", model, "--agent", agent]
        trace.dispatched = True
        output = self._invoke(args, workspace, timeout=timeout + 5, stdin="", trace=trace)
        events, malformed = json_events(output.stdout, "event")
        runtime_ref = None
        runtime_status = None
        result = None
        for event in events:
            reference = event.get("conversation_id")
            if event["event"] == "result":
                if result is not None or not isinstance(event.get("result"), dict):
                    malformed = True
                    continue
                result = event["result"]
                reference = result.get("conversation_id") or reference
            if reference:
                if not isinstance(reference, str) or (runtime_ref and runtime_ref != reference):
                    malformed = True
                else:
                    runtime_ref = reference
        response = ""
        error = None
        outcome = "unknown"
        if result:
            runtime_status = result.get("status")
            response = result.get("response", "")
            error = result.get("error")
            if not isinstance(runtime_status, str) or not isinstance(response, str):
                malformed = True
                runtime_status = runtime_status if isinstance(runtime_status, str) else None
                response = response if isinstance(response, str) else ""
        if output.timed_out:
            error = "CLI timeout; conversation outcome is unknown; reconcile before retrying"
        elif malformed:
            error = "malformed or inconsistent Antigravity stream-json output"
        elif runtime_status == "SUCCESS":
            if output.returncode == 0 and runtime_ref and not error:
                outcome = "succeeded"
            else:
                error = error or "SUCCESS conflicts with CLI exit status or lacks conversation_id"
        elif runtime_status in {"ERROR", "INVALID"}:
            outcome = "failed"
            error = error or f"Antigravity ended with {runtime_status}"
        elif runtime_status == "CANCELED":
            outcome = "cancelled"
        elif runtime_status == "INTERRUPTED":
            outcome = "interrupted"
        else:
            error = error or "no confirmed terminal result; conversation may be waiting or running"
        return self._result(started, trace, runtime_ref=runtime_ref,
                            runtime_status=runtime_status, execution_outcome=outcome,
                            response=response, error=error)
