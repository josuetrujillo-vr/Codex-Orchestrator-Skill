#!/usr/bin/env python3
"""Validate provider-neutral schema-4 ledgers without contacting a runtime."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path


PROVIDERS = {"codex", "opencode", "antigravity"}
STATES = {
    "pending", "launching", "running", "awaiting-approval", "outcome-unknown",
    "completed", "verified", "blocked", "failed", "interrupted", "cancelled", "partial",
}
OUTCOMES = {"unknown", "succeeded", "failed", "interrupted", "cancelled"}
EXCEPTIONAL_STATES = {"failed", "blocked", "partial", "interrupted", "cancelled"}
TERMINAL_STATES = EXCEPTIONAL_STATES | {"verified"}
ACTIVE_STATES = {"launching", "running", "awaiting-approval", "outcome-unknown"}
FIELDS = {
    "task_id", "parent_task_id", "depends_on", "provider", "agent_id", "model_id",
    "runtime_ref", "workspace", "write_scopes", "prompt_path", "output_path",
    "criterion", "evidence_paths", "state", "runtime_status", "execution_outcome",
    "created_at", "last_state_at", "notes",
}


class InvalidLedger(ValueError):
    """Invalid ledger content, distinct from an inaccessible ledger file."""


def require(condition, message):
    if not condition:
        raise InvalidLedger(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def string_list(value, label):
    require(isinstance(value, list) and all(nonempty(item) for item in value),
            f"{label} must be a list of non-empty strings")
    require(len(value) == len(set(value)), f"{label} contains duplicates")
    return value


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=object_pairs,
                      parse_constant=reject_constant)


def reject_constant(value):
    raise InvalidLedger(f"invalid JSON constant: {value}")


def resolve_path(value, base, label):
    require(nonempty(value) and not any(ord(char) < 32 for char in value),
            f"{label} must be a non-empty path without control characters")
    path = Path(value)
    # Reject foreign absolute forms rather than silently treating them as local relatives.
    if sys.platform != "win32":
        require(not re.match(r"^[A-Za-z]:", value) and "\\" not in value,
                f"{label} must use paths accessible on this host")
    else:
        require(not path.drive or path.is_absolute(), f"{label} cannot be drive-relative")
    return (path if path.is_absolute() else base / path).resolve()


def workspace_path(value, workspace, label):
    path = resolve_path(value, workspace, label)
    require(path.is_relative_to(workspace), f"{label} escapes workspace")
    return path


def timestamp(value, label):
    require(isinstance(value, str) and
            re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value),
            f"{label} must be UTC YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise InvalidLedger(f"{label} is not a valid timestamp") from exc


def ancestors(graph, label):
    """Iterative topological walk, also safe for ledgers deeper than recursion limits."""
    remaining = {task_id: set(edges) for task_id, edges in graph.items()}
    result = {}
    while remaining:
        ready = [task_id for task_id, edges in remaining.items() if edges <= result.keys()]
        require(ready, f"{label} contains a cycle")
        for task_id in ready:
            edges = remaining.pop(task_id)
            result[task_id] = set(edges)
            for edge in edges:
                result[task_id].update(result[edge])
    return result


def scopes_overlap(first, second):
    return any(a.is_relative_to(b) or b.is_relative_to(a) for a in first for b in second)


def validate(ledger, ledger_dir, closing=False):
    require(isinstance(ledger, dict), "root must be an object")
    require(type(ledger.get("schema_version")) is int and ledger["schema_version"] == 4,
            "schema_version must be 4; use legacy validators for schema 3")
    require(isinstance(ledger.get("run"), dict), "run must be an object")
    limit = ledger["run"].get("max_parallel_tasks", 3)
    require(type(limit) is int and limit > 0, "run.max_parallel_tasks must be a positive integer")
    backend = ledger["run"].get("execution_backend", "direct")
    require(isinstance(backend, str) and backend in {"direct", "herdr"},
            "run.execution_backend must be direct or herdr")
    herdr = ledger["run"].get("herdr", {})
    require(isinstance(herdr, dict) and herdr.keys() <= {"command"},
            "run.herdr must contain only command")
    if "command" in herdr:
        command = herdr["command"]
        require(nonempty(command) or (
                    isinstance(command, list) and command and
                    all(nonempty(item) for item in command)),
                "run.herdr.command must be an executable or non-empty argument list")
    require(isinstance(ledger.get("tasks"), list), "tasks must be a list")
    tasks = {}
    paths = {}
    for index, task in enumerate(ledger["tasks"]):
        label = f"tasks[{index}]"
        require(isinstance(task, dict), f"{label} must be an object")
        require(FIELDS <= task.keys(), f"{label} missing fields: {', '.join(sorted(FIELDS - task.keys()))}")
        task_id = task["task_id"]
        require(isinstance(task_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", task_id),
                f"{label}.task_id is invalid")
        require(task_id not in tasks, f"duplicate task_id: {task_id}")
        tasks[task_id] = task
        label = task_id
        for field, choices in (("provider", PROVIDERS), ("state", STATES),
                               ("execution_outcome", OUTCOMES)):
            require(isinstance(task[field], str) and task[field] in choices,
                    f"{label}.{field} is invalid")
        if closing:
            require(task["state"] in TERMINAL_STATES,
                    f"{label}: closing requires a terminal state; found {task['state']}")
        for field in ("agent_id", "model_id", "runtime_ref", "runtime_status", "parent_task_id"):
            require(task[field] is None or nonempty(task[field]),
                    f"{label}.{field} must be null or a non-empty string")
        if "transport_ref" in task:
            require(task["transport_ref"] is None or nonempty(task["transport_ref"]),
                    f"{label}.transport_ref must be null or a non-empty string")
        require(nonempty(task["criterion"]), f"{label}.criterion must be non-empty")
        require(isinstance(task["notes"], str), f"{label}.notes must be a string")
        created = timestamp(task["created_at"], f"{label}.created_at")
        updated = timestamp(task["last_state_at"], f"{label}.last_state_at")
        require(updated >= created, f"{label}.last_state_at precedes created_at")
        string_list(task["depends_on"], f"{label}.depends_on")
        workspace = resolve_path(task["workspace"], ledger_dir, f"{label}.workspace")
        require(workspace.is_dir(), f"{label}.workspace is not an existing directory")
        scopes = [workspace_path(scope, workspace, f"{label}.write_scopes")
                  for scope in string_list(task["write_scopes"], f"{label}.write_scopes")]
        prompt = workspace_path(task["prompt_path"], workspace, f"{label}.prompt_path")
        require(prompt.is_file(), f"{label}.prompt_path is not an existing file")
        output = None
        if task["output_path"] is not None:
            output = workspace_path(task["output_path"], workspace, f"{label}.output_path")
        evidence = [workspace_path(path, workspace, f"{label}.evidence_paths")
                    for path in string_list(task["evidence_paths"], f"{label}.evidence_paths")]
        paths[task_id] = scopes
        if task["state"] in EXCEPTIONAL_STATES:
            require(nonempty(task["notes"]), f"{label} requires a reason in notes")
        if task["state"] == "verified":
            require(task["execution_outcome"] == "succeeded", f"{label}: verified requires succeeded outcome")
            require(output is not None and output.is_file(), f"{label}: verified requires existing output file")
            require(evidence, f"{label}: verified requires evidence")
            for path in evidence:
                require(path.is_file(), f"{label}: evidence file does not exist: {path}")
                try:
                    record = read_json(path)
                except (OSError, ValueError) as exc:
                    raise InvalidLedger(f"{label}: cannot read JSON evidence {path}: {exc}") from exc
                require(isinstance(record, dict) and record.get("task_id") == task_id and
                        record.get("criterion") == task["criterion"] and
                        record.get("result") == "pass" and nonempty(record.get("observed")),
                        f"{label}: evidence needs matching task_id, exact criterion, pass and non-empty observed")

    graph = {}
    parents = {}
    for task_id, task in tasks.items():
        require(all(dep in tasks for dep in task["depends_on"]), f"{task_id}: missing dependency")
        graph[task_id] = task["depends_on"]
        parent = task["parent_task_id"]
        require(parent is None or parent in tasks, f"{task_id}: missing parent_task_id")
        parents[task_id] = [] if parent is None else [parent]
    order = ancestors(graph, "depends_on")
    ancestors(parents, "parent_task_id")
    task_ids = list(tasks)
    for index, first in enumerate(task_ids):
        for second in task_ids[index + 1:]:
            overlap = scopes_overlap(paths[first], paths[second])
            require(not overlap or first in order[second] or second in order[first],
                    f"conflicting write scopes: {first} and {second} need an ordering dependency")
            require(not overlap or not (tasks[first]["state"] in ACTIVE_STATES and
                                        tasks[second]["state"] in ACTIVE_STATES),
                    f"conflicting active write scopes: {first} and {second}")
    return len(tasks)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    closing = len(argv) > 1 and argv[0] == "validate" and argv[1] == "--closing"
    if closing:
        argv = [argv[0], *argv[2:]]
    if len(argv) != 2 or argv[0] != "validate":
        print("usage: validate_ledger_v4.py validate [--closing] PATH", file=sys.stderr)
        print(json.dumps({"valid": False, "error": "usage"}))
        return 2
    try:
        path = Path(argv[1]).resolve()
        ledger = read_json(path)
    except OSError as exc:
        print(f"ledger environment error: {exc}", file=sys.stderr)
        print(json.dumps({"valid": False, "error": "environment"}))
        return 2
    except (ValueError, RuntimeError) as exc:
        print(f"invalid ledger JSON: {exc}", file=sys.stderr)
        print(json.dumps({"valid": False, "error": "invalid"}))
        return 1
    try:
        count = validate(ledger, path.parent, closing=closing)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"invalid ledger: {exc}", file=sys.stderr)
        print(json.dumps({"valid": False, "schema_version": 4, "error": "invalid"}))
        return 1
    print(json.dumps({"valid": True, "schema_version": 4, "tasks": count}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
