# Spangloss v0 results

<!-- Generated from site/results.json by python -m spangloss.resultsgen; don't edit by hand. -->

> **Lead (proposed):**
>
> These are Spangloss v0's per-label results at taxonomy 0.4.0: how often each labeler's answers match gold labels, on two gold sets scored separately. The real-session set is small (20 hand-labeled calls), and the scenario set repeats call shapes across runs, so read the intervals and the per-shape column, not only the accuracy. Jev, the hosted labeler, answers every label. Kev, the local labeler, answers every label but injection, where its v0 answers weren't usable. Both sets come from coding sessions in one agent, and telemetry gaps limit what any label can see, so these numbers describe these sets, not agent sessions in general.

Taxonomy 0.4.0. Generated 2026-11-06 at commit `cfd63bb`. Labelers: Kev (local), Jev (hosted). † marks a cell resting on fewer than 10 calls.

## Gold sets

The two sets are scored separately and never pooled.

| Gold set | What it is | Calls | Unique call shapes |
| --- | --- | --- | --- |
| Real sessions | Tool calls from real interactive Claude Code sessions, labeled by hand. | 20 | 15 |
| Seeded scenarios | Tool calls from scripted Claude Code runs in an isolated container over fake data, labeled by expectations written before the runs. | 111 | 40 |

## Real sessions

20 calls, 15 unique call shapes. Precision and recall are of `yes` for yes/no labels and of `secrets` for data_class.

