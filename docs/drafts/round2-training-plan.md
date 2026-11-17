# Round 2 training plan, track 2, version 1 (draft; nothing built, no spend)

Status: **draft for owner review.** Version 1 has no new authors; authors 2 and 3
join in a later round. "Track 2" is read here as the fine-tuned labeler (the
delta-LoRA Kev-4B of docs/drafts/kev-finetune-pilot.md).

## Why

The pilot model fails batch 2's pre-registered criteria, on both pre-registered and
audited gold (docs/drafts/batch2-run-notes.md, docs/drafts/batch2-gold-audit.md). Its
training payloads came from one generator.

Round 2 trains on varied benchmark sources and tests on sources it never trained
on. Its result therefore measures transfer to unseen sources.

## Success criteria (owner, 2026-11-17; recorded before any round-2 run)

On group B (the round-2 test), the fine-tuned 4B:

1. **Recall:** injection recall within 10 points of Jev's.
2. **False positives:** within 10 points of Jev's rate on look-alikes and framed
   quotes.
3. **Calibration:** injection ECE ≤ 0.05 at a temperature fitted locally (on group
   A's calibration partition, served as it will be used).
4. **No forgetting:** the seven non-injection labels within 1 point of Kev-4B's
   accuracy on the v0 test.

Batch 2 is reported too, never trained on, split into:
- **in-source:** strings from LLMail-Inject and InjecAgent, which group A trains on;
- **out-of-source:** BIPIA, AgentDojo and the owner's hand-written pieces.

These criteria are fixed. They are not changed after any round-2 result is seen.

## Hard rules

- **Batch 2 is never training data.** That covers its 24 sets and calls, hand pieces
  001–048, frames F1–F6, and the 80 benchmark strings pooled for batch 2 (not only
  the 48 placed).
  - It isn't used for model selection or calibration either.
  - Split v3 enforces this for gold.
  - The export adds a text check: reject any training record sharing an 8-word run
    with batch-2 payload, look-alike or frame text.
- **Group B is never training data**, enforced the same way, with a new
  `round2_test` slice in a split v4.
- **Frames delimit the whole quoted string** (owner, 2026-11-17): a fence, quotes
  around all of it, begin/end markers, or a block quote on every line.
  - **Group A** may use generator-written frames. This is noted in the run notes as
    a known limitation of v1.
  - **Group B** frames don't come from the generator (see Group B frames below).

## Groups

| | Group A (train) | Group B (test, held out by source) |
|---|---|---|
| Payloads | LLMail-Inject rows not in batch 2's pool; InjecAgent rows not in the pool; the batch-1 generator, at most a third of payloads | BIPIA attack strings not in batch 2's pool; AgentDojo templates not used in batch 2 |
| Look-alikes | framed quotes (generator frames) around group-A strings; generator look-alikes (tool hints, text about injection); legitimate procedure steps that fetch or save things | framed quotes (non-generator frames, below) around group-B strings |
| Available after batch 2 | LLMail 678 rows; InjecAgent 42 rows (dh 20, ds 22) | BIPIA 130 rows in 29 categories (45 rows in the 9 categories batch 2 didn't use); AgentDojo: see below |

**AgentDojo in group B.**
- **The problem:** all five fixed-string templates batch 2 parsed (direct,
  ignore_previous, system_message, injecagent, important_instructions) were used.
  The `important_instructions_*` name variants reuse the same string, so they aren't
  new templates.
- **Proposal:**
  1. `tool_knowledge`, the one parsed template batch 2 skipped. Its `{tools}` slot
     gets the stand-in tool names. That's about 27 strings, one per injection goal.
     Its wording overlaps `important_instructions`, but that matters only against
     batch 2, not against group A, which has no AgentDojo.
  2. The DoS attacks (`dos_attacks.py`): a few fixed strings with no goal (gold yes,
     no canary). This needs a fresh download of that one file at the pinned
     revision. No spend.
- **Owner decision:** accept these, or treat AgentDojo in group B as "rows not in
  batch 2's pool" under the batch-2 templates instead (115 rows). The latter is
  weaker, because those templates aren't new.

**Group B frames.** These must not come from the generator.
1. **Recommended: the owner writes 6–8 new frames (G1–G8),** each delimiting the
   whole string and introducing it as an example. They're kept local and never
   trained on, and checked against F1–F6 and the generator banks for 8-word
   overlaps.
2. **Fallback:** reuse F1–F6 in group B with delimiters added (markup only;
   F2 and F4 need it). Batch 2 is evaluation-only, so this trains on nothing, but
   group B's framed false-positive rate then shares frames with batch 2 and isn't
   independent.
3. **Slower:** frames adapted from permissively licensed security documentation,
   after a licence check.

## Sizes

**Group A (train): 60 sets, 1 run each, 60 runs** (30 MCP, 30 web). One run per set:
distinct sets add more than repeats do.

| Per set | Count | Mix |
|---|---|---|
| Payloads | 2 | LLMail ~50, InjecAgent ~30, generator ≤ 40 (of 120) |
| Look-alikes | 2 | framed quotes ~80 (generator frames), generator look-alikes and legitimate procedure steps ~40 |
| Clean | 1 | |

- **Auto-resolved training calls:** about 300 (MCP and curl): ~120 yes, ~120
  look-alike no, ~60 clean. WebFetch calls aren't used for training.
- **The pilot's batch-1 training records stay in.** Round 2 adds to them.
- **Calibration:** 10 of the 60 sets (by set, fixed before the runs), never trained
  on.

**Group B (test): 24 sets, 2 runs each, 48 runs** (12 MCP, 12 web), shaped like batch 2.

| Per set | Count | Mix |
|---|---|---|
| Payloads | 2 | 1 BIPIA, 1 AgentDojo |
| Framed quotes | 2 | 1 BIPIA, 1 AgentDojo, in G-frames |
| Clean | 1 | |

- **Auto-resolved gold:** about 96 yes (48 per source), 96 framed no and 48 clean.
  About 120 WebFetch calls go to review.
- **Strings:** about 24 AgentDojo strings serve as payloads. Each also appears once
  as a framed quote in a different set, since there are only about 27–32 strings.
  BIPIA has enough rows for distinct strings everywhere.
- **No hand-written look-alikes in v1,** so group B's false-positive criterion
  rests on framed quotes alone.

## Steps (each needs owner approval)

1. **Owner decisions:**
   - AgentDojo for group B;
   - group B frames (G1–G8 or the fallback);
   - the 10 calibration sets.
2. **Benchmark build:**
   - pools for groups A and B, with batch 2's 80 pooled rows excluded;
   - the 8-word check against batch 1, batch 2 and the other group;
   - name swaps recorded in the edit manifest.
3. **Fixtures:**
   - group A (generator frames, noted);
   - group B (G-frames), with digests in a heldout file and split v4 with
     `round2_test`.
4. **Runs:** a pilot per carrier type, then group A, then group B. Spend is
   reported after each command, with the remainder passed on explicitly.
5. **Check and review:** check into separate gold files per group, then owner
   review.
6. **Train on Modal:** delta LoRA as in the pilot. `--max_state` is sized to the
   longest record, and the `kev_modal.py` Triton pin is kept.
7. **Evaluate locally:**
   - fit the temperature on group A's calibration sets;
   - score group B against the criteria;
   - score batch 2 in-source and out-of-source;
   - seven labels on the v0 test against Kev-4B;
   - batch1_test.

## Spend estimate (not approved)

| Item | Basis | Estimate |
|---|---|---|
| Pilots (2 runs) | batch 2: $0.02 MCP, $0.04 web | $0.06 |
| Group A runs (60) | 30 × $0.02 + 30 × $0.04 | $1.8 |
| Group B runs (48) | 24 × $0.02 + 24 × $0.04 | $1.4 |
| Training (H100) | pilot $0.90; about twice the records | $1.5–3 |
| Calibration and finish | pilot $0.20 | $0.2–0.5 |
| Jev labels | batch 2 | cents |
| Fine-tuned 4B and Kev-4B labels, v0 test | local | $0 |
| Kev-9B comparison (optional) | batch 2 labeling deploy, pinned and warm | $0.2–0.4 |
| **Total** | | **about $5–7.5 (cap proposal: $10)** |
