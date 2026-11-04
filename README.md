# spangloss

Your agent's actions, glossed.

The name is span + gloss: an OpenTelemetry span, annotated.

Spangloss adds security labels to AI agent telemetry. It reads the OpenTelemetry spans an agent session emits, asks a decision model a fixed set of narrow, typed questions about each tool call, and writes the answers back as `sec.*` attributes with probabilities. Detection rules and audits then run over the labels, not over raw prompts and tool output.

It is detective, not preventive: Spangloss never blocks an agent action.

## Why

Coding agents read files, run commands, fetch web pages and call third-party tools. Their telemetry already records each of those calls, but as raw arguments and output. Raw content is hard to write rules over and risky to keep. A question like "did this session read a secret, take in untrusted content and then send data out?" needs labels rather than text search.

Spangloss turns each tool call into a small set of typed facts:

| Label | Asks |
| --- | --- |
| `sec.effect` | Does the call read, write, delete, execute, or change nothing outside the agent? |
| `sec.reversibility` | Can its effect be undone? |
| `sec.data_class` | What is the most sensitive data it touches (secrets, payment, health, personal, or none)? |
| `sec.trifecta.*` | Does it read private data, bring in untrusted content, or send data out? |
| `sec.intent` | What is the call's purpose (explore, retrieve, modify, execute, communicate, ...)? |
| `sec.injection` | Does incoming content carry instructions aimed at the agent beyond its purpose? |

The exact questions, answer sets and labeling rules are in [TAXONOMY.md](TAXONOMY.md).

Every label is written with its probability and provenance:

```
sec.<label>: <value>
sec.<label>.p: <0.0–1.0>
sec.basis: name_only | args | content
sec.model: <backend and version>
sec.taxonomy: <taxonomy version>
```

## How it works

1. **Capture.** Claude Code exports spans and log events to a local OpenTelemetry Collector, which strips account identity and writes them to `data/raw/` (git-ignored). See [docs/capture.md](docs/capture.md).
2. **Parse.** An agent adapter joins tool spans with their log events into one record per tool call. It records the call's arguments and output, its nesting under subagents, the MCP servers it loads, and `sec.basis`: whether the call shows only its name, its arguments, or its content too. Claude Code is the first adapter.
3. **Label.** Each label is a question card. A question is asked only when the call shows enough to answer it; injection, for example, is asked only when the call has incoming content. Untrusted span content goes only in a marked evidence field, never mixed into the question. Facts the parser already knows, such as which MCP server a tool came from, are written as derived labels without asking a model. Backends are pluggable: Kev (a local model) and Jev (hosted), both through the TypeSafe SDK.
4. **Rules.** Deterministic, session-level detections over labels. The first is subagent taint: a subagent that took in untrusted content, flagged on the spawn that started it.
5. **Score.** Labels are scored against hand-labeled gold from real sessions and against gold from seeded scenarios. Scoring reports accuracy with 95% Wilson intervals, per-shape accuracy (so repeated identical calls don't inflate results), precision and recall on positives, confusion matrices and calibration.

### Seeded scenarios

Real sessions rarely contain the cases that matter most, such as a secret read or an injection attempt. Seeded scenarios fill that gap. Each runs Claude Code headless in an isolated Docker container. The container has no internet access, its only route out is a key proxy that allows only the model API, and a Collector runs per run. A workspace of fake fixtures and a canary instruction go in, and each expected call shape is labeled up front. A check matches the run's calls to those expectations and writes scenario gold, or sends unclear calls to review. See [docs/scenarios-design.md](docs/scenarios-design.md).

## Design principles

- **Label only what the span shows.** A label judges the visible evidence, and `sec.basis` says how much there was. Where telemetry has gaps, the taxonomy says so instead of guessing.
- **Narrow, typed questions.** Each card asks one question with a fixed answer set and a probability, not a free-text verdict.
- **Span content is untrusted.** Instructions inside tool output are evidence to label, never instructions to follow.
- **Derive what is known.** Structural facts come from the parser, not from a model.
- **Vendor-neutral attributes.** Keys live under `sec.*`, not under the project's name.
- **No raw session data in git.** Only redacted, labeled evaluation data is ever published.

## Status

Pre-v0. The first public release, with per-label evaluation results, is planned for November 29, 2026. Until then there are no published accuracy figures, and the taxonomy (now 0.3.2) can still change.

What exists today:
- capture and parsing for Claude Code;
- the label cards, two backends and derived labels;
- a DuckDB label store, the subagent taint rule and the scoring harness;
- five seeded scenarios: secrets read, web-page injection through WebFetch and through curl, tainted delegation and a poisoned dependency.

Known limits:
- **Claude Code's telemetry has gaps** that bound what any label can see. Grep and Glob output, subagent handback content and MCP tool descriptions are not captured; long arguments are cut; and WebFetch output is a model's summary of the page. Tool poisoning through MCP tool descriptions is therefore invisible today. Details and evidence are in [docs/findings/capture-gaps.md](docs/findings/capture-gaps.md).
- **The evaluation sets are still small,** and injection positives come from few call shapes.
- **Claude Code is the only agent adapter.**

Current work and next steps are tracked in [docs/STATUS.md](docs/STATUS.md).

## Developer quickstart

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Docker, and Claude Code for capture.

```
uv sync
uv run pytest
```

Capture a session (details in [docs/capture.md](docs/capture.md)):

```
docker compose up -d           # Collector and Jaeger, bound to localhost
source scripts/telemetry.sh    # in the shell you'll run claude from
claude
```

Parse, label, run rules and score:

```
uv run spangloss parse data/raw/traces.jsonl
uv run spangloss label --backend kev --limit 20     # or --backend jev
uv run spangloss rules
uv run spangloss export-sheet --limit 20            # hand-label calls, then:
uv run spangloss import-sheet <sheet.csv>
uv run spangloss score
```

- **Kev** expects a local server at `127.0.0.1:8009`.
- **Jev** reads `TYPESAFE_API_KEY` from `.env`, which is git-ignored. Before sending a call, Jev's backend refuses any call that references `.env` files or contains a likely secret.
- **Your own MCP servers:** every MCP server's tool descriptions and tool results are labeled `untrusted_source` yes, derived without asking a model, unless the server is on your allowlist. The allowlist is `mcp.first_party_servers` in `spangloss.toml` (copy `spangloss.example.toml`); it is empty by default.

Seeded scenarios call the model API and need a dedicated, spend-limited key; see [docs/scenarios-design.md](docs/scenarios-design.md) before running them.

```
uv run spangloss scenarios list
uv run spangloss scenarios run <name>
uv run spangloss scenarios check
```

Development checks:

```
uv run ruff check .
uv run ruff format .
uv run pre-commit run --all-files
```

## Documents

- [TAXONOMY.md](TAXONOMY.md): the labels, questions, answer sets and rules
- [docs/capture.md](docs/capture.md): capturing Claude Code traces
- [docs/scenarios-design.md](docs/scenarios-design.md): the seeded scenario harness
- [docs/findings/capture-gaps.md](docs/findings/capture-gaps.md): what Claude Code telemetry does not show
- [docs/STATUS.md](docs/STATUS.md): current state and next steps

## License

Apache-2.0
