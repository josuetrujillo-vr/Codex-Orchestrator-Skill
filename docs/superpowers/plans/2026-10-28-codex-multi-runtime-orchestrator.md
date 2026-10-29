# Codex-Led Multi-Runtime Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the active Codex conversation the root coordinator for distinct, concurrent tasks routed to whichever Codex, OpenCode, and Antigravity workers are configured.

**Architecture:** Preserve the current OpenCode-only commands and schema-3 YAML validator as a legacy path. Add a JSON schema-4 task ledger, standard-library Python adapters and DAG runner for Codex CLI, OpenCode CLI/API, and Antigravity CLI; let the Codex skill plan and integrate the work.

**Tech Stack:** POSIX `sh`, `awk`, Python 3 standard library, `codex exec --json`, `opencode api` against its active OpenAPI schema, and `agy -p` JSON/NDJSON output.

**Spec:** `docs/superpowers/specs/2026-10-28-codex-multi-runtime-orchestrator-design.md`

## Global Constraints

- Use only local provider CLIs and their documented interfaces; do not add a hosted orchestrator, persistent MCP service, or undocumented UI automation.
- Use any configured subset of Codex, OpenCode, and Antigravity; an unavailable provider must not block independent tasks assigned to other providers.
- Keep the current schema-3 OpenCode ledger and validators as the legacy route; new multi-runtime runs use schema 4 JSON.
- Do not store credentials in prompts, ledgers, logs, or result artifacts.
- Confirm the workspace for every task that can write; constrain assignments to non-overlapping scopes or serialize them through dependencies.
- Keep provider-specific status distinct from the normalized task outcome; a timeout with unknown delivery must not be retried automatically or release its scope.
- Keep the package compatible with Linux, macOS, Windows Git Bash, and WSL, using Python's standard library only for new runtime code.
- Keep provider permissions at their configured policies; never pass Antigravity's permission-bypass option by default.

## File Map

- `SKILL/scripts/validate_ledger_v4.py` — validates the generic JSON task ledger and closing evidence.
- `SKILL/scripts/providers/base.py` — common task/result/capability records and adapter interface.
- `SKILL/scripts/providers/codex.py` — Codex CLI preflight, invocation, event parsing, and result capture.
- `SKILL/scripts/providers/opencode.py` — OpenCode CLI/API preflight, session invocation, bounded wait, and result capture.
- `SKILL/scripts/providers/antigravity.py` — Antigravity CLI preflight, invocation, NDJSON parsing, and result capture.
- `SKILL/scripts/run_multiruntime.py` — topological scheduler, concurrency limit, ledger state transitions, and run summary.
- `SKILL/scripts/orchestrate.sh` — expose provider discovery, schema-4 validation, and multi-runtime execution while preserving legacy commands.
- `SKILL/SKILL.md` and `SKILL/scripts/prompt-templates/router-orchestrator.md` — Codex-root workflow, task decomposition, provider choice, and result integration.
- `SKILL/references/ledger-template.md` — schema-4 JSON contract, lifecycle, scopes, and evidence; retain the schema-3 contract as legacy.
- `SKILL/references/provider-adapters.md` — provider capability map, invocation modes, result contract, and unavailable-provider behavior.
- `README.md`, `README.es.md`, `SKILL/references/triggers-es.md`, `SKILL/references/trigger-tests.md` — Codex-first identity, triggers, setup, and usage.
- `SKILL/scripts/package.sh`, `.github/workflows/ci.yml`, and `CHANGELOG.md` — distributable name, closed file inventory, package references, and release notes.

## Review Focus

- **Provider missing or unauthenticated:** discovery reports it as unavailable and the scheduler still runs independent tasks on available providers. Owner: Task 3.
- **Malformed, truncated, or terminal-status-free CLI output:** record a provider failure or unknown outcome; never mark it verified. Owner: Task 2.
- **Timeout after a send may have succeeded:** keep the task and its scope reserved as `outcome-unknown`; do not automatically retry. Owner: Task 3.
- **Dependency failure or overlapping write scopes:** do not start a dependent task or two conflicting writers together. Owner: Tasks 1 and 3.
- **Windows paths, spaces, Unicode, or secrets in command output:** preserve paths and text, and redact credentials from saved diagnostics. Owner: Task 2.

---

### Task 1: Define and validate the provider-neutral ledger

**Files:**
- Create: `SKILL/scripts/validate_ledger_v4.py`
- Modify: `SKILL/references/ledger-template.md`
- Modify: `SKILL/scripts/orchestrate.sh`

