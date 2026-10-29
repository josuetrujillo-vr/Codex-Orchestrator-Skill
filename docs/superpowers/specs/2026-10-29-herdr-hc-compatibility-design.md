# Herdr Core (`hc`) compatibility design

**Status:** Approved by user
**Date:** 2026-10-29

## Intent

Add an optional Herdr Core execution path to the Codex Multi-Runtime Orchestrator. When selected, the current Codex CLI, OpenCode, and Antigravity workers run in Herdr-managed terminals that remain visible and persistent in Herdr. Codex stays the DAG coordinator and the only component that verifies task criteria.

## Goals

- Keep direct provider execution as the default and preserve existing schema-4 runs.
- Let a run opt into Herdr Core through its `hc` CLI without changing each task's provider identity.
- Capture both the provider's native session/conversation reference and the Herdr terminal reference when observed.
- Preserve provider-native structured output, task workspace, permission policy, write-scope scheduling, redaction, and Codex's verification gate.
- Support the local Herdr Core CLI on the repository's supported host platforms; report missing or incompatible `hc` as unavailable without installing it or silently falling back.
- Leave completed worker terminals available for inspection in Herdr.

## Non-goals

- Replace Codex as the coordinator or make Herdr another model provider.
- Replace the schema-4 task ledger or alter its dependency and verification semantics.
- Install, update, or configure Herdr automatically.
- Route tasks to remote Herdr machines in this iteration; workspace identity must remain confirmable by the local runner.
- Automate Herdr's desktop UI, bypass provider permissions, retry uncertain launches, or infer success from terminal text/state alone.

## Approaches

1. **Optional `hc` execution backend — selected.** Keep provider adapters responsible for Codex, OpenCode, and Antigravity semantics, and add a Herdr-backed execution path that launches each provider's documented CLI in a Herdr-managed terminal. This uses Herdr for persistent terminal lifecycle while preserving the current DAG and provider-specific result interpretation. The tradeoff is that each provider needs a supported CLI invocation path in addition to its existing direct path.
2. **Herdr-native delegation from the skill.** Teach the Codex router to launch and manage helpers itself through `hc`. This uses Herdr's agent-to-agent workflow directly, but would split scheduling and lifecycle state between Herdr and the existing ledger.
3. **In-pane compatibility only.** Detect Herdr context and document how to run the current skill from a Herdr pane. This is smaller, but worker processes would not become separately managed Herdr terminals.

The user selected approach 1: “Usar `hc` como capa opcional para ejecutar los trabajadores actuales.”

## Decisions

- Existing provider names remain `codex`, `opencode`, and `antigravity`; `hc` is an execution backend, not a provider.
- The run selects `direct` or `herdr`; absent configuration means `direct`.
- Herdr-backed tasks use provider-native CLI output formats: Codex `exec --json`, OpenCode `run --format json`, and Antigravity `-p --output-format stream-json`. Existing direct adapters remain the default routes.
- Add an optional nullable `transport_ref` to schema-4 tasks for the Herdr terminal name. Existing ledgers without the field remain valid and are treated as if it were `null`. `runtime_ref` continues to hold the provider's observed session/conversation identity.
- Allow an optional `run.herdr.command` executable or argv prefix so a CLI installed outside the Codex process's `PATH` can be configured explicitly.
- Herdr Core is addressed through its supported `hc` CLI. The runner never starts or replaces a Herdr server. It only launches work when a compatible Core is already available.
- When Herdr mode is selected but `hc` is unavailable, incompatible, or cannot confirm the task workspace, the task is blocked or marked uncertain as appropriate. There is no implicit direct-mode fallback.
- Herdr manages terminal persistence, not filesystem permissions. The underlying provider's configured permission behavior remains authoritative, and the orchestrator's write-scope rules still govern scheduling.
- Remote Herdr machines, terminal cancellation, and automatic resumption after an uncertain result remain outside this iteration.

## Proposed design

### Configuration and discovery

