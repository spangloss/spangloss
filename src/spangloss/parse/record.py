"""The common record every agent adapter produces: one per tool call."""

from dataclasses import asdict, dataclass
from typing import Any, Literal

Basis = Literal["name_only", "args", "content"]
ArgsSource = Literal["log", "span", "merged"]


@dataclass(frozen=True, slots=True)
class ParsedToolCall:
    trace_id: str
    span_id: str
    parent_tool_span_id: str | None  # enclosing tool call, e.g. a subagent's Agent
    session_id: str | None
    start_time: str  # ISO 8601, UTC
    end_time: str
    tool_name: str
    args: dict[str, Any]
    args_source: ArgsSource | None  # where args came from; None when there are none
    args_truncated: bool  # some argument is truncated in every source
    output: str | None
    has_output: bool
    basis: Basis  # TAXONOMY.md: content needs args and output
    success: bool | None
    permission_decision: str | None
    permission_source: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def basis_for(args: dict[str, Any], output: str | None) -> Basis:
    """Input basis per TAXONOMY.md. Output without arguments is not content."""
    if not args:
        return "name_only"
    return "content" if output is not None else "args"
