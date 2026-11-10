# hard_negatives_v2

Batch 1 (docs/drafts/training-data-batch1.md). Look-alikes only: every call is
`injection` no, and no canary token appears anywhere. Each of the 14 sets places
four of seven look-alike kinds in a small support-assistant project:

- a security doc or dependency README quoting an attack string as an example;
- tests whose inputs are payload strings;
- a red-team dataset;
- a prompt template, addressed to the product's own model;
- `AGENTS.md` or `CONTRIBUTING.md` with ordinary build steps;
- a prompt-scanner report quoting what it flagged;
- `npm test` output with npm's and Node's own hints.

The project is a git repository with one commit (made by setup). Runs use the
Node image. Sets hn03, hn08 and hn12 are the batch-1 test slice.
Train overlays are in `fixtures/sets/`; the held-out ones are local only (`fixtures/heldout/`, listed by digest in `scenarios/heldout.sha256`), and so is the generator that writes both (`scenarios/_gen/`).

**Local only** (`publishable: false`). All data is synthetic.
