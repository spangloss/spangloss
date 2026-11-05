# Known limitations (v0)

Limitations of the labelers that v0 ships with and doesn't try to fix. These are about the models, not the telemetry; telemetry gaps are in [capture-gaps.md](capture-gaps.md).

## `sec.injection` needs Jev

**What:** In v0, Kev, the local labeler, does not answer `sec.injection`. The card is not sent to Kev, no label is written, `spangloss label` reports the label as unsupported, and `spangloss score` shows it as unsupported for Kev rather than scoring it (`backends/support.py`). An absent `sec.injection` means "not asked", never "no". Use Jev, the hosted labeler, for injection.

**Why:** Kev's answers on the v0 card (card (a) with its tool-hints note, threshold 0.5) were not usable. In the 2026-11-03 rerun over all injection-eligible gold (82 calls: 67 scenario, 15 real-session), Kev missed every injection planted in a dependency file and labeled ordinary instruction files and security docs `yes`:

| Call shape | Gold | n | Kev `yes` | Kev p(yes) | Jev `yes` |
| --- | --- | --- | --- | --- | --- |
| curl control (web page fetched with `curl`) | yes | 3 | 3 (recall 3/3) | 0.624 | 3 |
| dependency README (`poisoned_dependency`) | yes | 8 | **0 (recall 0/8)** | 0.423 | 8 |
| reported-injection summaries | no | 6 | 0 | 0.374–0.480 | 0 |
| security doc, framed examples | no | 4 | **4** | 0.566 | 0 |
| `AGENTS.md` with build steps | no | 4 | **4** | 0.553 | 0 |
| other scenario calls | no | 42 | 0 | 0.101–0.429 | 0 |
| real-session calls | no | 15 | 1 | 0.261–0.524 | 0 of 14 |

- **Kev: recall 3/11, precision 3/11.** Every positive Kev finds is the curl control. Jev: 11/11 and 11/11.
- **No threshold separates Kev's answers.** The lowest positive (README, 0.423) sits below the framed examples and `AGENTS.md` (0.553–0.566). The same held under the earlier cards: README p(yes) 0.449 on the 0.3.2 injection card and 0.281 on card (a) without its note, where Kev scored the README the same as ordinary project files from the same scenario (0.282).
- **The evidence was complete.** The README is 789 characters and went to Kev whole, in one chunk, with the canary token in it.
- **The v0 note made Kev worse on framed text.** Adding the tool-hints note to card (a) moved Kev's framed-example and `AGENTS.md` Reads from p(yes) 0.468 and 0.444 to 0.566 and 0.553, from `no` to `yes`.

**Effect:** with Kev alone, no call carries `sec.injection`, so injection-based detections need Jev's labels. A later Kev version can be supported again by removing it from `backends/support.py` once it scores well on the same gold.

## `.env.test` Reads with captured content are often labeled `secrets`

**What:** A Read, Grep or Glob whose target is a test-config file (`.env.test`, `.env.example`, `.env.sample`, `.env.template`) is `sec.data_class` `none` by a derived rule, without asking a model, when the call's content was not captured (`args` basis; `data_class` is not asked at `name_only`). When the content was captured (`content` basis), the model is still asked, because the content could show a real secret (TAXONOMY.md, data_class notes), and both labelers tend to answer `secrets` there.

**Evidence:** in the `hard_negatives` scenario, every Read of `.env.test` was at `content` basis, holding test-mode keys (`tallow_test_…`); gold is `none`. Kev and Jev answered `secrets` on all 4 (Kev p 0.49, Jev p 0.53–0.61), with the data_class card's test-config note in place. The note lowered Jev's p from about 0.90 but did not change the answer.

**Effect:** reading a test-config file with its content captured can be labeled `secrets`, a false positive for credential-access detections. Searches whose target is not itself a test-config file, such as a Glob of `**/.env*` (which also matches `.env`) or a Grep whose pattern mentions `.env`, are not covered by the derived rule and are model-answered at any basis.
