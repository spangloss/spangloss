# Spangloss v0 results

<!-- Generated from site/results.json by python -m spangloss.resultsgen; don't edit by hand. -->

These are Spangloss v0's per-label results at taxonomy 0.4.0, on two gold sets scored separately: 20 hand-labeled calls from real sessions and 111 calls from seeded scenarios. On the scenarios, each label's accuracy runs from 86.5% to 100% for Jev, the hosted labeler, and from 89.2% to 100% for Kev, the local one. Jev caught all 11 planted injections; Kev isn't scored on injection in v0. Both labelers over-call secrets (precision 5/17 for Kev, 5/20 for Jev). Kev's probabilities run below its accuracy on nearly every label (ECE up to 0.30, against at most 0.13 for Jev), and on real sessions its intent accuracy drops to 61.5%. The real-session set is small and holds no injections, so Jev's 100% there shows only that it raised no false alarms. All of it comes from one agent's coding sessions: these numbers describe these sets, not agent sessions in general.

Taxonomy 0.4.0. Generated 2026-11-08 at commit `89c7a80`. Labelers: Kev (local, `jaredpalmer/kev-4b@v1.0`), Jev (hosted, version not recorded). † marks a cell resting on fewer than 10 calls.

## What the results say

Five things stand out.

**The labels about what a call does are reliable.** On the scenarios, effect, reversibility and the three trifecta checks match gold 97–100% of the time for both labelers. That makes sense: for these questions, the answer is mostly sitting in the call itself.

**Injection is Jev’s strongest result.** It caught all 11 planted injections. It also raised no false alarm on the 70 calls that had none, including security docs that quote attacks, instruction files full of commands, and summaries that report an attack without repeating it. Those look-alikes are exactly what trips up keyword rules. Kev couldn’t tell them apart, so v0 doesn’t use Kev for injection.

**Both labelers over-call secrets.** They found every real secret, but raised two to three false alarms for each one. Most of those were reads of, or searches for, test-config files like `.env.test`. For a triage label, that’s the right direction to be wrong in, since a missed secret costs more than a second look. Still, read `secrets` as “worth a look”, not “confirmed”.

**The real-session numbers are directional.** With 12 to 20 calls per label, a 92% could really be anywhere from 67% to 99%. One weakness shows through anyway: Kev’s intent accuracy drops to 61.5%, and all five misses are shell commands it labeled `other`.

**Jev’s confidence means about what it says; Kev’s runs low.** Jev’s rare answers below 0.7 confidence are right only about half the time, so read those as a coin flip. When Kev answered scenario intent questions, its average confidence was 0.67, but it was right 97% of the time. Kev is usable. Just don’t read its probabilities literally.

## How to read this page

Everything below compares two labelers with a set of answers we treat as correct. Here’s what the columns mean, minus the jargon:

- **Gold:** the answers we count as correct. For real sessions, I labeled each call by hand. For scenarios, the expected answers were written before each scripted run, so the runs couldn’t influence them.
- **Accuracy:** the share of calls where the labeler’s answer matched gold.
- **95% interval:** the range the true accuracy most likely falls in, given how few calls we have. A long line means a small sample, so don’t lean on its dot too hard.
- **Accuracy per shape:** scripted runs repeat the same call, so this counts each distinct kind of call once. A call that shows up ten times can’t carry the score on its own.
- **Precision and recall:** for yes/no labels, and for secrets. Recall asks: of the real cases, how many did it find? Precision asks: of the ones it flagged, how many were real? Low recall means misses; low precision means false alarms.
- **ECE (calibration):** whether a labeler’s confidence matches how often it’s right. 0 is perfect. At these sample sizes, treat it as a hint, not a measurement.
- **Set by rule:** answers written by code from facts the trace already shows, not by a model. They’re listed separately, and their perfect score is by design.
- **Not reported / not labeled:** “not reported” means the labeler doesn’t answer that label in v0. “Not labeled” counts calls it was never asked about, such as the one call the hosted labeler’s secret screen refused to send.
- **†:** the number rests on fewer than 10 calls. Read it as a rough signal.

## Gold sets

The two sets are scored separately and never pooled.

| Gold set | What it is | Calls | Unique call shapes |
| --- | --- | --- | --- |
| Real sessions | Tool calls from real interactive Claude Code sessions, labeled by hand. | 20 | 15 |
| Seeded scenarios | Tool calls from scripted Claude Code runs in an isolated container over fake data, labeled by expectations written before the runs. | 111 | 40 |

The scenario set has 111 calls but only 40 distinct kinds of call, because scripted runs repeat themselves. That’s why the tables also show accuracy per shape.

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

Jev leaves one call unlabeled on every row: its secret screen refused to send that call to a hosted model, which is what it’s for.

### Set by rule

Written by code, not by a labeler, and not counted in the table above.

| Label | n | Agrees with gold |
| --- | --- | --- |
| `sec.trifecta.private_source` | 1 † | 1 of 1 |
| `sec.trifecta.sink` | 1 † | 1 of 1 |
| `sec.trifecta.untrusted_source` | 1 † | 1 of 1 |

