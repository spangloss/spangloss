"""Ask a tool call's applicable cards and turn the answers into labels.

- Cards are gated by min_basis (TAXONOMY.md, Input basis); unasked labels are omitted.
- Cards that share an evidence set go in one request. Questions in a request share
  one state, so cards that see output and cards that don't go in separate requests.
- Each label records its request's input token count, as the backend reports it.
- Evidence goes only in the request's state, never in a question.
- A two-step label (data_class) asks step 2 only when step 1 is yes, and stores
  p(no) for none, or p(yes) x p(class) for a class.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
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
    input_tokens: int | None  # of the request that produced this answer


@dataclass(frozen=True, slots=True)
class RequestInfo:
    output_truncated: bool
    input_tokens: int | None


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
        answers, requests = _ask_grouped(call, first_round, backend, max_evidence_chars)
        labels = [
            _label(call, card, answers[card.name], backend, requests[card.name])
            for card in first_round
            if card.step is None
        ]
        for step1 in (c for c in first_round if c.step == 1):
            step2 = next(c for c in cards if c.label == step1.label and c.step == 2)
            labels.append(
                _two_step_label(
                    call, step1, step2, answers, requests, backend, max_evidence_chars
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


def _ask_grouped(
    call: ParsedToolCall, cards: list[Card], backend: Backend, max_chars: int
) -> tuple[dict[str, Distribution], dict[str, RequestInfo]]:
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
    answers: dict[str, Distribution] = {}
    requests: dict[str, RequestInfo] = {}
    for fields, group in groups.items():
        group_answers, info = _ask(call, fields, group, backend, max_chars)
        answers |= group_answers
        requests |= {c.name: info for c in group}
    return answers, requests


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
    call: ParsedToolCall,
    card: Card,
    distribution: Distribution,
    backend: Backend,
    request: RequestInfo,
) -> Label:
    answer = max(distribution, key=lambda k: distribution[k])
    return _make(call, card, answer, distribution[answer], backend, request)


def _two_step_label(
    call: ParsedToolCall,
    step1: Card,
    step2: Card,
    answers: dict[str, Distribution],
    requests: dict[str, RequestInfo],
    backend: Backend,
    max_chars: int,
) -> Label:
    """A class answer records the step-2 request; none records step 1's."""
    p_yes = answers[step1.name]["yes"]
    if p_yes < 0.5:
        return _make(
            call, step1, NONE_ANSWER, 1.0 - p_yes, backend, requests[step1.name]
        )
    fields = tuple(sorted(step2.evidence))
    step2_answers, request = _ask(call, fields, [step2], backend, max_chars)
    distribution = step2_answers[step2.name]
    answer = max(distribution, key=lambda k: distribution[k])
    return _make(call, step2, answer, p_yes * distribution[answer], backend, request)


def _make(
    call: ParsedToolCall,
    card: Card,
    answer: str,
    p: float,
    backend: Backend,
    request: RequestInfo,
) -> Label:
    return Label(
        span_id=call.span_id,
        session_id=call.session_id,
        label=card.label,
        answer=answer,
        p=p,
        basis=call.basis,
        model=backend.model,
        taxonomy=card.taxonomy,
        output_truncated=request.output_truncated,
        input_tokens=request.input_tokens,
    )


def _size(evidence: dict[str, Any]) -> int:
    return len(json.dumps(evidence, ensure_ascii=False))
