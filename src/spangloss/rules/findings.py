"""What a detection rule produces, and the shape of the answers it reads."""

from collections.abc import Callable
from dataclasses import dataclass

from spangloss.parse.record import ParsedToolCall

# span_id -> label -> answer, from one model's labels plus derived labels.
Answers = dict[str, dict[str, str]]


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    span_id: str  # the span the finding is about
    session_id: str | None
    trigger_span_ids: tuple[str, ...]  # the spans whose labels triggered it


Rule = Callable[[list[ParsedToolCall], Answers], list[Finding]]
