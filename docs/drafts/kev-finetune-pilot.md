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

## Results

(after the run)