**Interfaces:**
- `validate_ledger_v4.py validate [--closing] PATH` reads schema-4 JSON and returns `0` for valid, `1` for invalid, `2` for usage/environment errors; it emits a concise machine-readable summary to stdout and diagnostics to stderr. Default validation accepts in-progress ledgers; --closing requires a settled run.
- Schema 4 root fields: `schema_version: 4`, `run`, and `tasks`.
- Each task has `task_id`, `parent_task_id`, `depends_on`, `provider`, nullable `agent_id`, nullable `model_id`, nullable `runtime_ref`, `workspace`, `write_scopes`, `prompt_path`, nullable `output_path`, `criterion`, `evidence_paths`, `state`, nullable `runtime_status`, `execution_outcome`, timestamps, and `notes`.
- Provider values are `codex`, `opencode`, and `antigravity`. Native runtime parent/session fields are optional metadata; `parent_task_id` and `depends_on` are the logical DAG.
- `state` values are `pending`, `launching`, `running`, `awaiting-approval`, `outcome-unknown`, `completed`, `verified`, `blocked`, `failed`, `interrupted`, `cancelled`, and `partial`; `execution_outcome` values are `unknown`, `succeeded`, `failed`, `interrupted`, and `cancelled`.
- A runtime success becomes `completed`, not `verified`. Codex reviews the response against `criterion`, then calls `verify-task`; that command writes evidence with `task_id`, exact `criterion`, `result: "pass"`, and non-empty `observed`, and transitions the task to `verified`.
- The existing schema-3 YAML validators remain unchanged and continue to validate old OpenCode runs.

- [x] **Step 1: Specify the schema-4 JSON example and field contract** in `ledger-template.md`. Include a run with independent tasks on different providers, a dependency, a missing optional provider, and the allowed lifecycle values. State that provider-specific IDs are recorded only after the runtime returns them.
- [x] **Step 2: Implement schema-4 validation** in `validate_ledger_v4.py` using `json`, `pathlib`, and other standard-library modules. Reject duplicate task IDs, unknown providers/states, missing dependencies, dependency cycles, invalid paths, an invalid execution outcome, and conflicting scopes without an ordering dependency. Keep schema-3 handling in its existing validator path.
- [x] **Step 3: Implement the closing gate** in the same validator. Normal validation permits in-progress states; a --closing mode rejects pending, launching, running, awaiting-approval, outcome-unknown, and completed tasks. Require successful outcomes, existing output/evidence paths, and criterion-matching evidence for verified tasks; allow documented failed, blocked, partial, interrupted, or cancelled tasks only with a non-empty reason. Never infer verification from process exit alone.
- [x] **Step 4: Add the `validate-ledger-v4` command route** in `orchestrate.sh`. Accept [--closing] PATH, pass arguments without shell re-parsing, and preserve the validator's exit code.
- [x] **Step 5: Review the schema examples against the contract.** Confirm a schema-3 ledger still uses the old commands and that a schema-4 ledger can represent a run where one provider is unavailable and unrelated tasks continue.

---

### Task 2: Implement the provider adapters

**Files:**
- Create: `SKILL/scripts/providers/__init__.py`
- Create: `SKILL/scripts/providers/base.py`
- Create: `SKILL/scripts/providers/codex.py`
- Create: `SKILL/scripts/providers/opencode.py`
- Create: `SKILL/scripts/providers/antigravity.py`

**Interfaces:**
- `ProviderResult` contains `provider`, nullable `runtime_ref`, nullable `runtime_status`, `execution_outcome`, `response`, nullable `error`, `started_at`, `finished_at`, and nullable `trace_path`.
- `ProviderAdapter` exposes `preflight() -> ProviderStatus` and `run(task: dict, prompt_text: str, workspace: Path) -> ProviderResult`.
- `ProviderStatus` contains provider name, installed/configured state, discovered agent/model identifiers when supported, and a reason when unavailable. It must not contain credentials.
- Each adapter runs synchronously; Task 3 owns concurrency and ledger mutation.

- [x] **Step 1: Define the shared adapter types and status values** in `base.py`. Keep required capabilities explicit and allow a provider to report `cancel` or `resume` as unsupported instead of fabricating them.
- [x] **Step 2: Implement Codex preflight and invocation** in `codex.py`. Use the installed `codex` CLI, `exec --json`, the task workspace, and the configured model/profile. Parse JSONL events and final response; return a failure or unknown result if the stream is malformed or lacks a confirmed terminal result. Never parse the terminal's human-readable rendering.
- [x] **Step 3: Implement Antigravity preflight and invocation** in `antigravity.py`. Use `agy -p` with `--output-format stream-json`, an explicit timeout, and configured `--model`/`--agent` values. Extract the final `result` event and `conversation_id`; map non-success terminal states without marking the task verified. Do not add permission-bypass flags.
- [x] **Step 4: Implement OpenCode preflight and invocation** in `opencode.py`. Call the installed `opencode api` CLI so it uses the user's configured endpoint/authentication. Discover routes and payloads from the active `/openapi.json`; create a worker session, send the prompt, use bounded wait/read, and capture the final assistant result. Keep the old shell API path intact for schema-3 legacy runs.
- [x] **Step 5: Normalize provider results** through `ProviderResult`. Retain raw structured event traces under the run artifact directory, redact secrets, and keep the literal runtime status separate from the normalized outcome.
- [x] **Step 6: Review adapter behavior for partial setup and malformed output.** Confirm each adapter returns a structured unavailable/failure result rather than aborting the whole run, and that paths with spaces and non-ASCII text survive command invocation.

---

### Task 3: Add the multi-runtime DAG runner

