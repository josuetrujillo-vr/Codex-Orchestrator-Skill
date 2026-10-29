# Codex Multi-Runtime Orchestrator

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/host-Codex-6A5ACD.svg)](SKILL/SKILL.md)
[![Español](https://img.shields.io/badge/read%20in-Espa%C3%B1ol-blue.svg)](README.es.md)

Coordinate multi-agent work from the active Codex conversation. Codex breaks a
request into distinct DAG tasks, assigns them to configured Codex CLI,
OpenCode, or Antigravity workers, reviews their results, and integrates the
work. Independent tasks can run concurrently. The default is not to send the
same task to every runtime, and normal Codex-led runs do not create an
OpenCode `[00]` root.

## Workflow

1. Discover each provider and its usable capabilities.
2. Create a schema-4 JSON ledger with task criteria, dependencies, workspaces,
   and non-overlapping write scopes.
3. Dispatch ready tasks and continue unrelated branches when a provider is
   unavailable.
4. Inspect each completed result. Codex records `verified` only after the
   acceptance criterion is met.
5. Integrate results and close the ledger, recording blockers and uncertain
   outcomes honestly.

## Quick start

Run from the repository root. Start with provider discovery:

```sh
sh SKILL/scripts/orchestrate.sh preflight-providers
```

Create prompt files and a ledger using
[the schema-4 template](SKILL/references/ledger-template.md), then:

```sh
sh SKILL/scripts/orchestrate.sh validate-ledger-v4 runs/example/ledger.json
sh SKILL/scripts/orchestrate.sh run-multiruntime --ledger runs/example/ledger.json
sh SKILL/scripts/orchestrate.sh status-multiruntime --ledger runs/example/ledger.json
# After reviewing the output against the criterion:
sh SKILL/scripts/orchestrate.sh verify-task --ledger runs/example/ledger.json \
  --task-id research --observed "Reviewed report.md against the required integration points."
sh SKILL/scripts/orchestrate.sh validate-ledger-v4 --closing runs/example/ledger.json
```

The runner's default concurrency limit is three; `--parallel N` sets a
positive per-run limit. Runtime success is `completed`, not `verified`.
Dependencies launch only after their prerequisites are verified. The
[adapter guide](SKILL/references/provider-adapters.md) explains capabilities,
permission behavior, workspaces, and timeout reconciliation.

## Install as a Codex skill

Copy the `SKILL/` directory into your Codex skills directory, using the
package name from its frontmatter:

```sh
mkdir -p ~/.codex/skills/codex-multi-runtime-orchestrator
cp -R SKILL/. ~/.codex/skills/codex-multi-runtime-orchestrator/
```

The project package can be built with `sh SKILL/scripts/package.sh`; the
closed file list writes `dist/codex-multi-runtime-orchestrator.zip`.
Requirements for schema-4 commands are POSIX `sh` and Python 3.9+.
Windows Git Bash is a CI/package target. Installed providers must expose native
executables or explicit runtime/CLI entrypoints; `.cmd`, `.bat`, and `.ps1`
shims are reported unavailable, while other providers can continue.

## Schema-3 compatibility

Existing OpenCode-only schema-3 YAML ledgers, `[00]` roots, worker/subagent
rules, and their original commands and validators remain supported as a
legacy path. They are not required for a Codex-led schema-4 run. Do not pass a
schema-3 ledger to `validate-ledger-v4`.

## Project layout

- `SKILL/SKILL.md` — Codex-led workflow and command reference.
- `SKILL/scripts/` — shell entry point, schema-4 runner, provider adapters,
  and schema-3 OpenCode utilities.
- `SKILL/references/` — ledger, adapter, and legacy OpenCode documentation.
- `.github/workflows/ci.yml` — Linux, macOS, and Windows Git Bash CI matrix.

## Contributing

Keep the package's closed list in `SKILL/scripts/package.sh` synchronized with
the contents of `SKILL/`. Preserve the Linux, macOS, and Windows Git Bash
CI/package target matrix. Do not include `.superpowers/sdd/` review artifacts
in the distributable package.

## License

MIT; see [LICENSE](LICENSE).
