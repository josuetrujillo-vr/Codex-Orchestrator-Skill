# Herdr Core Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Herdr Core (`hc`) as an optional execution backend for the existing Codex, OpenCode, and Antigravity workers.

**Architecture:** Keep provider identity and schema-4 orchestration semantics. `run.execution_backend` selects `direct` (default) or `herdr`; Herdr mode launches each provider's native CLI in a persistent local Core terminal, stores its terminal name in optional `transport_ref`, and retains native provider references in `runtime_ref`. Codex still reviews results and calls `verify-task`.

**Tech Stack:** POSIX `sh`, Python 3.9+ standard library, Herdr Core `hc` CLI, Codex `exec --json`, OpenCode `run --format json`, Antigravity `stream-json`.

**Spec:** `docs/superpowers/specs/2026-10-29-herdr-hc-compatibility-design.md`

## Global Constraints

- Keep schema version 4 and accept old ledgers that omit `transport_ref`.
- An absent `run.execution_backend` means `direct`; Herdr is opt-in.
- Use the public local `hc` CLI only; do not install or configure Herdr, control its UI, or route to remote machines.
- Use argument arrays with `shell=False`; do not interpolate prompts or paths into shell command strings.
- Keep provider authentication and permission policy local; never enable permission bypasses.
- Preserve workspace/write-scope reservations, credential redaction, no automatic retry, and Codex-only semantic verification.
- Do not add or run automated tests unless the user asks.

## Review Focus

- **Herdr missing or unsupported:** preflight reports it unavailable; Herdr-selected work is blocked without changing direct runs. Owner: Tasks 1–2.
- **Launch accepted, then timed out:** persist the terminal reference and reservation as `outcome-unknown`; never dispatch the same task again automatically. Owner: Tasks 2–3.
- **Missing, truncated, or malformed provider JSON:** preserve the Herdr terminal reference and classify the outcome as unknown; do not infer completion from terminal state. Owner: Task 3.
- **Workspace cannot be confirmed locally:** do not launch a write task or silently use a remote/default directory. Owner: Tasks 2–3.
- **Windows paths, Unicode prompts, or CLI shims:** keep argv boundaries intact; report unsupported executables clearly. Owner: Task 2.

---

### Task 1: Extend schema-4 backend configuration

**Files:**
- Modify: `SKILL/scripts/validate_ledger_v4.py`
- Modify: `SKILL/scripts/run_multiruntime.py`
- Modify: `SKILL/references/ledger-template.md`

**Interfaces:**
- `run.execution_backend` accepts `direct` or `herdr`, defaults to `direct` when omitted.
- Optional `run.herdr.command` accepts an executable string or argv prefix; default executable is `hc`.
- Optional task field `transport_ref` accepts a non-empty string or `null`; omission is equivalent to `null`.
- `task_status()` includes `transport_ref` when present.

- [x] **Step 1: Add backend and Herdr command validation**

Validate the backend enum and command shape before provider discovery. Reject unknown backend names and malformed command lists with a concise ledger error.

- [x] **Step 2: Add backward-compatible transport reference handling**

Add `transport_ref` to the optional task-field contract and validate it when present. Leave schema version 4 and all required legacy fields unchanged.

- [x] **Step 3: Document configuration and references**

Update the schema-4 contract/example to show direct default, opt-in Herdr configuration, `runtime_ref` versus `transport_ref`, and reconciliation guidance.

---

### Task 2: Add the Herdr Core CLI transport

**Files:**
- Create: `SKILL/scripts/providers/herdr.py`
- Create: `SKILL/scripts/herdr_worker.py`
- Modify: `SKILL/scripts/providers/__init__.py`
- Modify: `SKILL/scripts/run_multiruntime.py`

**Interfaces:**
- `HerdrTransport(command: list[str], artifact_dir: Path)` owns `hc` discovery and one task's terminal lifecycle.
- `HerdrTransport.preflight(workspace: Path) -> ProviderStatus` checks the configured executable and documented `run --bg --name` support without starting a worker.
- `HerdrTransport.run(task_id: str, terminal_name: str, argv: list[str], workspace: Path, timeout: float) -> HerdrResult` launches the helper in one named terminal, polls its sidecar result within the timeout, and returns raw provider output plus process outcome.
- `HerdrResult` carries `transport_ref`, stdout, stderr, exit code, timeout flag, and whether dispatch may have occurred; it also exposes the `ProcessOutput` fields consumed by existing event parsers.
- `herdr_worker.py` reads one JSON manifest and starts its argv with `subprocess.Popen(..., shell=False)`, streaming stdout/stderr to both task artifacts and the Herdr terminal; it atomically writes `result.json` after process exit.

- [x] **Step 1: Resolve `hc` without shell shims**

Use the configured argv prefix or `hc`, resolve it with the same executable checks as provider adapters, reject `.cmd`, `.bat`, and `.ps1`, and avoid printing environment or credential values.

- [x] **Step 2: Add Herdr preflight**

