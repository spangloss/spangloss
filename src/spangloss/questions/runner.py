"""Ask a tool call's applicable cards and turn the answers into labels.

- Cards are gated by min_basis (TAXONOMY.md, Input basis); unasked labels are omitted.
- Cards that share an evidence set go in one request. Questions in a request share
  one state, so cards that see output and cards that don't go in separate requests.
- Output longer than CHUNK_CHARS is split into overlapping chunks, each asked in its
  own request, and the answers are combined by the most severe answer (Labeling
  rule 3): yes if any chunk says yes, with the highest such p; otherwise no, with
  the lowest chunk p.
- Each label records its chunk count and the largest input token count among its
  requests, as the backend reports it.
- Evidence goes only in the request's state, never in a question.
- A two-step label (data_class) asks step 2 only when step 1 is yes, and only for
  the chunks whose step 1 was yes. It stores p(no) for none, or p(yes) x p(class)
  for a class, taking the most severe class across chunks.
"""

import json
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any

from spangloss.backends.base import (
    Backend,
    Distribution,
    EvidenceTooLong,
    QuestionSpec,
    RequestRejected,
)
from spangloss.parse.record import ParsedToolCall
from spangloss.questions.cards import Card

# About 8,192 tokens at a conservative 3 characters per token: Kev-4B's validated context.
DEFAULT_MAX_EVIDENCE_CHARS = 24_000
NONE_ANSWER = "none"  # a two-step label's answer when step 1 is no

# Kev's answers drift with length (see the TAXONOMY.md length experiment), so long
# output is asked in pieces. The overlap keeps text that straddles a boundary whole
# in at least one chunk, as long as it is no longer than the overlap.
CHUNK_CHARS = 2_000
CHUNK_OVERLAP = 200

# Most severe first, from TAXONOMY.md's severity orders. Used to combine chunks.
SEVERITY: dict[str, tuple[str, ...]] = {
    "sec.effect": ("delete", "execute", "write", "read", "none"),
    "sec.reversibility": ("irreversible", "recoverable", "reversible"),
    "sec.data_class": ("secrets", "phi", "payment", "pii", "none"),
}


@dataclass(frozen=True, slots=True)
class Label:
    span_id: str
    session_id: str | None
    label: str
    answer: str
    p: float
    basis: str
    model: str
    taxonomy: str
    output_truncated: bool
    input_tokens: int | None  # the largest of the requests behind this answer
    chunks: int  # output chunks asked; 1 when output was not split or not seen


@dataclass(frozen=True, slots=True)
class RequestInfo:
    output_truncated: bool
    input_tokens: int | None


@dataclass(frozen=True, slots=True)
class ChunkAnswer:
    call: ParsedToolCall  # the call as asked: its output is this chunk
    answers: dict[str, Distribution]
    request: RequestInfo


@dataclass(frozen=True, slots=True)
class Outcome:
    labels: list[Label]
    skipped: str | None = None  # why nothing was labeled


def label_call(
    call: ParsedToolCall,
    cards: list[Card],
    backend: Backend,
    max_evidence_chars: int = DEFAULT_MAX_EVIDENCE_CHARS,
) -> Outcome:
    if reason := backend.screen(call):
        return Outcome([], skipped=reason)

    first_round = [c for c in cards if c.applies_to(call.basis) and c.step != 2]
    try:
        results = _ask_grouped(call, first_round, backend, max_evidence_chars)
        labels = [
            _label(call, card, results[card.name], backend)
            for card in first_round
            if card.step is None
        ]
        for step1 in (c for c in first_round if c.step == 1):
            step2 = next(c for c in cards if c.label == step1.label and c.step == 2)
            labels.append(
                _two_step_label(
                    call, step1, step2, results[step1.name], backend, max_evidence_chars
                )
            )
    except RequestRejected as e:  # includes EvidenceTooLong
        return Outcome([], skipped=str(e))
    return Outcome(labels)


def question_spec(card: Card) -> QuestionSpec:
    instructions: dict[str, Any] = {"question": card.question}
    if card.notes:
        instructions["notes"] = list(card.notes)
    return QuestionSpec(
        kind=card.kind, instructions=instructions, criteria=card.answers
    )


def split_output(output: str) -> list[tuple[int, str]]:
    """(start offset, text) chunks of CHUNK_CHARS, overlapping by CHUNK_OVERLAP."""
    if len(output) <= CHUNK_CHARS:
        return [(0, output)]
    chunks: list[tuple[int, str]] = []
    start = 0
    while True:
        chunks.append((start, output[start : start + CHUNK_CHARS]))
        if start + CHUNK_CHARS >= len(output):
            return chunks
        start += CHUNK_CHARS - CHUNK_OVERLAP


def build_evidence(
    call: ParsedToolCall, fields: tuple[str, ...], max_chars: int
) -> tuple[dict[str, Any], bool]:
    """The evidence a card may see, cut to max_chars by truncating output only."""
    evidence: dict[str, Any] = {"tool_name": call.tool_name}
    if "args" in fields and call.args:
        evidence["args"] = call.args
    if "output" in fields and call.output is not None:
        evidence["output"] = call.output
    if _size(evidence) <= max_chars:
        return evidence, False
    if "output" not in evidence:
        raise EvidenceTooLong(f"arguments alone exceed {max_chars} characters")

    output = evidence["output"]
    keep = max_chars - _size({**evidence, "output": ""})
    while keep > 0:
        evidence["output"] = (
            output[:keep]
            + f"\n…[output truncated: first {keep} of {len(output)} characters]"
        )
        if _size(evidence) <= max_chars:
            return evidence, True
        keep = int(keep * 0.9) - 1  # JSON escaping makes some characters longer
    raise EvidenceTooLong(f"arguments alone exceed {max_chars} characters")


