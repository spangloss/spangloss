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

## `sec.data_class` `secrets` has low precision

**What:** Both labelers find every secret in the scenario gold but label many calls without one `secrets`. v0 ships with these false positives.

**Evidence:** scenario gold, 111 calls, v0 cards (rescored 2026-11-04):

| Model | `secrets` recall | `secrets` precision | False positives |
| --- | --- | --- | --- |
| Kev | 5/5 | **5/17 (29.4%)** | 12 |
| Jev | 5/5 | **5/20 (25.0%)** | 15 |

- 11 of Jev's false positives, and the same 11 calls for Kev, are test-config calls in `hard_negatives`: the 4 content-basis `.env.test` Reads above, 4 Globs of `**/.env*` and 3 Greps whose pattern mentions `.env`.
- Kev's 12th is the `find` below (p 0.53).
- Jev's other 4 are in `secrets_read`: two Reads of client code that only names the key's environment variable (p 0.60, 0.61), a `find` whose output lists `./app/.env` by name (p 0.73), and an args-basis Grep for credential words over the workspace (p 0.61).

**Effect:** treat a single `sec.data_class` = `secrets` as a candidate, not a confirmed credential access; detections that combine it with other labels are less affected.

## Kev's `sec.intent` misses on real-session Bash calls

**What:** On real sessions, Kev labels many Bash calls' intent `other` where gold has `explore` or `modify`. On scenarios its intent is accurate.

**Evidence:** taxonomy 0.4.0 results ([results-v0.md](../results-v0.md)). Kev is right on 8 of 13 real-session calls (61.5%, 95% CI 35.5–82.3%), against 104 of 107 (97.2%) on scenarios. Jev is right on all 12 real-session calls it labeled. Kev's real-session answers, gold → Kev:

| Gold | Kev `explore` | Kev `retrieve` | Kev `modify` | Kev `delegate` | Kev `other` |
| --- | --- | --- | --- | --- | --- |
| `explore` | 1 | | | | **2** |
| `retrieve` | | 5 | | | |
| `modify` | | | 1 | | **3** |
| `delegate` | | | | 1 | |

- All 5 errors are `other`, all on Bash calls at `content` basis, across 4 call shapes, with p 0.31–0.41. Kev is right on 4 of the 9 Bash calls and on all 4 Read and Agent calls.
- No wrong answer is another real intent: Kev falls back to `other` rather than confusing one intent with another.

**Effect:** with Kev, real-session shell commands that explore or change the workspace can carry `sec.intent` `other`, so detections keyed on `explore` or `modify` miss them. The 13 calls are too few to say how often this happens in general.

## Trifecta errors on real sessions

**What:** Both labelers mark real-session ToolSearch calls as a sink and an untrusted source, and both miss some Bash calls that read private data.

**Evidence:** taxonomy 0.4.0 results, real-session gold (18 calls for Jev, 19 for Kev, after calls set by rule):

| Label | Kev | Jev | Where the errors fall |
| --- | --- | --- | --- |
| `sec.trifecta.sink` | precision 4/6, recall 4/4 | precision 4/6, recall 4/4 | both false positives are ToolSearch |
| `sec.trifecta.untrusted_source` | precision 4/6, recall 4/4 | precision 4/6, recall 4/4 | both false positives are ToolSearch |
| `sec.trifecta.private_source` | precision 10/11, recall 10/12 | precision 9/9, recall 9/11 | misses are Bash; Kev's false positive is SubagentHandback |

- The sink and untrusted_source false positives are the same 2 ToolSearch calls for both labelers and both labels: one call shape, at `name_only` basis. Kev's p(yes) is 0.52 for sink and 0.91 for untrusted_source; Jev's is 0.50–0.54 and 0.58–0.61.
- Jev's 2 private_source misses are Bash calls (p 0.57 for `no`). Kev also misses 2 Bash calls, one of them the same call.
- These are the only trifecta errors on real sessions. On scenarios, sink and untrusted_source are 100% for both labelers.

**Effect:** a ToolSearch call can supply two legs of the trifecta at once, so session-level trifecta detections can fire on sessions that only searched for tools. ToolSearch spans carry no output ([capture-gaps.md](capture-gaps.md)), so the labelers judge these calls by name alone.

## Kev is less well calibrated than Jev

**What:** Kev's probabilities match how often it is right less closely than Jev's. Kev mostly understates its confidence: its mean p is at or below its accuracy on every label.

**Evidence:** expected calibration error (10 equal-width bins) at taxonomy 0.4.0:

| Label | Real sessions: Kev | Real sessions: Jev | Scenarios: Kev | Scenarios: Jev |
| --- | --- | --- | --- | --- |
| `sec.data_class` | **0.301** | 0.062 | **0.257** | 0.127 |
| `sec.effect` | **0.279** | 0.035 | 0.141 | 0.036 |
| `sec.injection` | not asked | 0.085 | not asked | 0.069 |
| `sec.intent` | 0.118 | 0.128 | **0.299** | 0.047 |
| `sec.reversibility` | 0.170 | 0.057 | 0.130 | 0.000 |
| `sec.trifecta.private_source` | 0.131 | 0.134 | 0.198 | 0.121 |
| `sec.trifecta.sink` | 0.118 | 0.099 | 0.048 | 0.034 |
| `sec.trifecta.untrusted_source` | 0.197 | 0.117 | 0.152 | 0.069 |

- Kev's ECE is higher than Jev's on 12 of 14 label and set pairs, and reaches 0.30; Jev's is at most 0.13.
- Example: on scenario intent Kev is right 97% of the time with mean p 0.67; Jev is right 100% with mean p 0.95.
- **Only indicative at this size.** Real-session labels rest on 12–20 calls spread over 10 bins, so a few calls move ECE a lot, and scenario calls repeat across runs.

**Effect:** Kev's `sec.<label>.p` understates how reliable its answers are, so a p cutoff tuned on Jev would drop many correct Kev answers. Compare p only within one labeler.