Add an optional `run.execution_backend` field with values `direct` and `herdr`. Extend ledger validation without changing schema version 4. The provider-neutral preflight reports Herdr Core separately from the three model providers and checks the installed `hc` CLI's supported process lifecycle commands. It must not start work or mutate Herdr configuration.

When the selected backend is `herdr`, construct provider-specific argument arrays and launch them through `hc` in the declared workspace. Do not interpolate prompts into shell command strings. Preserve Codex model/profile, OpenCode agent/model, and Antigravity agent/model selection. OpenCode's Herdr path uses its non-interactive JSON CLI route; its existing direct OpenAPI route remains available when the backend is `direct`.

### Lifecycle and evidence

The runner writes `launching` and the deterministic task-scoped Herdr terminal name to the ledger before submitting a task. `hc run --bg` starts a small Python worker in that terminal; the worker launches the provider CLI with `shell=False`, streams output to the terminal for visibility, and writes the raw provider streams and exit result to task artifacts. The coordinator parses those artifacts rather than terminal rendering. This preserves structured output while the provider process runs inside a persistent Herdr terminal. The literal provider status remains in `runtime_status`; Herdr terminal state never proves semantic success.

A worker that produces a confirmed provider success becomes `completed`. Codex inspects the saved response/output against the criterion and calls `verify-task` to become `verified`. If launch delivery or terminal completion is ambiguous, retain the Herdr name and task reservation as `outcome-unknown`; do not retry automatically. The user can inspect the persistent terminal through Herdr/`hc` and reconcile its sidecar artifacts before clearing the run's stale lock or dispatching again.

Each managed terminal gets a stable, task-scoped name. Completed terminals are left in Herdr. Every provider trace and response continues under `.orchestrator/<ledger-path-hash>/<task-id>/` with credential redaction.

### Compatibility and failure behavior

- Existing ledgers with no `run.execution_backend` continue to use direct adapters.
- `hc` is discovered only when Herdr mode is selected; a missing optional Herdr installation does not affect direct runs.
- A Herdr invocation failure blocks that task if no request was submitted; once submission may have occurred, record `outcome-unknown` and preserve its reference/scope.
- The Herdr terminal address and provider-native session reference are recorded only after each runtime returns them.
- Use no additional Python dependencies; keep argument passing shell-free for paths and prompts with spaces or Unicode.
- The first implementation targets local Herdr Core. It does not assume that a workspace on an SSH-connected Herdr machine is the local repository.

## Success criteria

- A legacy schema-4 ledger without backend configuration validates and runs through the existing direct path.
- A Herdr-configured run discovers `hc`, launches each supported provider CLI in the requested workspace, and exposes its terminal in Herdr.
- Herdr terminal references survive runner interruption in the ledger or referenced trace, so an uncertain launch can be reconciled without a duplicate dispatch.
- Provider-specific JSON/JSONL output is captured and parsed; terminal text or Herdr's `done` state alone never makes a task completed or verified.
- Codex still performs task verification, and dependent tasks remain blocked until prerequisites are verified.
- Missing `hc`, unsupported command behavior, path/workspace mismatch, timeout, malformed output, and provider errors are reported without automatic fallback or retry.
- Existing permission policies, scope reservations, and credential redaction remain in effect.

## Calls left to the builder

- Confirm the precise `hc run --bg --name` argument shape against the installed Core CLI; use the public CLI, not private socket state.
- Define how the helper writes its completion sidecar atomically so a partial record cannot be mistaken for a terminal result.
- Confirm that OpenCode's JSON event stream is emitted intact by `opencode run --format json` under the helper's captured stdout; if not, keep Herdr mode fail-closed for OpenCode and document the limitation.
- Document the minimum supported Herdr Core CLI version only if a concrete CLI capability requires it.

## Official references

- [Herdr Core overview and `hc` workflows](https://herdr.dev/core/)
- [Herdr Core repository and CLI contract](https://github.com/herdrdev/herdr-core)
- [OpenCode CLI commands](https://dev.opencode.ai/v2/docs/cli/commands/)
- [Codex CLI structured execution](https://developers.openai.com/blog/eval-skills)
