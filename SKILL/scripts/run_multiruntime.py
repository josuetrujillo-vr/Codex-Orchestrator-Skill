#!/usr/bin/env python3
"""Run schema-4 DAG tasks; provider execution never implies semantic verification."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from providers import AntigravityAdapter, CodexAdapter, OpenCodeAdapter, ProviderStatus
from providers import HerdrTransport
from providers.base import Redactor, utc_now
from validate_ledger_v4 import (
    ACTIVE_STATES, EXCEPTIONAL_STATES, TERMINAL_STATES, InvalidLedger, nonempty,
    read_json, require, resolve_path, scopes_overlap, validate, workspace_path,
)


ADAPTERS = {"codex": CodexAdapter, "opencode": OpenCodeAdapter,
            "antigravity": AntigravityAdapter}
OPTIONS = {"command", "timeout", "model_id", "agent_id", "profile"}
REDACTOR = Redactor()


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes arbitrary argument values into its errors.
        self.print_usage(sys.stderr)
        emit({"error": "usage", "detail": "Invalid arguments; see --help."})
        self.exit(2)


def emit(record):
    print(json.dumps(REDACTOR.value(record), ensure_ascii=False))


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_json(path, record):
    atomic_text(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def ledger_lock(path):
    # Fail closed on a stale marker: a crashed dispatch must be reconciled first.
    marker = path.with_name(path.name + ".lock")
    try:
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise InvalidLedger("ledger is locked; reconcile any interrupted runner before removing its .lock marker") from None
    try:
        os.close(descriptor)
        yield
    finally:
        marker.unlink()


def load(path):
    ledger = read_json(path)
    validate(ledger, path.parent)
    return ledger


def save(path, ledger, closing=False):
    require(REDACTOR.value(ledger) == ledger,
            "ledger contains credential material; remove it before mutation")
    validate(ledger, path.parent, closing=closing)
    atomic_json(path, ledger)


def execution_config(ledger):
    run = ledger.get("run", {})
    backend = run.get("execution_backend", "direct")
    herdr = run.get("herdr", {})
    command = herdr.get("command", "hc")
    return backend, [command] if isinstance(command, str) else list(command)


def provider_options(ledger):
    configurations = ledger.get("run", {}).get("providers", {})
    require(isinstance(configurations, dict) and configurations.keys() <= ADAPTERS.keys(),
            "run.providers must map known provider names to adapter options")
    result = {}
    for provider, options in configurations.items():
        require(isinstance(options, dict) and options.keys() <= OPTIONS,
                f"{provider}: unsupported adapter configuration")
        if "command" in options:
            command = options["command"]
            require(nonempty(command) or (isinstance(command, list) and command and
                                         all(nonempty(item) for item in command)),
                    f"{provider}.command must be an executable or argument list")
        for name in ("model_id", "agent_id", "profile"):
            if name in options:
                require(options[name] is None or nonempty(options[name]),
                        f"{provider}.{name} must be null or a non-empty identifier")
        result[provider] = options
    return result


def discover(options, providers, root=None, herdr_transport=None):
    records = {}
    for provider in providers:
        try:
            adapter = ADAPTERS[provider](herdr_transport=herdr_transport,
                                         **options.get(provider, {}))
            if root is None:
                records[provider] = adapter.preflight()
            else:
                # The public CLI preflight uses cwd; dispatch discovery must not chdir
                # the process while other workspace workers may be running.
                adapter._command()
                records[provider] = adapter._preflight(root)
        except Exception:
            # Adapter failures are isolated; never serialize exception payloads.
            records[provider] = ProviderStatus(provider, False, False,
                                               reason="provider discovery failed")
    return records


def workspace(task, ledger_path):
    return resolve_path(task["workspace"], ledger_path.parent, "workspace")


def artifacts(task, ledger_path):
    run_id = hashlib.sha256(str(ledger_path).encode("utf-8")).hexdigest()[:16]
    return workspace_path(f".orchestrator/{run_id}/{task['task_id']}",
                          workspace(task, ledger_path), "artifact directory")


def reserved_paths(ledger, ledger_path):
    paths = {ledger_path, ledger_path.with_name(ledger_path.name + ".lock")}
    for task in ledger["tasks"]:
        root = workspace(task, ledger_path)
        paths.add(workspace_path(task["prompt_path"], root, "prompt_path"))
    return paths


def artifact_paths(task, ledger_path):
    root = workspace(task, ledger_path)
    values = [task[key] for key in ("output_path", "response_path", "trace_path")
              if task.get(key) is not None]
    return [workspace_path(item, root, "artifact")
            for item in [*values, *task["evidence_paths"]]]


def task_artifact_reservations(task, ledger_path):
    # The directory is used for the runner's response and provider trace files,
    # including paths that are not known until a provider returns.
    return [artifacts(task, ledger_path), *artifact_paths(task, ledger_path)]


def reservations(task, ledger_path):
    root = workspace(task, ledger_path)
    prompt = workspace_path(task["prompt_path"], root, "prompt_path")
    return ([prompt] +
            [workspace_path(item, root, "write scope") for item in task["write_scopes"]] +
            task_artifact_reservations(task, ledger_path))


def writable_artifact(path, ledger, ledger_path, owner, check_active=True):
    path = path.resolve()
    require(not scopes_overlap([path], reserved_paths(ledger, ledger_path)),
            "artifact path aliases the ledger, lock, or a prompt")
    for task in ledger["tasks"]:
        if task["task_id"] == owner:
            continue
        require(not scopes_overlap([path], task_artifact_reservations(task, ledger_path)),
                "artifact path aliases another task's output, evidence, response or trace")
        if check_active and task["state"] in ACTIVE_STATES:
            require(not scopes_overlap([path], reservations(task, ledger_path)),
                    "artifact path conflicts with an active task's reservation")
    require(not path.is_dir(), "artifact path is a directory")


def transition(task, state, note=None):
    task["state"] = state
    task["last_state_at"] = max(task["last_state_at"], utc_now())
    if note:
        task["notes"] = REDACTOR.text(note)


def task_status(task):
    keys = ("task_id", "provider", "state", "runtime_ref", "transport_ref", "runtime_status",
            "execution_outcome", "output_path", "response_path", "trace_path",
            "started_at", "finished_at", "last_state_at", "notes")
    return {key: task[key] for key in keys if key in task}


def worker_prompt(task, prompt, root):
    boundary = {"task_id": task["task_id"], "workspace": str(root),
                "write_scopes": task["write_scopes"], "output_path": task["output_path"],
                "criterion": task["criterion"]}
    return ("Assignment constraints: " + json.dumps(boundary, ensure_ascii=False) +
            "\nOnly edit files within the declared write_scopes. An empty scope means "
            "read-only work. Return your result and evidence for Codex to review.\n\n" + prompt)


def summary(ledger, **fields):
    return {"schema_version": 4, **fields,
            "counts": dict(Counter(task["state"] for task in ledger["tasks"])),
            "tasks": [task_status(task) for task in ledger["tasks"]]}


def reconcile(task, result, ledger, ledger_path):
    for key in ("runtime_ref", "runtime_status", "execution_outcome", "started_at",
                "finished_at", "trace_path"):
        task[key] = REDACTOR.value(getattr(result, key))
    if getattr(result, "transport_ref", None):
        task["transport_ref"] = REDACTOR.value(result.transport_ref)
    state = {"succeeded": "completed", "failed": "failed", "interrupted": "interrupted",
             "cancelled": "cancelled", "unknown": "outcome-unknown"}[task["execution_outcome"]]
    if state == "outcome-unknown" and result.runtime_status == "awaiting-approval":
        state = "awaiting-approval"
    note = result.error
    if state in EXCEPTIONAL_STATES or state in {"outcome-unknown", "awaiting-approval"}:
        note = note or "Runtime did not confirm successful completion; inspect its trace before proceeding."
    try:
        response = artifacts(task, ledger_path) / "response.txt"
        response = workspace_path(str(response), workspace(task, ledger_path), "response_path")
        writable_artifact(response, ledger, ledger_path, task["task_id"])
        atomic_text(response, REDACTOR.text(result.response))
        task["response_path"] = str(response)
        if task["output_path"] is None:
            task["output_path"] = str(response)
    except (OSError, ValueError, RuntimeError):
        note = "Could not persist the response artifact; inspect the runtime trace."
        if state == "completed":
            state = "partial"
    transition(task, state, note)


def run(ledger_path, parallel):
    with ledger_lock(ledger_path):
        ledger = load(ledger_path)
        options = provider_options(ledger)
        backend, herdr_command = execution_config(ledger)
        limit = parallel if parallel is not None else ledger["run"].get("max_parallel_tasks", 3)
        tasks = {task["task_id"]: task for task in ledger["tasks"]}
        run_id = hashlib.sha256(str(ledger_path).encode("utf-8")).hexdigest()[:16]
        # No runtime resume/retry contract exists. Durable in-flight work retains its scope.
        for task in tasks.values():
            if task["state"] in {"launching", "running"} or (
                    task["state"] == "pending" and (
                        task["runtime_ref"] is not None or task.get("transport_ref"))):
                task["execution_outcome"] = "unknown"
                transition(task, "outcome-unknown", "Previous dispatch requires reconciliation; automatic retry is disabled.")
        save(ledger_path, ledger)
        statuses = {}
        futures = {}
        with ThreadPoolExecutor(max_workers=limit) as executor:
            while True:
                changed = False
                for task in tasks.values():
                    if task["state"] != "pending":
                        continue
                    deps = [tasks[key] for key in task["depends_on"]]
                    if any(dep["state"] in EXCEPTIONAL_STATES for dep in deps):
                        transition(task, "blocked", "A required dependency ended without verification.")
                        changed = True
                        continue
                    root = workspace(task, ledger_path)
                    provider = task["provider"]
                    discovery_key = (provider, root)
                    herdr_transport = (HerdrTransport(herdr_command)
                                       if backend == "herdr" else None)
                    if discovery_key not in statuses:
                        statuses[discovery_key] = discover(
                            options, [provider], root,
                            herdr_transport=herdr_transport)[provider]
                    status = statuses[discovery_key]
                    herdr_status = None
                    if backend == "herdr":
                        herdr_key = ("herdr", root)
                        if herdr_key not in statuses:
                            statuses[herdr_key] = HerdrTransport(herdr_command).preflight(root)
                        herdr_status = statuses[herdr_key]
                    if herdr_status is not None and not herdr_status.available:
                        transition(task, "blocked", herdr_status.reason or
                                   "Required Herdr Core CLI is unavailable.")
                        changed = True
                        continue
                    if not status.available:
                        transition(task, "blocked", status.reason or "Required provider is unavailable.")
                        changed = True
                        continue
                    active = [other for other in tasks.values() if other["state"] in ACTIVE_STATES]
                    if len(active) >= limit or any(dep["state"] != "verified" for dep in deps):
                        continue
                    if any(scopes_overlap(reservations(task, ledger_path),
                                          reservations(other, ledger_path))
                           for other in active):
                        continue
                    try:
                        prompt = workspace_path(task["prompt_path"], root, "prompt_path").read_text(encoding="utf-8-sig")
                        require(REDACTOR.text(prompt) == prompt,
                                "prompt contains credential material; remove it before dispatch")
                        prompt = worker_prompt(task, prompt, root)
                        artifact_dir = artifacts(task, ledger_path)
                        response = artifact_dir / "response.txt"
                        writable_artifact(response, ledger, ledger_path, task["task_id"],
                                          check_active=False)
                        if task["output_path"] is not None:
                            output = workspace_path(task["output_path"], root, "output_path")
                            writable_artifact(output, ledger, ledger_path, task["task_id"],
                                              check_active=False)
                            own_runner_paths = [artifacts(task, ledger_path), response]
                            own_runner_paths.extend(
                                workspace_path(task[key], root, "artifact")
                                for key in ("response_path", "trace_path") if task.get(key))
                            own_runner_paths.extend(
                                workspace_path(item, root, "evidence_path")
                                for item in task["evidence_paths"])
                            require(not scopes_overlap([output], own_runner_paths),
                                    "output path conflicts with this task's runner artifacts")
                        herdr_transport = (HerdrTransport(herdr_command, artifact_dir)
                                           if backend == "herdr" else None)
                        adapter = ADAPTERS[task["provider"]](
                            artifact_dir=artifact_dir, herdr_transport=herdr_transport,
                            **options.get(task["provider"], {}))
                    except (OSError, ValueError, RuntimeError):
                        transition(task, "blocked", "Task inputs or adapter configuration are unusable.")
                        changed = True
                        continue
                    if backend == "herdr":
                        task["transport_ref"] = HerdrTransport.terminal_name(
                            run_id, task["task_id"], task["created_at"])
                    transition(task, "launching")
                    save(ledger_path, ledger)  # Durable write-ahead MUST precede submit.
                    try:
                        future = executor.submit(adapter.run, dict(task), prompt, root)
                    except Exception:
                        transition(task, "outcome-unknown", "Dispatch submission was not confirmed; reconcile before retrying.")
                    else:
                        futures[future] = task
                        transition(task, "running")
                    save(ledger_path, ledger)
                    changed = True
                if changed:
                    save(ledger_path, ledger)
                if not futures:
                    # Revisit after blocked propagation, but never spin on verification waits.
                    if changed:
                        continue
                    break
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    task = futures.pop(future)
                    try:
                        result = future.result()
                    except Exception:
                        task["execution_outcome"] = "unknown"
                        transition(task, "outcome-unknown", "Adapter failed after submission; reconcile runtime effects before retrying.")
                    else:
                        reconcile(task, result, ledger, ledger_path)
                    save(ledger_path, ledger)
        closed = all(task["state"] in TERMINAL_STATES for task in tasks.values())
        save(ledger_path, ledger, closing=closed)
        return summary(ledger, closed=closed, max_parallel_tasks=limit)


def verify_task(ledger_path, task_id, observed):
    require(nonempty(observed), "observed must be non-empty")
    require(REDACTOR.text(observed) == observed, "observation contains credential material")
    with ledger_lock(ledger_path):
        ledger = load(ledger_path)
        require(REDACTOR.value(ledger) == ledger,
                "ledger contains credential material; remove it before mutation")
        task = next((item for item in ledger["tasks"] if item["task_id"] == task_id), None)
        require(task is not None, "task_id was not found")
        require(task["state"] == "completed", "verify-task requires a completed task")
        require(task["execution_outcome"] == "succeeded", "verify-task requires succeeded execution")
        root = workspace(task, ledger_path)
        require(task["output_path"] is not None, "verify-task requires an output path")
        output = workspace_path(task["output_path"], root, "output_path")
        require(output.is_file(), "verify-task requires an existing output file")
        writable_artifact(output, ledger, ledger_path, task_id)
        if not task["evidence_paths"]:
            task["evidence_paths"] = [str(artifacts(task, ledger_path) / "evidence.json")]
        evidence = [workspace_path(item, root, "evidence_path") for item in task["evidence_paths"]]
        require(len(set(evidence)) == len(evidence), "evidence paths alias one another")
        for path in evidence:
            require(path != output, "evidence cannot overwrite output")
            require(all(path != workspace_path(task[key], root, key)
                        for key in ("response_path", "trace_path") if task.get(key)),
                    "evidence cannot overwrite response or trace")
            writable_artifact(path, ledger, ledger_path, task_id)
        record = {"task_id": task_id, "criterion": task["criterion"], "result": "pass",
                  "observed": observed}
        for path in evidence:
            atomic_json(path, record)
        transition(task, "verified")
        save(ledger_path, ledger)
        return {"schema_version": 4, "task": task_status(task),
                "evidence_paths": task["evidence_paths"]}


def positive_integer(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv=None):
    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--ledger")
    for name in ("run", "status", "verify-task"):
        command = commands.add_parser(name)
        command.add_argument("--ledger", required=True)
        if name == "run":
            command.add_argument("--parallel", type=positive_integer)
        if name == "verify-task":
            command.add_argument("--task-id", required=True)
            command.add_argument("--observed", required=True)
    args = parser.parse_args(argv)
    try:
        ledger_path = Path(args.ledger).resolve() if args.ledger else None
        if args.command == "preflight":
            ledger = load(ledger_path) if ledger_path else None
            options = provider_options(ledger) if ledger is not None else {}
            herdr_transport = None
            if ledger is not None:
                backend, command = execution_config(ledger)
                if backend == "herdr":
                    herdr_transport = HerdrTransport(command)
            for record in discover(options, ADAPTERS,
                                   herdr_transport=herdr_transport).values():
                emit({**asdict(record), "available": record.available})
            if ledger is not None:
                if backend == "herdr":
                    record = HerdrTransport(command).preflight(Path.cwd())
                    emit({**asdict(record), "available": record.available})
        elif args.command == "status":
            emit(summary(load(ledger_path)))
        elif args.command == "run":
            emit(run(ledger_path, args.parallel))
        else:
            emit(verify_task(ledger_path, args.task_id, args.observed))
        return 0
    except (OSError, ValueError, RuntimeError):
        # Raw exceptions may contain user/provider-controlled credential values.
        emit({"error": "runner failure", "detail": "Review ledger structure, paths, configuration and lock state."})
        return 1


if __name__ == "__main__":
    sys.exit(main())
