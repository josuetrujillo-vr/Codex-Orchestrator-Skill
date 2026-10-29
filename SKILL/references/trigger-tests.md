# Activation examples

Use these examples when reviewing the `description` in
[`SKILL.md`](../SKILL.md). They describe intended routing; they are not a
runtime test suite. The skill applies to Codex-led coordination of multiple
distinct tasks across agents or runtimes.

## Should activate

1. "From Codex, split this migration into tasks and coordinate Codex CLI, OpenCode, and Antigravity workers; integrate their results."
2. "Coordinate a multi-agent review across whichever runtimes are available, and keep independent tasks moving if one provider is missing."
3. "Assign distinct agents to review separate modules, track dependencies and write scopes in a DAG, and integrate the results."
4. "Orchestrate multiple agents from Codex and verify each result against its acceptance criterion."
5. "Continue this existing OpenCode schema-3 run and reconcile its worker ledger." (legacy route)

## Should not activate

1. "Explain what an AI agent is and how it differs from a chatbot." (general explanation)
2. "Fix this React component bug." (single-agent request)
3. "Orchestrate a nightly Airflow pipeline that loads CSV files into a warehouse." (generic data-pipeline orchestration)

Single-agent work stays with the ordinary Codex workflow. A mention of agents
alone does not request multi-runtime coordination, and data-pipeline
orchestration is outside this skill's scope.
