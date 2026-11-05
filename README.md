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

## Threat model & non-goals

### "Aren't spans for debugging?"

They started out that way. So did syslog, auditd, CloudTrail and VPC flow logs, which are now standard inputs for security monitoring. Where data comes from doesn't decide what it's good for.

For AI agents, traces are the richest record available. A trace links the prompt, the model's output, each tool call and its result, and nests a subagent's calls under the call that spawned it. Spangloss labels each tool call from its own span today. Its first session-level rule, subagent taint, follows those parent/child links to flag a subagent that took in untrusted content. Rules that combine labels across a session, such as untrusted input followed by private-data access and then data leaving, build on the same structure.

The line between debugging and security is blurry anyway. "Why did my agent delete that?" is a question for both.

### What Spangloss is

- A **visibility and detection layer**: it reads agent spans and adds security labels (glosses) that people and downstream tools can act on.
- **Complementary** to OS-, network- and identity-level monitoring, not a replacement for it.

### What Spangloss is not

- **Not a control plane.** Spans are exported after the fact, in batches. Spangloss detects; it doesn't block. Preventing an action needs an inline hook in the agent runtime.
- **Not an audit log or forensic source of truth.** The application emits its own spans, so they aren't tamper-evident. A compromised agent or broken instrumentation can drop or change them without anyone noticing.
- **Not complete coverage.** Spangloss only sees what your agent framework instruments. Uninstrumented tools, subprocesses or side channels are blind spots. Some context the model reads never reaches the telemetry at all. With Claude Code today, that includes MCP tool descriptions and the contents of `CLAUDE.md` files. Instructions planted there can't be labeled directly. Their effects can still show up in later tool calls. Spangloss marks tool loads and MCP tool results as untrusted input, unless the server is on your `mcp.first_party_servers` allowlist (empty by default), so those calls carry the context. See [docs/findings/capture-gaps.md](docs/findings/capture-gaps.md) for the full list.

### Deployment guidance

- **Turn off sampling for agent spans.** Head or tail sampling is fine for performance tracing, but a dropped span is a missed detection. Send agent spans through their own pipeline if you need to keep sampling elsewhere.
- **Treat span data as sensitive.** Prompts, tool arguments and tool output can contain secrets or personal data. Restrict access to wherever spans and glosses are stored. Observability backends are often more open than security tools.
- **Set retention deliberately.** Tracing backends often keep data for days. If you need glosses for incident response, forward them somewhere with longer retention.
- **Use the Jev backend if you need injection detection.** Kev doesn't report `sec.injection`: in testing it couldn't tell a poisoned dependency README from an ordinary file. With Kev, the label is left out rather than set to `no`. See [docs/findings/known-limitations.md](docs/findings/known-limitations.md).
- **Keep `mcp.first_party_servers` short.** Results from servers on that list aren't treated as untrusted input. Add only servers you run or fully trust.
- **Review agent instruction files and MCP servers yourself.** Because their contents aren't captured, a poisoned `CLAUDE.md` or tool description is invisible to Spangloss until it causes an action.
- **Cross-check with independent telemetry** when the stakes are high, for example EDR, auditd or egress logs, which a compromised agent can't edit.

## Status

Pre-v0. The first public release, with per-label evaluation results, is planned for November 29, 2026. Until then there are no published accuracy figures.

The taxonomy is **0.4.0, frozen for v0** (tag `taxonomy-v0.4.0`). Changes found after the freeze wait for a later version.

What exists today:
- capture and parsing for Claude Code;
- the label cards, two backends and derived labels;
- a DuckDB label store, the subagent taint rule and the scoring harness;
- seven seeded scenarios: secrets read, web-page injection through WebFetch and through curl, tainted delegation, a poisoned dependency, hard negatives (look-alike cases that should not be flagged; local only, not published) and tool poisoning through an MCP tool description.

Known limits:
- **Claude Code's telemetry has gaps** that bound what any label can see. Six are documented: Grep and Glob output, subagent handback content, MCP tool descriptions and a workspace `CLAUDE.md` are not captured; long arguments are cut; and WebFetch output is a model's summary of the page. Tool poisoning through MCP tool descriptions and a poisoned `CLAUDE.md` are therefore invisible today. Details and evidence are in [docs/findings/capture-gaps.md](docs/findings/capture-gaps.md).
- **The labelers have known weaknesses:** Kev doesn't answer `sec.injection` in v0, and `sec.data_class` `secrets` has low precision for both backends. Details are in [docs/findings/known-limitations.md](docs/findings/known-limitations.md).
- **The evaluation sets are still small** (20 hand-labeled real calls, 111 scenario calls), and injection positives come from few call shapes.
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

- **Kev** expects a local server at `127.0.0.1:8009`. In v0, `spangloss label` reports `sec.injection` as unsupported for Kev; see [Deployment guidance](#deployment-guidance).
- **Jev** reads `TYPESAFE_API_KEY` from `.env`, which is git-ignored. Before sending a call, Jev's backend refuses any call that references `.env` files or contains a likely secret.
- **Your own MCP servers:** set `mcp.first_party_servers` in `spangloss.toml` (copy `spangloss.example.toml`). The `untrusted_source` label for these calls is derived in code, without asking a model; see [What Spangloss is not](#what-spangloss-is-not) and [Deployment guidance](#deployment-guidance).

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