Read the installed CLI's version/help surfaces and confirm the public `run --bg --name` command needed by the adapter. Return Herdr availability separately from model-provider availability; never start, stop, or replace the Core server.

- [x] **Step 3: Add task-scoped terminal names and lifecycle calls**

Derive a bounded unique terminal name from the ledger run identity and task ID. Persist that name as `transport_ref` before dispatch, then start the Python helper through the documented background `hc run` interface in the task workspace. The coordinator polls the helper's result sidecar; it does not parse terminal rendering. Treat a timeout or uncertain launch as active/unknown and retain its name for reconciliation.

- [x] **Step 4: Keep output machine-readable**

The helper stores raw stdout, stderr, and exit status in task artifacts while streaming them to the Herdr terminal. The transport reads the atomically completed sidecar and returns `ProcessOutput`; on timeout, it returns unknown and leaves the helper running. Redact before writing traces. If the helper result is missing or malformed, preserve the terminal name and return unknown.

---

### Task 3: Route provider adapters through Herdr

**Files:**
- Modify: `SKILL/scripts/providers/base.py`
- Modify: `SKILL/scripts/providers/codex.py`
- Modify: `SKILL/scripts/providers/opencode.py`
- Modify: `SKILL/scripts/providers/antigravity.py`
- Modify: `SKILL/scripts/run_multiruntime.py`

**Interfaces:**
- `ProviderAdapter.__init__(..., herdr_transport: Optional[HerdrTransport] = None)` selects the alternate transport; absent transport preserves existing direct behavior.
- `ProviderAdapter._invoke_herdr(args: list[str], task: dict, workspace: Path, timeout: float) -> HerdrResult` starts the helper using `task['transport_ref']` and returns its raw process output.
- Provider-specific command builders return argv lists and retain current exact model/agent/profile validation.
- `ProviderResult.runtime_ref` remains the provider session/conversation reference; optional `ProviderResult.transport_ref` records the Herdr terminal name separately.

- [x] **Step 1: Add provider command builders for Herdr mode**

Build argv for Codex `exec --json`, OpenCode `run --format json`, and Antigravity `-p --output-format stream-json`. Preserve task workspace, model, profile, agent, timeout, and local permission configuration; pass prompt data without shell parsing.

- [x] **Step 2: Reuse native structured event parsing**

Parse each provider's documented JSON/JSONL event format and require its confirmed terminal success record plus a successful process exit. Record the provider session/conversation ID when it is actually returned.

- [x] **Step 3: Integrate Herdr discovery and scheduling**

In `herdr` mode, preflight `hc` and the selected provider independently before dispatch. Compute and persist `transport_ref` with the `launching` state before calling `hc run`, so a lost acknowledgement is still reconcilable by name. Do not fall back to direct mode if `hc` or a provider route is unavailable.

- [x] **Step 4: Preserve ledger lifecycle and status output**

Map confirmed provider success to `completed`, never `verified`. Map ambiguous submission, timeout, or incomplete event streams to `outcome-unknown` with the Herdr reference retained. Keep dependencies blocked until `verify-task` marks prerequisites verified, and include transport metadata in `status-multiruntime` output.

- [x] **Step 5: Keep direct mode unchanged**

When the backend is absent or `direct`, continue using the current provider adapters, including OpenCode's live OpenAPI session route.

---

### Task 4: Document, package, and review the feature

**Files:**
- Modify: `SKILL/SKILL.md`
- Modify: `SKILL/scripts/prompt-templates/router-orchestrator.md`
- Modify: `SKILL/references/provider-adapters.md`
- Modify: `SKILL/references/ledger-template.md`
- Modify: `README.md`
- Modify: `README.es.md`
- Modify: `CHANGELOG.md`
- Modify: `SKILL/scripts/package.sh`

**Interfaces:**
- The user-facing quick start distinguishes direct default from optional `hc` mode.
- The packaged skill contains the new Herdr transport module.

- [x] **Step 1: Update skill guidance and examples**

Document Herdr Core as an optional execution backend, how to configure its executable path, how provider and transport references differ, and how to reconcile an uncertain terminal. Keep installation and server startup under the user's control.

- [x] **Step 2: Update both READMEs and changelog**

Describe the optional backend and local `hc` prerequisite in English and Spanish; do not claim Herdr is required for ordinary runs.

- [x] **Step 3: Add both modules to the closed package inventory**

Add `scripts/providers/herdr.py` and `scripts/herdr_worker.py` to `PACKAGE_FILES`. Do not add an Herdr installation or dependency step to CI.

- [x] **Step 4: Review the final diff against the spec**

Inspect configuration compatibility, state transitions, shell-free argv handling, package inventory, docs/examples, and the five Review Focus behaviors. Record any runtime limitation if `hc` remains unavailable in the Codex process.

---

## Handoff

The design is approved. Before implementing, review this plan and select the execution method. The provider, schema, and runner changes share interfaces, so native execution in this session is the recommended path; subagent-driven implementation is available if you prefer independent task reviews.
