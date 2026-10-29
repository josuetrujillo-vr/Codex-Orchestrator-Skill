# Codex-Led Multi-Runtime Orchestrator Design

**Status:** Approved by user
**Date:** 2026-10-28

## Intent

Turn this OpenCode-focused orchestration skill into a Codex-first workflow that lets the active Codex conversation (using a user-selected Codex model such as GPT-6 Sol or Astra) break a request into distinct tasks, dispatch those tasks concurrently to Codex, OpenCode, and Antigravity agents, collect their results, and integrate them.

Codex remains the planning and integration point. Workers receive bounded, non-duplicative assignments chosen for their available runtime, model, and capabilities. The workflow must be usable locally from Codex without requiring a hosted coordinator service.

## Goals

- Package and document the project as a Codex skill, with Codex as the top-level orchestrator.
- Keep OpenCode as a supported worker runtime and retain its current session API integration where it fits.
- Add local adapters for Codex CLI and Antigravity CLI using their documented machine-readable execution modes.
- Let Codex create a task DAG of distinct assignments, route tasks to configured and available agents, launch independent tasks concurrently, and collect each result.
- Keep durable task identity, dependencies, write scopes, lifecycle states, evidence, and fail-closed behavior in the project.
- Discover provider capabilities and validate them before dispatch; report unavailable or unverified capabilities instead of pretending a task ran.
- Preserve the current POSIX shell and small-runtime footprint where practical, including Windows Git Bash, Linux, macOS, and WSL support.

## Non-goals

- Building a persistent MCP gateway, desktop dashboard, or hosted orchestration service in this iteration.
- Automating Antigravity or Codex by reading private desktop state or controlling undocumented UI internals.
- Making worker agents message one another directly; Codex owns planning, routing, and integration.
- Requiring every provider worker to create a fixed number of native child agents. Native subagent trees remain provider-specific and optional.
- Automatically approving tool, file, network, or shell actions across providers.
- Requiring API keys that bypass the user's locally authenticated CLI accounts.

## Anti-goals

- Broadcasting the same task to every model as the default. Codex should decompose work into separate assignments and compare/integrate the distinct results.
- Claiming a task is verified merely because a process exited successfully or returned text.
- Allowing concurrent workers to write overlapping file scopes without a dependency or other explicit serialization.
- Hardcoding provider model names, agent names, endpoint routes, or session schemas when a supported live catalog or schema can be queried.

## Constraints

- This repository is currently a distributable skill under `SKILL/`, backed by POSIX `sh`, `awk`, Python standard library utilities, a closed-list packager, and cross-platform CI.
- The current OpenCode implementation assumes OpenCode owns the `[00]` orchestrator session and models work as a `worker_session` plus exactly two verified native `subagent` children. Those assumptions cannot govern Codex-led work across runtimes.
- OpenCode exposes a headless HTTP server and an OpenAPI schema; the adapter must continue to treat the active instance's schema and catalogs as authoritative.
- Codex provides `codex exec` structured output and a documented SDK/app-server surface. This design uses the CLI for worker invocations; the Codex app conversation remains the root coordinator.
- Antigravity's documented headless CLI accepts a prompt and can return JSON or NDJSON with a terminal status and conversation ID. Its Python SDK is an optional future route, not a required dependency here.
- Use the official CLI authentication already configured on the machine. Never copy credentials into prompts, the ledger, logs, or result artifacts.
- The common working directory must be available and checked for each agent that can edit files. If a runtime cannot be confirmed against the intended workspace, restrict that task to text-only/read-only work or block it.

## Approaches

### 1. Codex skill with local provider adapters — selected

Make the active Codex task the root coordinator and add local provider adapters to the existing skill/scripts package. Use `codex exec --json` for Codex worker runs, the existing OpenCode server/API route for OpenCode, and `agy -p` with JSON or stream-JSON output for Antigravity. Keep the task ledger and safety checks local.

This fits the existing skill and shell-tooling shape, uses documented local interfaces, and avoids requiring a long-running service. The tradeoff is that session lifecycle and events differ by provider, so adapters need explicit capability mapping and the shared contract must remain conservative.

### 2. Persistent MCP provider gateway — deferred

Add a separate MCP service that exposes provider-neutral operations to Codex and maintains provider sessions, streaming, approvals, and result collection behind those tools.

This could give Codex a richer, more uniform control surface and persistent task management. It adds a new service/runtime, plugin and MCP packaging, deployment and lifecycle work, and would be a larger first change than this repository needs.

## Decisions from the conversation

- Codex is the principal coordinator; the chosen Codex model performs decomposition and final integration.
- Delegation is automated: Codex creates/sends tasks and collects results.
- Work is divided into distinct assignments, rather than defaulting to identical prompts sent to every model.
- A run may use any subset of the three runtimes that is configured and capable. Missing providers do not block independent work; tasks that specifically require a missing provider are recorded as blocked while the rest of the run continues.
- The chosen implementation direction is a Codex skill with local provider adapters, not a persistent MCP gateway.

## Proposed design

### Coordinator and task flow

1. Codex inspects the request and available provider inventory, then proposes or constructs a DAG of distinct tasks with acceptance criteria, dependencies, required capabilities, workspace location, and write scope.
2. Codex chooses a configured agent/model that satisfies each task's needs and is currently available. Provider selection is per task; no permanent provider-to-task mapping is assumed.
3. The local orchestrator records a task as `launching` before calling an adapter. Independent tasks are launched concurrently up to a configurable run limit. Dependencies and overlapping write scopes are enforced before launch.
4. Adapters return a provider-specific task/session reference and expose their actual progress, terminal status, response, and errors through a normalized adapter result.
5. Codex inspects each result against its task criterion and evidence, updates the ledger, resolves dependencies, and integrates completed work. A process exit or non-empty response alone does not make a task `verified`.