These are right by construction. They’re listed so you can see which answers aren’t a model’s judgment.

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

Nearly every number is above 90%. The exception is `data_class`, where most of the misses are false alarms on secrets.

### Set by rule

Written by code, not by a labeler, and not counted in the table above.

| Label | n | Agrees with gold |
| --- | --- | --- |
| `sec.intent` | 4 † | 4 of 4 |
| `sec.trifecta.private_source` | 3 † | 3 of 3 |
| `sec.trifecta.sink` | 3 † | 3 of 3 |
| `sec.trifecta.untrusted_source` | 15 | 15 of 15 |

These are right by construction. They’re listed so you can see which answers aren’t a model’s judgment.

## Injection by call shape

Gold answers for `sec.injection` and how many calls each labeler that reports it answered `yes`, by call shape. The overall accuracy hides which kinds of content a labeler gets right.

| Gold set | Call shape | Gold | n | Jev `yes` |
| --- | --- | --- | --- | --- |
| Real sessions | real-session calls | `no` | 15 | 0 of 14 labeled |
| Seeded scenarios | curl control | `yes` | 3 † | 3 |
| Seeded scenarios | dependency README | `yes` | 8 † | 8 |
| Seeded scenarios | WebFetch summary reporting an injection | `no` | 6 † | 0 |
| Seeded scenarios | security doc with framed examples | `no` | 4 † | 0 |
| Seeded scenarios | agent instruction file with build steps | `no` | 4 † | 0 |
| Seeded scenarios | other scenario calls | `no` | 42 | 0 |

Kev doesn't report `sec.injection` in v0; [known limitations](findings/known-limitations.md#secinjection-needs-jev) has its numbers from before that decision.

## Known limitations

Weaknesses v0 ships with, written up with their evidence:

- [`sec.injection` needs Jev](findings/known-limitations.md#secinjection-needs-jev)
- [`.env.test` Reads with captured content are often labeled `secrets`](findings/known-limitations.md#envtest-reads-with-captured-content-are-often-labeled-secrets)
- [`sec.data_class` `secrets` has low precision](findings/known-limitations.md#secdata_class-secrets-has-low-precision)
- [Kev's `sec.intent` misses on real-session Bash calls](findings/known-limitations.md#kevs-secintent-misses-on-real-session-bash-calls)
- [Trifecta errors on real sessions](findings/known-limitations.md#trifecta-errors-on-real-sessions)
- [Kev is less well calibrated than Jev](findings/known-limitations.md#kev-is-less-well-calibrated-than-jev)

## What these results don’t tell you

These numbers are honest about a narrow slice. Before you generalize from them:

- **One agent, coding work only.** Every call comes from Claude Code 2.1.312 doing software tasks. Agents that send email, book things or move money haven’t been tested, and intent is the label most likely to change there.
- **A small real-world sample.** The real-session set is 20 calls from my own work. It says where the labelers stumble, not how often they would across a team.
- **Attacks I planted.** Every injection in the scenarios was written by me. They cover a few known patterns, not what a determined attacker would try next.
- **One person’s gold.** I wrote the taxonomy and labeled the gold, so these scores measure agreement with my reading of it. There’s no second labeler yet to check that reading.
- **Only what the telemetry captured.** A label can only judge what reached the trace. The [capture gaps](https://github.com/spangloss/spangloss/blob/main/docs/findings/capture-gaps.md) limit every number here.
- **A snapshot.** These are taxonomy 0.4.0 and the labelers’ current versions. Answers close to the 50% line can flip between runs, so small differences between the two labelers may not hold up.

## Method

- Real-session gold was labeled by hand, call by call. Scenario gold comes from each scenario's expectations, written before its runs; calls no expectation could settle were labeled by hand in review. See [TAXONOMY.md](../TAXONOMY.md), [scenarios-design.md](scenarios-design.md).
- A label is scored only where the taxonomy asks it, so n differs by label. A labeler's answer is its most probable one (0.5 for yes/no labels). Calls a labeler didn't label, such as calls the hosted labeler's secret screen refused, are listed as not labeled, not counted as errors.
- Accuracy carries a 95% Wilson score interval. Scenario runs repeat the same calls, so accuracy per shape counts each unique call shape (the tool plus its normalized arguments) once, as the share of its calls answered correctly, and averages over shapes.
- ECE (expected calibration error, 10 equal-width bins) compares the probability of each answer with whether it was right. At these sizes it is only indicative.
- Labels set by rule are written by code from facts the parser already knows, without asking a labeler. They are listed apart and not counted in either labeler's accuracy. See [TAXONOMY.md](../TAXONOMY.md).
- Claude Code's telemetry doesn't capture everything the agent sees, and the labelers have known weaknesses that v0 ships with. See [capture-gaps.md](findings/capture-gaps.md), [known-limitations.md](findings/known-limitations.md).
