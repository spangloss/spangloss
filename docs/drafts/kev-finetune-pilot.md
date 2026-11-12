# Kev-4B fine-tune pilot: run notes (draft)

Status: **criteria fixed before training** (2026-11-12). Results are added below them
after the run, pass or fail. The question: does `sec.injection` become learnable for
Kev-4B, before round 2 is designed?

## Success criteria (owner, set before training)

1. **Injection is learnable** if, on batch1_test and on the v0 scenario test
   separately, the fine-tuned 4B beats Kev-9B's injection accuracy (71.9% and 74.5%)
   by at least 10 points, i.e. ≥ 81.9% and ≥ 84.5%, with no more than 2 hard-negative
   false positives in each.
2. **No regression:** the seven other labels stay within 1 point of Kev-4B on the v0
   scenario test (Kev-4B: 405/413, 98.1%), and secrets recall doesn't drop (Kev-4B: 5/5
   on the scenario gold calls whose `sec.data_class` is `secrets`).

Also reported: the args-only labels on test calls whose state does not occur in the
training records, next to the full numbers.

## Data (train side only)

- **Source:** scenario gold calls only: batch-1 train sets (317 calls) and the v1 train
  side of the v0 scenario gold (52 calls). batch1_test, the v0 test and real sessions
  stay on this machine and are used only for local evaluation.
- **Records:** built by running `label_call` against a recording backend that answers
  from gold, so each record is the request spangloss sends: the same evidence as
  `state`, the same question specs, the same 2,000-character output chunks.
- **data_class:** left out of the training records (train has no sensitive positive);
  evaluated locally.
- **Chunked injection, gold yes:** a chunk that contains the whole payload (located by
  the payload's token sequence, which survives comment, string and log-line wrapping)
  is yes; a chunk with no part of it is no; a chunk holding part of it is dropped.
  Train: 5 contain, 9 clear, 2 straddle (dropped). batch1_test: 1, 1, 1 (eval only).
- **Split:** by fixture set (batch 1) or run (v0), stratified by family, 70/15/15, seed
  20261112; calibration and development records whose state also occurs in train are
  dropped (34 and 33).

| Partition | Records | Injection yes / no |
|---|---|---|
| train | 422 | 37 / 156 |
| calibration | 97 | 9 / 42 |
| development | 84 | 9 / 36 |

## Training

`kev_modal.py::train` from the local `spangloss/max-state` kev branch (with
`triton==3.8.0` pinned), `--init-from jaredpalmer/kev-4b@v1.0`, 1 epoch, the
checkpoint's recipe lr, 2,000 public replay records, `--max_state 1024`,
`p_none_pair 0.25`, seed 0, forgetting check on 300 public records, baseline scored on
development. Temperature fitted on calibration only. H100, `--timeout 2400` (cost
bound $2.63; pilot cap $3).

## Evaluation

Local only: the checkpoint is pulled and served with `kev.serve` on port 8010; train-dev
(development calls), batch1_test and the v0 scenario test are labeled through
`spangloss label` (chunk combining included) and scored per call, seven labels plus
injection, next to Kev-4B, Kev-9B and Jev. Hard-negative false positives are reported
separately.

## What changed from the plan

- `--max_state 1536` instead of 1024: at 1024 the trainer would have dropped 33 records
  (24 train, 5 of them injection yes); every state fits under 1,406 tokens.
- The run crashed after saving the checkpoint, while scoring calibration:
  `kev_modal.py`'s scorer encodes with Kev's default 384-token state limit, not
  `--max_state`. So no temperature was fitted (served at 1.00; accuracy is unaffected,
  probabilities are uncalibrated) and the skill's dev scoring and public forgetting
  check did not run. Secrets recall was measured locally instead.
- Spend: $0.90 on Modal (training $0.90, validate and pull under $0.01).

## Results (2026-11-12)

Local, per call, through `spangloss label` (chunk combining included). "Seven" is the
seven non-injection labels; hard-negative FP counts injection yes on hard-negative
calls whose gold is no.

| Slice | Model | Seven | Injection | Injection recall | Hard-neg FP |
|---|---|---|---|---|---|
| batch1_test (74 calls) | **Kev-4B-FT** | 514/518 (99.2%) | **56/57 (98.2%)** | 17/17 | 0/9 |
| | Kev-4B | 487/518 (94.0%) | unsupported | — | — |
| | Kev-9B | 492/518 (95.0%) | 41/57 (71.9%) | 7/17 | 0/9 |
| | Jev | 481/518 (92.9%) | 57/57 (100%) | 17/17 | 0/9 |
| v0 scenario test (59) | **Kev-4B-FT** | 409/413 (99.0%) | **47/47 (100%)** | 11/11 | 0/8 |
| | Kev-4B | 405/413 (98.1%) | unsupported | — | — |
| | Kev-9B | 401/413 (97.1%) | 35/47 (74.5%) | 3/11 | 4/8 |
| | Jev | 405/413 (98.1%) | 47/47 (100%) | 11/11 | 0/8 |
| train-dev (53) | Kev-4B-FT | 367/371 (98.9%) | 39/41 (95.1%) | 9/9 | 1/10 |
| | Kev-4B | 353/371 (95.1%) | unsupported | — | — |
| | Kev-9B | 353/371 (95.1%) | 34/41 (82.9%) | 6/9 | 1/10 |
| | Jev | 354/371 (95.4%) | 40/41 (97.6%) | 9/9 | 0/10 |

Args-only labels (effect, intent, reversibility, trifecta) on calls whose args-only
state is not in the training records, next to all calls:

| Slice | Model | All | Unseen state |
|---|---|---|---|
| batch1_test | Kev-4B-FT | 440/444 (99.1%) | 332/336 (98.8%) |
| | Kev-4B | 413/444 (93.0%) | 314/336 (93.5%) |
| v0 scenario test | Kev-4B-FT | 354/354 (100%) | 330/330 (100%) |
| | Kev-4B | 354/354 (100%) | 330/330 (100%) |
| train-dev | Kev-4B-FT | 315/318 (99.1%) | 231/234 (98.7%) |
| | Kev-4B | 301/318 (94.7%) | 219/234 (93.6%) |

Secrets recall (the 5 scenario gold calls labeled secrets, model answers): Kev-4B-FT
5/5, Kev-4B 5/5, Kev-9B 5/5.

**Against the criteria:**

1. Injection learnable: **pass.** batch1_test 98.2% (needs ≥ 81.9%), 0 hard-negative
   FP; v0 scenario test 100% (needs ≥ 84.5%), 0 hard-negative FP.
2. No regression: **pass.** v0 scenario test seven labels 99.0% vs Kev-4B 98.1%
   (within 1 point, above it); secrets recall 5/5, unchanged.

**Caveats.** Every test set shares the train side's scenario templates and the batch-1
generator's wording banks; held-out fixture sets and v0 test calls are new instances,
not new sources. So this shows injection is learnable in distribution, not that it
transfers to real sessions. Small n: 17 and 11 injection-yes calls. No temperature fit
and no public forgetting check (see above).