The active Codex conversation is the logical root. The program must not create a second `[00] Orchestrator` session in OpenCode for ordinary Codex-led runs. Legacy OpenCode-only workflows may remain available as a clearly separated compatibility route if practical.

### Provider adapters

Each adapter must implement the common operations the runtime actually supports: `preflight`, `launch`, `wait/read`, and, when advertised, `cancel` and `resume`. The adapter reports a capability inventory instead of pretending all providers have identical session semantics.

- **Codex:** invoke the installed Codex CLI with structured JSONL output for worker tasks. Pass an explicit model only after validating it against the locally available Codex model/configuration surface. Capture the final response, process exit code, and structured events; do not parse human-formatted terminal output.
- **OpenCode:** preserve the current OpenCode session API workflow as its adapter. Discover the server, OpenAPI schema, agents/models, and task routes from the live instance before acting. Keep current credential redaction and session reconciliation behavior.
- **Antigravity:** invoke `agy -p` using machine-readable JSON or stream-JSON output, explicit timeout, and an available model/agent when configured. Persist the returned conversation ID for follow-up/reconciliation. Keep CLI permissions at their configured policy; never add the bypass-permissions flag by default.

Provider launch failures, timeouts, permission waits, unknown completion, and malformed output must remain distinguishable. A failed or unavailable adapter must not prevent reporting the results of other tasks in the same run.

### Generic task ledger

Replace the provider-bound worker/subagent identity contract with a versioned, provider-neutral task schema. Keep the existing DAG, scope, evidence, and lifecycle principles. Each task should record:

- stable logical `task_id`, optional logical `parent_task_id`, and dependency task IDs;
- provider/runtime ID, configured agent and model identifiers, and provider-specific session/conversation/turn references when actually observed;
- workspace identity/path and explicit write scopes;
- prompt/input artifact, output artifact, acceptance criterion, and evidence references;
- local lifecycle state, literal runtime status, normalized execution outcome, timestamps, and failure/approval notes.

Native runtime parent IDs are optional metadata, not the logical ownership relation. Remove the mandatory two-native-subagents-per-worker closure rule. Preserve verification gates: each task needs inspectable output and evidence that satisfies its criterion before it is marked `verified`. Keep a legacy schema-3 validator or a documented migration path for existing OpenCode ledgers; choose the smaller reliable path during implementation and document the compatibility boundary.

### Workspace and concurrency

All agents that may write must be confirmed to operate on the same intended repository/workspace. Independent assignments receive non-overlapping write scopes; overlapping scopes require an explicit dependency and serialized execution. Review and research assignments default to read-only. If an adapter cannot confirm its workspace, it may return analysis as text but cannot be treated as having edited the user's working tree.

Use a configurable parallel-task limit. Keep reservations for running, awaiting-approval, or outcome-unknown tasks until reconciliation, so a timeout does not silently free a write scope. Do not automatically retry a launch when the first attempt's effect is unknown.

### Codex-first skill and package

Update the skill's trigger and instructions to describe Codex-led multi-runtime orchestration, document Codex as the root and the three supported worker runtimes, and add a Codex installation path. Rename the distributable skill/package to a Codex-first multi-runtime name (proposed: `codex-multi-runtime-orchestrator`) while leaving the GitHub repository URL/name to the repository owner. Update README files, package metadata, the closed file list, examples, and CI/package labels consistently. Retain OpenCode documentation as the worker adapter reference rather than the product identity.

## Success criteria

- From a Codex session, the skill can discover which of Codex CLI, OpenCode, and Antigravity are configured and report each provider's usable capabilities without exposing credentials; a missing provider does not block tasks assigned to other available providers.
- Codex can dispatch different bounded tasks across any available subset of runtimes, with independent tasks launched concurrently and dependent tasks held until prerequisites settle.
- Results and failures from every task are captured in one provider-neutral ledger and returned to the Codex coordinator for integration.
- Missing access, unsupported model/agent, unconfirmed workspace, approval wait, timeout, and unknown outcome are represented honestly; none is reported as successful verification.
- OpenCode remains usable as a worker runtime, with its live schema/catalog preflight and existing fail-closed behavior retained.
- The package and installation instructions identify Codex as the host skill while keeping the implementation compatible with the repository's supported local shell platforms.

## Calls left to the builder

- Select the smallest schema-4 representation and migration/legacy-validation strategy that preserves useful existing schema-3 OpenCode runs.
- Keep the adapter implementation in the current POSIX shell structure unless a specific provider capability cannot be implemented cleanly there; reuse Python's standard library rather than adding a dependency if structured parsing needs it.
- Make the parallel-task limit configurable and document its default and override.
- Choose exact subcommand names and output contract while keeping status/result capture machine-readable and useful to Codex.
- Keep UI tab attachment, private application stores, hosted API orchestration, and persistent MCP server work outside this iteration.

## Official interface references

- [Codex SDK](https://developers.openai.com/codex/sdk)
- [Codex App Server](https://developers.openai.com/codex/app-server)
- [Codex CLI](https://developers.openai.com/codex/cli)
- [OpenCode Server](https://dev.opencode.ai/docs/server/)
- [OpenCode V2 API](https://dev.opencode.ai/v2/docs/api)
- [Antigravity CLI headless mode](https://antigravity.google/docs/cli/headless/)
- [Antigravity CLI reference](https://antigravity.google/docs/cli/reference/)
- [Antigravity SDK overview](https://antigravity.google/docs/sdk/overview)
