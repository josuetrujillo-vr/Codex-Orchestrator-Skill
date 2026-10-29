# Provider adapters

The Codex conversation is the coordinator. Local adapters execute bounded
worker tasks for Codex CLI, OpenCode, and Antigravity; they do not coordinate
with one another. `preflight-providers [--ledger PATH]` emits one JSON record
per provider. Availability and configured models/agents are reported
independently. A missing provider is not a global failure: the runner blocks
tasks assigned to that provider and continues independent ready tasks.

The shared capability record advertises `launch`, `wait`, and `read`;
`cancel` and `resume` are not supported by these adapters. An adapter must not
claim a capability just because another provider has it.

On Windows Git Bash, the project remains a CI and package target, but installed
providers must expose a native executable or an explicit runtime/CLI entrypoint
(for example, `node.exe` plus a CLI script). `.cmd`, `.bat`, and `.ps1` shims
are unsupported because adapters invoke processes without a shell; discovery
reports those providers unavailable, and the runner can continue with other
providers.

## Codex CLI

- The adapter checks that the installed CLI supports `codex exec --json` and
  that local `codex login status` succeeds.
- Worker prompts are sent on standard input to `codex exec --json --cd
  WORKSPACE`, with an optional configured `--model` or `--profile`. JSONL
  events are parsed for the thread reference, terminal turn status, and final
  assistant message; terminal text output is not used as a substitute.
- There is no agent selector in this interface. Do not set `agent_id` for
  Codex. This CLI preflight does not expose a documented model catalog, so the
  CLI validates any explicit model/profile when invoked.
- Authentication remains in the locally configured Codex CLI. Do not copy
  credentials into task prompts or ledger configuration.

## OpenCode

- The adapter discovers the active local server, reads its live OpenAPI
  document, and queries its live location, agent catalog, and model catalog.
  The active schema and catalogs are authoritative; do not hardcode routes,
  session fields, agent IDs, or model IDs.
- Before creating a session, it confirms that the server's default location
  matches the task workspace, validates request bodies against the live
  schema, and selects the exact configured agent and unambiguous model.
  It creates a worker session, confirms its identity/workspace/configuration,
  sends the prompt, and reads messages until a supported terminal result is
  observed.
- It retains provider session identity and literal runtime status. Credential
  redaction and bounded message pagination are part of the adapter behavior.
- This is a worker route. The ordinary Codex-led flow does not create the
  OpenCode `[00] Orchestrator` session. Existing schema-3 OpenCode-only runs
  retain their former identity and validators as a separate legacy path.

## Antigravity CLI

- The adapter discovers agent and model catalogs through the CLI's structured
  JSON output, then checks the selected `agent_id` and `model_id`.
- It launches `agy -p` with `--output-format stream-json`, an explicit print
  timeout, and the configured model and agent. It parses NDJSON events and
  records the returned conversation ID and terminal result.
- CLI authentication and permission prompts remain under the user's local
  configuration. The adapter never adds a permission bypass flag.

## Configuration, workspace, and permissions

Each schema-4 task names a provider, workspace, write scope, prompt, criterion,
and expected output. It can also name provider-specific `agent_id` and
`model_id`. Optional `run.providers` entries configure an adapter with only
these supported keys: `command`, `timeout`, `model_id`, `agent_id`, and
`profile`. `command` may be an executable name or an argument list. Keep
authentication in each provider's own local CLI configuration.

The runner confirms provider readiness in the task workspace before dispatch.
If the workspace cannot be confirmed, do not grant write access; use a
read-only task or mark it blocked. Research and review work should normally
have an empty `write_scopes`. Concurrent task scopes and artifact paths must
not conflict. A dependency orders intentional overlap. The default parallel
limit is three; `run-multiruntime --parallel N` sets a positive per-run limit.

Provider permission policies are not made uniform or broadened by the
orchestrator. A request awaiting approval remains reserved and is not
verification. Never disable approval prompts to force a worker to proceed.

## Outcomes, timeouts, and reconciliation

Provider status is kept literal in `runtime_status`; normalized outcome is
stored separately in `execution_outcome`, and local lifecycle is stored in
`state`. Successful runtime completion becomes `completed`. Codex reviews the
result and calls `verify-task` only after the acceptance criterion is met.

A launch error for one task does not cancel independent tasks. Timeouts,
malformed output, and ambiguous provider effects remain distinguishable from
confirmed success. `outcome-unknown` retains its capacity and workspace
reservations. The runner does not retry uncertain launches, resume sessions,
or cancel work automatically. Reconcile the provider reference and effects
before any manual retry; if the effect cannot be established, report the
unknown result and keep dependent work blocked.

Task outputs and traces are written below `.orchestrator/<ledger-path-hash>/`
in the task workspace. Inspect these artifacts and the returned result against
the task's criterion; file existence and process exit alone do not prove
success.
