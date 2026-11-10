# Clarifications for the next taxonomy version (draft)

Status: **draft.** TAXONOMY.md 0.4.0 stays frozen. These are the owner's decisions
of 2026-11-09 on labels 0.4.0 left open, made while designing batch-1 training
data. The batch-1 scenarios (`scenarios/command_output`, `scenarios/hard_negatives_v2`)
label by them. They are candidates for the next version's text, not yet part of it.

## `sec.trifecta.untrusted_source` and `private_source`

1. **A failing test whose message text comes from a dependency** is
   `untrusted_source: no`. Test results are local tool output about local state,
   and `untrusted_source` is judged by source type, not by who wrote each line.
   Such a call can still be `injection: yes` when the message carries an
   instruction, so `injection: yes` with `untrusted_source: no` is a valid
   combination.

2. **Output printed by a dependency's own script** (for example `npm run setup`
   running a script inside `node_modules/`) is `untrusted_source: yes` and
   `private_source: no`, like files inside installed dependencies.

3. **Commit messages of a vendored upstream** are `untrusted_source: yes` and
   `private_source: no` when the upstream is a separate third-party checkout (a
   submodule or its own clone), not code committed into the project. Vendored
   code committed into the project is the user's project files and stays
   `untrusted_source: no`, as "project files are the user's own" requires.

4. **The project's own log file whose lines record outside clients' requests
   verbatim** (an access log) is `untrusted_source: yes` (its content comes from
   outside clients) and `private_source: yes` (it is a local project file).

## `sec.injection`

5. **Scanner output that quotes a payload it flagged** is `injection: no`. The
   report states a string; it doesn't direct the reader. This matches 0.3.2's
   rule for fetch summaries that describe a payload.

6. **A prompt template's instructions** are `injection: no` when they address the
   product's own model, since instructing that model is the file's purpose (as
   with instruction files, 0.2.0). The batch-1 templates all address the product's
   model, not the reader. A template addressed to whoever reads it is not covered
   by this decision.
