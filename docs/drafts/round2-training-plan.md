# Round 2 training plan, track 2, version 1

Status: **approved by the owner; groups A and B built; G2 and FelonyDoS wait on the owner.** Version 1 has no new authors; authors 2 and 3
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

## Owner decisions

1. **AgentDojo in group B:** `tool_knowledge` with its tool-list slot filled, plus
   the DoS strings at the pinned revision.
2. **Group B frames:** the owner's G1–G8 (round2-frames.txt, local). Each must
   delimit the whole `{quoted_attack}`, use it exactly once, and share no 6-word
   run with F1–F6.
3. **Calibration:** 10 group-A sets chosen with a fixed seed, stratified by source
   and carrier, and committed before any run.
4. **Cap:** $10 for all of round 2 v1. Kev-9B is skipped.

## Group A as built

`scenarios/round2a_mcp` and `scenarios/round2a_web`, 30 sets each (r2am01–30,
r2aw01–30), one run per set.

- **Shape and labels:** as batch 2, with the labels the owner approved for batch 2.
  The canary tool and file names differ from batch 2's.
- **Overlays:** the sets are `split: local`, a new spec value. Overlays live in
  `fixtures/local/<id>/` (git-ignored), and their digests are in
  `scenarios/round2a.sha256`.
- **Pools** (`scenarios/round2/payloads/manifest.jsonl`: provenance, digests and
  edits, no text):
  - **Sizes:** LLMail 103 and InjecAgent 40, from rows batch 2 didn't pool.
  - **Dropped:** any string sharing an 8-word run with batch-2 text, with batch-1
    held-out text, or with a string already pooled.
  - **Names:** 44 more name swaps were reviewed, among them a registrar's name and
    street address, person names and product tool names.
- **Payloads (120):** LLMail 50, InjecAgent 30, and the batch-1 generator 40 (a
  third). The generator payloads use batch-1 wording drawn past batch 1's
  positions; any draw sharing 8 words with batch-2 or batch-1 held-out text is
  skipped.
- **Look-alikes (120):**
  - **80 framed quotes** (LLMail 53, InjecAgent 10, generator 17). They use eight
    generator-written frames, A1–A8: a fence, BEGIN/END markers, `> ` on every line,
    inline quotes (single-line strings only), triple quotes, tags and markers.
  - **A frame is used only if it encloses its string:** a string containing the
    frame's own delimiter goes in another frame.
  - **Known limitation of v1:** the group A frames are generator-written.
  - **40 bank look-alikes** (written for this generator): legitimate procedure
    steps that fetch or save (14), tool hints (13), and text about injection (13).
- **Checks:** within a set, no other carrier shares 8 words with a payload, and no
  look-alike holds the set's canary.
  - 30 carriers have a stand-in person name or an account-like number, so they go
    to review for data_class.
- **Calibration** (`scenarios/round2/calibration.json`, seed 20261119):
  - **Chosen sets:** r2am11, r2am19, r2am21, r2am25, r2am26, r2aw09, r2aw17,
    r2aw20, r2aw22 and r2aw30.
  - **Rule:** 5 sets per half, closest over 20,000 draws to the group's mix of
    payload sources, payload carriers and look-alike kinds.
  - **Mix:** LLMail 8, InjecAgent 5 and generator 7 payloads; 14 framed and 6 bank
    look-alikes.

## Group B as built

`scenarios/round2b_mcp` and `scenarios/round2b_web`, 12 sets each (r2bm01–12,
r2bw01–12), 2 runs per set. They are held out: `split: test`, with overlays in
`fixtures/heldout/` and digests in `scenarios/round2b.sha256`.

- **Frames:** the owner's frames, converted with whitespace changes only.
  - **G2 is excluded:** its text has no `{quoted_attack}` (the code block it
    introduces is missing).
  - **G1 and G3–G8** each delimit the whole string, use the slot once, and share no
    6-word run with F1–F6 or A1–A8.
  - **G8's `> > > ` prefix** goes on every line of a multi-line string.
  - **G5** stays as written (commented START/END lines inside code).
  - **Delimiter collisions:** a string containing a frame's own delimiter goes to
    another frame.
- **Pools:**
  - **BIPIA:** 70 rows batch 2 didn't pool.
  - **AgentDojo `tool_knowledge`:** 22 strings. The tool sequence is read
    statically from each task's ground truth, and 5 tasks with non-literal
    arguments are dropped.
  - **AgentDojo DoS:** 4 strings, fetched at the pinned revision. FelonyDoS is left
    out pending the owner: it claims to contain links to abuse material.
  - **Overlap:** no string shares 8 words with group A.
- **Deviation from "1 BIPIA + 1 AgentDojo framed quote per set":** every AgentDojo
  string shares its template's sentences. In a set holding both an AgentDojo payload
  and an AgentDojo framed quote, the quote would show 8 words of the payload and go
  to review. There are two set types instead, in a 2:1 ratio in each half:
  - **X (16 sets):** AgentDojo + BIPIA payloads; two BIPIA framed quotes.
  - **Z (8 sets):** two BIPIA payloads; two AgentDojo framed quotes.
- **Totals:**
  - **Payloads:** AgentDojo 16, BIPIA 32.
  - **Framed quotes:** AgentDojo 16, BIPIA 32.
  - **Reuse:** 6 AgentDojo strings appear once as a payload and once as a framed
    quote, in different sets. The BIPIA strings are all distinct.
  - **Auto-resolved calls over 2 runs (MCP and curl):** 96 yes (AgentDojo 32,
    BIPIA 64) and 96 framed-quote no (AgentDojo 32, BIPIA 64).
- **data_class review:** 32 carriers hold a stand-in name or an account-like number
  (AgentDojo's `{user}` stand-in is a person name).
- **Still to write:** split v4 with `round2_test`. That is `eval/` code; until then
  group B gold is kept in its own file.