**Files:**
- Create: `SKILL/scripts/run_multiruntime.py`
- Modify: `SKILL/scripts/orchestrate.sh`
- Modify: `SKILL/scripts/validate_ledger_v4.py`

**Interfaces:**
- `run_multiruntime.py preflight` prints one JSON capability record per provider and exits non-zero only for an invalid invocation or runner failure; individual unavailable providers remain records, not fatal errors.
- `run_multiruntime.py run --ledger PATH [--parallel N]` validates schema 4, executes ready tasks, updates the ledger atomically, and emits one JSON run summary. After all tasks settle, it performs closing validation; while work remains active, it uses normal validation.
- `run_multiruntime.py status --ledger PATH` emits the current task states and runtime references as JSON.
- `run_multiruntime.py verify-task --ledger PATH --task-id ID --observed TEXT` accepts only a `completed` task, writes its evidence file, and moves it to `verified` after validating the criterion and artifact paths.
- Default `max_parallel_tasks` is `3`; a positive `--parallel` value overrides it for a run. The scheduler counts `running`, `awaiting-approval`, and `outcome-unknown` tasks against the limit.

- [x] **Step 1: Implement provider discovery** using the three adapters. Report each provider as available/unavailable independently; do not place environment variables or auth material in JSON output.
- [x] **Step 2: Implement the scheduler** with `concurrent.futures.ThreadPoolExecutor`. Before dispatch, persist `launching`; launch only tasks whose dependencies are `verified`; mark tasks blocked when a required dependency fails or the specific provider is unavailable; continue unrelated branches.
- [x] **Step 3: Implement state reconciliation and atomic ledger updates.** Record returned runtime references, literal runtime status, normalized outcome, timestamps, output, and error notes. Runtime success stops at `completed`; semantic verification remains with Codex. On ambiguous timeout, record `outcome-unknown`, retain the scope, and do not retry.
- [x] **Step 4: Implement `verify-task`.** Require a completed task, exact criterion, non-empty observation, and valid output/evidence paths; write the evidence record and transition to `verified` so dependent tasks can become eligible.
- [x] **Step 5: Add `preflight-providers`, `run-multiruntime`, `status-multiruntime`, and `verify-task` routes** in `orchestrate.sh`. Keep current OpenCode-only command names and behavior available for schema-3 ledgers.
- [x] **Step 6: Review a mixed provider run by tracing each state transition.** Confirm that independent tasks can run concurrently, dependencies wait for `verified`, one provider's launch failure does not cancel other branches, and conflicting scopes never run together.

---

### Task 4: Make the skill Codex-first and update packaging

**Files:**
- Modify: `SKILL/SKILL.md`
- Modify: `SKILL/scripts/prompt-templates/router-orchestrator.md`
- Create: `SKILL/references/provider-adapters.md`
- Modify: `SKILL/references/ledger-template.md`
- Modify: `SKILL/references/triggers-es.md`
- Modify: `SKILL/references/trigger-tests.md`
- Modify: `README.md`
- Modify: `README.es.md`
- Modify: `.gitignore`
- Modify: `SKILL/scripts/package.sh`
- Modify: `.github/workflows/ci.yml`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Skill/package name: `codex-multi-runtime-orchestrator`.
- The skill workflow treats the current Codex conversation as root and its three local adapters as workers. Codex plans distinct tasks and integrates results; the default is not to broadcast a duplicate prompt.
- The new ledger and commands use schema 4; existing OpenCode-only runs and schema-3 validators remain documented as a legacy path.

- [x] **Step 1: Rewrite the skill's activation, quick-start, and core workflow** in `SKILL/SKILL.md` around Codex as root, provider discovery, distinct task decomposition, partial provider availability, concurrent dispatch, and result integration. Move OpenCode-specific identity/subagent requirements to the legacy route instead of applying them to all providers.
- [x] **Step 2: Update the router prompt** so it plans a DAG with task-specific criteria, required capabilities, dependencies, and write scopes; assigns each task to an available configured agent; inspects each completed result; and invokes `verify-task` only when the criterion is met.
- [x] **Step 3: Document adapter capabilities and limitations** in `provider-adapters.md`, including Codex JSONL, OpenCode live-schema API, Antigravity NDJSON, unavailable provider behavior, timeout reconciliation, workspace rules, and configured permission policies.
- [x] **Step 4: Update triggers and examples** in English and Spanish to target Codex-led multi-runtime work. Keep single-agent requests and generic data-pipeline orchestration out of scope.
- [x] **Step 5: Rename package metadata and install instructions** to `codex-multi-runtime-orchestrator`; update the closed package file list for the new Python files and reference, and change CI/package labels while preserving the supported operating-system matrix.
- [x] **Step 6: Add a changelog entry** describing the Codex-first behavior, provider adapters, schema-4 JSON ledger, and continued schema-3 legacy support.
- [x] **Step 7: Review README and skill examples together.** Confirm that every documented command exists, installation targets Codex skills, and no OpenCode `[00]` session is presented as the root for normal Codex-led runs.

## Handoff

This plan is for implementation after user review. It intentionally leaves detailed adapter payload mapping to the live provider schemas/catalogs at runtime. No design decision requires a hosted service or an undocumented UI endpoint.
