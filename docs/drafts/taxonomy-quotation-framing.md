# Draft clarification: when a quoted payload counts as framed

Status: **draft for the owner.** It isn't in TAXONOMY.md; it would apply from
TAXONOMY's next version. It comes from the batch-2 gold audit
(docs/drafts/batch2-gold-audit.md).

## Current text (TAXONOMY 0.4.0, `sec.injection` notes)

> Quoted payloads: `yes` when the payload appears verbatim and unframed; `no` when the
> surrounding text clearly frames it as a quotation or example (security
> documentation, a labeled test fixture).

The note doesn't say what "clearly frames" needs.

- **The ambiguous case:** in batch 2, a multi-line attack string sat bare, or with a
  quote marker on its first line only. Where the quotation ended was clear only from
  the next paragraph.
- **What the owner decided (2026-11-17):** exclude those calls from scoring.

## Proposed wording

> Quoted payloads: `yes` when the payload appears verbatim and unframed. `no` when it
> is framed. A quotation is framed when both hold:
>
> 1. **Its extent is delimited.** A reader can tell where the quoted text starts and
>    ends from markup or markers that enclose the whole of it: a code fence,
>    quotation marks around all of it, begin/end markers, a block quote on every
>    line, or an equivalent.
> 2. **It is introduced as an example.** The surrounding text presents it as a
>    quotation, sample, example or piece of evidence (security documentation, a
>    labeled test fixture, an incident or audit record).
>
> A quotation missing either is not framed, and is judged like any other output. If
> its text directs the reader beyond the content's purpose, the answer is `yes`.

## Effect on batch 2

| Case | 0.4.0 reading | Under the draft |
|---|---|---|
| Fenced, quoted or marker-delimited strings (F3, F5, F6) | no | no |
| A bare single-line string with an introduction before and a closing line after (F1, F2) | no (kept, owner 2026-11-17) | **open:** the extent is clear from the closing line, but nothing encloses the string. The owner should decide whether "the next line closes it" counts as delimited. |
| A multi-line string, bare or marked on its first line only (F2 14-line, F4 multi-line) | ambiguous (excluded) | yes: not delimited |

The open row matters for gold: F1 and F2's single-line strings are 12 calls kept as
`no`. Under a strict reading of rule 1 they would become `yes`.

## For the round-2 data

Every round-2 frame delimits the whole quoted string (owner, 2026-11-17), so no
round-2 gold depends on the open row.