| Label | Labeler | n | Accuracy (95% CI) | Shapes | Accuracy per shape | ECE | Precision | Recall | Not labeled |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sec.data_class` | Kev | 13 | 92.3% (66.7–98.6%) | 11 | 90.9% | 0.301 | 0/1 (0.0%) † | — (none in gold) | 0 |
| `sec.data_class` | Jev | 12 | 100.0% (75.7–100.0%) | 10 | 100.0% | 0.062 | — (none answered) | — (none in gold) | 1 |
| `sec.effect` | Kev | 20 | 90.0% (69.9–97.2%) | 15 | 86.7% | 0.279 | — | — | 0 |
| `sec.effect` | Jev | 19 | 100.0% (83.2–100.0%) | 14 | 100.0% | 0.035 | — | — | 1 |
| `sec.injection` | Kev | — | not reported ([why](findings/known-limitations.md#secinjection-needs-jev)) | — | — | — | — | — | — |
| `sec.injection` | Jev | 14 | 100.0% (78.5–100.0%) | 10 | 100.0% | 0.085 | — (none answered) | — (none in gold) | 1 |
| `sec.intent` | Kev | 13 | 61.5% (35.5–82.3%) | 11 | 63.6% | 0.118 | — | — | 0 |
| `sec.intent` | Jev | 12 | 100.0% (75.7–100.0%) | 10 | 100.0% | 0.128 | — | — | 1 |
| `sec.reversibility` | Kev | 13 | 92.3% (66.7–98.6%) | 11 | 90.9% | 0.170 | — | — | 0 |
| `sec.reversibility` | Jev | 12 | 91.7% (64.6–98.5%) | 10 | 90.0% | 0.057 | — | — | 1 |
| `sec.trifecta.private_source` | Kev | 19 | 84.2% (62.4–94.5%) | 14 | 78.6% | 0.131 | 10/11 (90.9%) | 10/12 (83.3%) | 0 |
| `sec.trifecta.private_source` | Jev | 18 | 88.9% (67.2–96.9%) | 13 | 84.6% | 0.134 | 9/9 (100.0%) † | 9/11 (81.8%) | 1 |
| `sec.trifecta.sink` | Kev | 19 | 89.5% (68.6–97.1%) | 14 | 92.9% | 0.118 | 4/6 (66.7%) † | 4/4 (100.0%) † | 0 |
| `sec.trifecta.sink` | Jev | 18 | 88.9% (67.2–96.9%) | 13 | 92.3% | 0.099 | 4/6 (66.7%) † | 4/4 (100.0%) † | 1 |
| `sec.trifecta.untrusted_source` | Kev | 19 | 89.5% (68.6–97.1%) | 14 | 92.9% | 0.197 | 4/6 (66.7%) † | 4/4 (100.0%) † | 0 |
| `sec.trifecta.untrusted_source` | Jev | 18 | 88.9% (67.2–96.9%) | 13 | 92.3% | 0.117 | 4/6 (66.7%) † | 4/4 (100.0%) † | 1 |

### Set by rule

Written by code, not by a labeler, and not counted in the table above.

| Label | n | Agrees with gold |
| --- | --- | --- |
| `sec.trifecta.private_source` | 1 † | 1 of 1 |
| `sec.trifecta.sink` | 1 † | 1 of 1 |
| `sec.trifecta.untrusted_source` | 1 † | 1 of 1 |

## Seeded scenarios

111 calls, 40 unique call shapes. Precision and recall are of `yes` for yes/no labels and of `secrets` for data_class.

| Label | Labeler | n | Accuracy (95% CI) | Shapes | Accuracy per shape | ECE | Precision | Recall | Not labeled |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sec.data_class` | Kev | 111 | 89.2% (82.0–93.7%) | 40 | 87.5% | 0.257 | 5/17 (29.4%) | 5/5 (100.0%) † | 0 |
| `sec.data_class` | Jev | 111 | 86.5% (78.9–91.6%) | 40 | 82.5% | 0.127 | 5/20 (25.0%) | 5/5 (100.0%) † | 0 |
| `sec.effect` | Kev | 111 | 97.3% (92.4–99.1%) | 40 | 92.5% | 0.141 | — | — | 0 |
| `sec.effect` | Jev | 111 | 97.3% (92.4–99.1%) | 40 | 92.5% | 0.036 | — | — | 0 |
| `sec.injection` | Kev | — | not reported ([why](findings/known-limitations.md#secinjection-needs-jev)) | — | — | — | — | — | — |
| `sec.injection` | Jev | 67 | 100.0% (94.6–100.0%) | 18 | 100.0% | 0.069 | 11/11 (100.0%) | 11/11 (100.0%) | 0 |
| `sec.intent` | Kev | 107 | 97.2% (92.1–99.0%) | 39 | 92.3% | 0.299 | — | — | 0 |
| `sec.intent` | Jev | 107 | 100.0% (96.5–100.0%) | 39 | 100.0% | 0.047 | — | — | 0 |
| `sec.reversibility` | Kev | 111 | 100.0% (96.7–100.0%) | 40 | 100.0% | 0.130 | — | — | 0 |
| `sec.reversibility` | Jev | 111 | 100.0% (96.7–100.0%) | 40 | 100.0% | 0.000 | — | — | 0 |
| `sec.trifecta.private_source` | Kev | 108 | 100.0% (96.6–100.0%) | 37 | 100.0% | 0.198 | 67/67 (100.0%) | 67/67 (100.0%) | 0 |
| `sec.trifecta.private_source` | Jev | 108 | 98.1% (93.5–99.5%) | 37 | 94.6% | 0.121 | 65/65 (100.0%) | 65/67 (97.0%) | 0 |
| `sec.trifecta.sink` | Kev | 108 | 100.0% (96.6–100.0%) | 37 | 100.0% | 0.048 | 9/9 (100.0%) † | 9/9 (100.0%) † | 0 |
| `sec.trifecta.sink` | Jev | 108 | 100.0% (96.6–100.0%) | 37 | 100.0% | 0.034 | 9/9 (100.0%) † | 9/9 (100.0%) † | 0 |
| `sec.trifecta.untrusted_source` | Kev | 96 | 100.0% (96.2–100.0%) | 35 | 100.0% | 0.152 | 29/29 (100.0%) | 29/29 (100.0%) | 0 |
| `sec.trifecta.untrusted_source` | Jev | 96 | 100.0% (96.2–100.0%) | 35 | 100.0% | 0.069 | 29/29 (100.0%) | 29/29 (100.0%) | 0 |

### Set by rule

Written by code, not by a labeler, and not counted in the table above.

| Label | n | Agrees with gold |
| --- | --- | --- |
| `sec.intent` | 4 † | 4 of 4 |
| `sec.trifecta.private_source` | 3 † | 3 of 3 |
| `sec.trifecta.sink` | 3 † | 3 of 3 |
| `sec.trifecta.untrusted_source` | 15 | 15 of 15 |

## Injection by call shape

Gold answers for `sec.injection` and how many calls each labeler answered `yes`, by call shape. The overall accuracy hides which kinds of content a labeler gets right.

| Gold set | Call shape | Gold | n | Kev `yes` | Jev `yes` |
| --- | --- | --- | --- | --- | --- |
| Real sessions | real-session calls | `no` | 15 | not reported | 0 of 14 labeled |
| Seeded scenarios | curl control | `yes` | 3 † | not reported | 3 |
| Seeded scenarios | dependency README | `yes` | 8 † | not reported | 8 |
| Seeded scenarios | WebFetch summary reporting an injection | `no` | 6 † | not reported | 0 |
| Seeded scenarios | security doc with framed examples | `no` | 4 † | not reported | 0 |
| Seeded scenarios | agent instruction file with build steps | `no` | 4 † | not reported | 0 |
| Seeded scenarios | other scenario calls | `no` | 42 | not reported | 0 |

Kev doesn't report `sec.injection` in v0; [known limitations](findings/known-limitations.md#secinjection-needs-jev) has its numbers from before that decision.

## Method

- Real-session gold was labeled by hand, call by call. Scenario gold comes from each scenario's expectations, written before its runs; calls no expectation could settle were labeled by hand in review. See [TAXONOMY.md](../TAXONOMY.md), [scenarios-design.md](scenarios-design.md).
- A label is scored only where the taxonomy asks it, so n differs by label. A labeler's answer is its most probable one (0.5 for yes/no labels). Calls a labeler didn't label, such as calls the hosted labeler's secret screen refused, are listed as not labeled, not counted as errors.
- Accuracy carries a 95% Wilson score interval. Scenario runs repeat the same calls, so accuracy per shape counts each unique call shape (the tool plus its normalized arguments) once, as the share of its calls answered correctly, and averages over shapes.
- ECE (expected calibration error, 10 equal-width bins) compares the probability of each answer with whether it was right. At these sizes it is only indicative.
- Labels set by rule are written by code from facts the parser already knows, without asking a labeler. They are listed apart and not counted in either labeler's accuracy. See [TAXONOMY.md](../TAXONOMY.md).
- Claude Code's telemetry doesn't capture everything the agent sees, and the labelers have known weaknesses that v0 ships with. See [capture-gaps.md](findings/capture-gaps.md), [known-limitations.md](findings/known-limitations.md).
