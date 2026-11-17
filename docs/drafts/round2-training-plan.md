# Round 2 training plan, track 2 (draft; plan only, no spend)

Status: **draft for owner review.** "Track 2" is read here as the fine-tuned labeler
(the delta-LoRA Kev-4B of docs/drafts/kev-finetune-pilot.md); correct this if it
means something else.

## Why

The pilot model fails batch 2's pre-registered criteria (docs/drafts/batch2-run-notes.md).

- **Where it fails:** recall is 66.7% on hand-written payloads and 76.5% on
  benchmark strings. False positives are 20.8% on hand-written look-alikes and 40%
  on framed quotes.
- **Likely cause:** its training payloads came from one generator (batch 1).
- **Errors that point at narrow training:** misses on fake_step and impersonation,
  and flags that follow frame wording rather than framing.

Round 2 trains on varied sources. Its own test set is separated **by source**, so
the result measures transfer to unseen sources, not to unseen rows of seen ones.

## Hard rules

- **Batch 2 is never training data.** That covers its 24 sets and their calls, the
  owner's hand pieces 001–048, frames F1–F6, and the 48 benchmark strings placed in
  batch 2.
  - It's also not used for model selection or calibration.
  - Split v3 (`batch2_eval`) and `build_split`'s refusal already enforce this for
    gold.
  - The export adds a text check: reject any training record sharing an 8-word run
    with batch-2 payload, look-alike or frame text.
- **Batch 2 stays the pre-registered benchmark.** After round 2, its numbers are
  reported twice: for sources round 2 trained on ("in-source"), and for sources it
  didn't ("out-of-source"). Rows differ either way.
- **Fixed before any run:** the round-2 test set, its split by source, and the
  success criteria.
- **No spend** until the owner approves each step.

## Sources

| Group | Payloads | Look-alikes | Use |
|---|---|---|---|
| **A** | LLMail-Inject and InjecAgent (rows not in batch 2), the batch-1 generator, author 2's hand pieces | author 2's look-alikes; frames by author 2; benign procedure pages with real setup URLs | train |
| **B** | BIPIA attack strings, AgentDojo (templates not in batch 2), author 3's hand pieces | author 3's look-alikes and frames | round-2 test (held out by source) |

- **Fold 2 (optional):** swap the groups and train again, to see whether transfer
  depends on which sources come first. It doubles training cost.
- **Authors:** the owner's batch-2 pieces stay out of both groups; they belong to
  batch 2. Authors 2 and 3 get the same brief (docs/drafts/batch2-payload-brief.md)
  and never see each other's pieces.
- **AgentDojo:** its templates repeat whole sentences, so it splits by template,
  not by row.

## What batch 2 says to add

- **Procedure steps, both ways:**
  - legitimate setup steps that fetch or save things (gold no, as in 38 and 47);
  - steps that send data or write values for no purpose of the page (gold yes).

  This depends on the audit's decision about 08 and 18.
- **Framing variety:** at least 20 frames from two or more authors, with the string
  bare, quoted, fenced and between markers, and single- and multi-line. Labels
  follow TAXONOMY's quoted-payload note, after the owner rules on undelimited
  multi-line quotes.
- **Carriers:** MCP results, web pages (curl), and Bash output as in batch 1.
  WebFetch summaries stay out of training until their review rule settles.
- **Balance:** about 1:1 payloads to look-alikes, plus clean carriers. Keep the
  batch-1 generator's share at or below a third of the payloads.

## Size (to confirm)

- **Group A:** about 600 records with injection labels, plus the seven labels on
  every call. Roughly 60 runs of 5–10 carrier calls.
- **Group B test:** about 300 calls (roughly 30 runs), at least 60 injection-yes
  calls per source group.
- **Calibration:** a partition of group A only. The temperature is fitted locally,
  with the `fit-temperature` fix.

## Steps

1. Owner decisions:
   - the batch-2 audit;
   - the group assignment above;
   - authors 2 and 3;
   - the round-2 success criteria, written before any run.
2. Benchmark build for both groups:
   - the 8-word check against batch 1 and batch 2;
   - name swaps, recorded in the edit manifest, as before.
3. Fixtures for groups A and B (generator plus authors), with heldout digests for B.
4. Runs, in this order:
   - pilot, one per carrier type;
   - group A;
   - group B.
5. Check into separate gold files per group, then owner review.
6. Train on Modal, delta LoRA as in the pilot, with `--max_state` sized to the
   longest record.
7. Evaluate:
   - local, at T 1.0 and at the fitted T;
   - on group B (the round-2 criteria), batch 2 (in-source and out-of-source), v0
     test and batch1_test;
   - a forgetting check.

## Spend estimate (not approved)

| Item | Estimate |
|---|---|
| Group A runs (~60) | $2.5–4 |
| Group B runs (~30) | $1.5–2 |
| Training (H100, one fold) | ~$1–3 |
| Calibration and finish | ~$0.2–0.5 |
| Kev-9B comparison (L40S, warm start) | ~$0.3–0.8 |
| Jev labels | cents |
| **One fold** | **about $6–10** (fold 2 adds about $3–5) |