def _chunked(call: ParsedToolCall) -> list[ParsedToolCall]:
    """The call once per output chunk, each chunk marked with its place in the output."""
    if call.output is None or len(call.output) <= CHUNK_CHARS:
        return [call]
    chunks = split_output(call.output)
    total = len(call.output)
    return [
        replace(
            call,
            output=f"{text}\n…[part {i} of {len(chunks)}:"
            f" characters {start}–{start + len(text)} of {total}]",
        )
        for i, (start, text) in enumerate(chunks, start=1)
    ]


def _ask_grouped(
    call: ParsedToolCall, cards: list[Card], backend: Backend, max_chars: int
) -> dict[str, list[ChunkAnswer]]:
    # Group by the fields that are actually present, so a call without output
    # sends all its cards together.
    present = {"tool_name"}
    if call.args:
        present.add("args")
    if call.output is not None:
        present.add("output")
    groups: dict[tuple[str, ...], list[Card]] = defaultdict(list)
    for card in cards:
        groups[tuple(sorted(set(card.evidence) & present))].append(card)
    results: dict[str, list[ChunkAnswer]] = {}
    for fields, group in groups.items():
        asked = _chunked(call) if "output" in fields else [call]
        chunk_answers = [
            ChunkAnswer(c, *_ask(c, fields, group, backend, max_chars)) for c in asked
        ]
        results |= {card.name: chunk_answers for card in group}
    return results


def _ask(
    call: ParsedToolCall,
    fields: tuple[str, ...],
    cards: list[Card],
    backend: Backend,
    max_chars: int,
) -> tuple[dict[str, Distribution], RequestInfo]:
    """One request; on a too-long refusal, retry once with half the budget."""
    questions = {c.name: question_spec(c) for c in cards}
    for budget in (max_chars, max_chars // 2):
        evidence, was_truncated = build_evidence(call, fields, budget)
        try:
            result = backend.ask(evidence, questions)
            return result.answers, RequestInfo(was_truncated, result.input_tokens)
        except EvidenceTooLong:
            if budget != max_chars or "output" not in evidence:
                raise
    raise AssertionError("unreachable")


def _label(
    call: ParsedToolCall, card: Card, chunks: list[ChunkAnswer], backend: Backend
) -> Label:
    distributions = [c.answers[card.name] for c in chunks]
    if card.kind == "noul":
        answer, p = _any_yes([d["yes"] for d in distributions])
    else:
        answer, p = _most_severe(card.label, [_top(d) for d in distributions])
    return _make(
        call, card, answer, p, backend, [c.request for c in chunks], len(chunks)
    )


def _two_step_label(
    call: ParsedToolCall,
    step1: Card,
    step2: Card,
    chunks: list[ChunkAnswer],
    backend: Backend,
    max_chars: int,
) -> Label:
    p_yes = [c.answers[step1.name]["yes"] for c in chunks]
    requests = [c.request for c in chunks]
    answer, p = _any_yes(p_yes)
    if answer == "no":
        return _make(call, step1, NONE_ANSWER, p, backend, requests, len(chunks))

    fields = tuple(sorted(step2.evidence))
    picks: list[tuple[str, float]] = []
    for chunk, chunk_p_yes in zip(chunks, p_yes, strict=True):
        if chunk_p_yes < 0.5:
            continue
        answers, request = _ask(chunk.call, fields, [step2], backend, max_chars)
        requests.append(request)
        cls, p_cls = _top(answers[step2.name])
        picks.append((cls, chunk_p_yes * p_cls))
    cls, p = _most_severe(step2.label, picks)
    return _make(call, step2, cls, p, backend, requests, len(chunks))


def _any_yes(p_yes: list[float]) -> tuple[str, float]:
    """Yes if any chunk says yes, with the highest such p; otherwise no, with the lowest."""
    yes = [p for p in p_yes if p >= 0.5]
    if yes:
        return "yes", max(yes)
    return "no", min(1.0 - p for p in p_yes)


def _most_severe(label: str, picks: list[tuple[str, float]]) -> tuple[str, float]:
    """The most severe chunk answer. Its p is the highest among chunks giving it, or
    the lowest when every chunk gives the least severe answer."""
    if len(picks) == 1:
        return picks[0]
    order = SEVERITY.get(label)
    if order is None:
        raise ValueError(f"{label} has no severity order to combine chunks with")
    answer = min((a for a, _ in picks), key=order.index)
    ps = [p for a, p in picks if a == answer]
    return answer, min(ps) if answer == order[-1] else max(ps)


def _top(distribution: Distribution) -> tuple[str, float]:
    answer = max(distribution, key=lambda k: distribution[k])
    return answer, distribution[answer]


def _make(
    call: ParsedToolCall,
    card: Card,
    answer: str,
    p: float,
    backend: Backend,
    requests: list[RequestInfo],
    chunks: int,
) -> Label:
    tokens = [r.input_tokens for r in requests if r.input_tokens is not None]
    return Label(
        span_id=call.span_id,
        session_id=call.session_id,
        label=card.label,
        answer=answer,
        p=p,
        basis=call.basis,
        model=backend.model,
        taxonomy=card.taxonomy,
        output_truncated=any(r.output_truncated for r in requests),
        input_tokens=max(tokens) if tokens else None,
        chunks=chunks,
    )


def _size(evidence: dict[str, Any]) -> int:
    return len(json.dumps(evidence, ensure_ascii=False))
