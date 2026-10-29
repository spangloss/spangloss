"""Claude Code adapter: claude_code.tool spans (plus tool_result log events) to ParsedToolCall.

Field names here come from captured Claude Code telemetry, not from guesses.
Tool arguments arrive in the tool_result log event's tool_input; the span itself
carries only a few input-derived attributes. tool_input truncates long strings, so
a complete span copy of a field replaces its truncated log copy.
"""

import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spangloss.parse.otlp import (
    IDENTITY_KEYS,
    LogRecord,
    Span,
    read_log_records,
    read_spans,
)
from spangloss.parse.record import ArgsSource, ParsedToolCall, basis_for

TOOL_SPAN = "claude_code.tool"
EXECUTION_SPAN = "claude_code.tool.execution"
PERMISSION_SPAN = "claude_code.tool.blocked_on_user"
OUTPUT_EVENT = "tool.output"
TOOL_RESULT_EVENT = "tool_result"

# Attributes common to every tool span. Anything else on an unknown tool's span is an argument.
ENVELOPE_KEYS = frozenset(
    {
        "agent_id",  # set on calls made inside a subagent; context, not an argument
        "duration_ms",
        "gen_ai.conversation.id",
        "gen_ai.operation.name",
        "gen_ai.provider.name",
        "gen_ai.tool.call.id",
        "gen_ai.tool.name",
        "session.id",
        "span.type",
        "terminal.type",
        "tool_name",
        "tool_name_safe",
        "tool_use_id",
        "user.id",
    }
    | IDENTITY_KEYS
)

# tool_input keeps the first 128 characters of a long string, then "…[N chars]",
# where N is the original length.
TRUNCATED = re.compile(r"…\[(\d+) chars\]\Z")

# Span attributes that copy an argument, for tools that need mapping: span key -> args key.
# Bash's full_command equals tool_input.command, untruncated; bash_argv0 and
# bash_command_class are derived from it and dropped.
SPAN_ARGS: dict[str, dict[str, str]] = {
    "Bash": {"full_command": "command"},
    "Read": {"file_path": "file_path"},
}

# Which tool.output event attribute holds the tool's output.
OUTPUT_KEY: dict[str, str] = {
    "Bash": "output",
    "Read": "content",
    "WebFetch": "output",
    "WebSearch": "output",
}

# Tools whose tool.output event describes the input, not the tool's output:
# event key -> args key. Unlisted event keys are ignored, and output stays None.
# Write's event holds the full written content, which tool_input truncates.
# Edit's event holds a diff of the change; the tool's actual result is not captured.
OUTPUT_EVENT_ARGS: dict[str, dict[str, str]] = {
    "Write": {"content": "content", "file_path": "file_path"},
    "Edit": {"diff": "diff", "file_path": "file_path"},
}


def parse_files(traces: Path, logs: Path | None = None) -> list[ParsedToolCall]:
    spans = read_spans(traces)
    records = read_log_records(logs) if logs is not None else []
    return parse(spans, records)


def parse(spans: dict[str, Span], records: list[LogRecord]) -> list[ParsedToolCall]:
    children: dict[str, list[Span]] = defaultdict(list)
    for span in spans.values():
        if span.parent_span_id:
            children[span.parent_span_id].append(span)

    tool_inputs: dict[str, dict[str, Any]] = {}
    for record in records:
        a = record.attributes
        if a.get("event.name") == TOOL_RESULT_EVENT and a.get("tool_use_id"):
            tool_inputs[a["tool_use_id"]] = _json_object(a.get("tool_input"))

    tool_spans = sorted(
        (s for s in spans.values() if s.name == TOOL_SPAN),
        key=lambda s: (s.start_time_unix_nano, s.span_id),
    )
    return [
        _tool_call(s, children[s.span_id], tool_inputs, _parent_tool(s, spans))
        for s in tool_spans
    ]


def _parent_tool(span: Span, spans: dict[str, Span]) -> str | None:
    """Nearest claude_code.tool ancestor. A subagent's calls sit under the
    delegating call's execution span, so walk up rather than check one level."""
    seen = {span.span_id}
    parent_id = span.parent_span_id
    while parent_id and parent_id not in seen and parent_id in spans:
        parent = spans[parent_id]
        if parent.name == TOOL_SPAN:
            return parent.span_id
        seen.add(parent_id)
        parent_id = parent.parent_span_id
    return None


def _tool_call(
    span: Span,
    children: list[Span],
    tool_inputs: dict[str, dict[str, Any]],
    parent_tool_span_id: str | None,
) -> ParsedToolCall:
    a = span.attributes
    tool_name = str(a.get("tool_name", ""))
    output_event = next(
        (e.attributes for e in span.events if e.name == OUTPUT_EVENT), None
    )

    span_args = _span_args(tool_name, a, output_event)
    logged = tool_inputs.get(a.get("tool_use_id", ""))
    args, args_source = _merge_args(tool_name, logged, span_args)
    args = {k: v for k, v in args.items() if k not in IDENTITY_KEYS}

    output: str | None = None
    if output_event is not None and tool_name not in OUTPUT_EVENT_ARGS:
        output = _output(tool_name, output_event)
    execution = _child(children, EXECUTION_SPAN)
    permission = _child(children, PERMISSION_SPAN)

    return ParsedToolCall(
        trace_id=span.trace_id,
        span_id=span.span_id,
        parent_tool_span_id=parent_tool_span_id,
        session_id=a.get("session.id"),
        start_time=_iso(span.start_time_unix_nano),
        end_time=_iso(span.end_time_unix_nano),
        tool_name=tool_name,
        args=args,
        args_source=args_source,
        args_truncated=_is_truncated(args),
        output=output,
        has_output=output is not None,
        basis=basis_for(args, output),
        success=execution.attributes.get("success") if execution else None,
        permission_decision=permission.attributes.get("decision")
        if permission
        else None,
        permission_source=permission.attributes.get("source") if permission else None,
    )


def _span_args(
    tool_name: str, attributes: dict[str, Any], output_event: dict[str, Any] | None
) -> dict[str, Any]:
    """Arguments visible on the span: mapped or generic attributes, plus any
    input-describing tool.output fields."""
    if tool_name in SPAN_ARGS:
        args = {
            args_key: attributes[span_key]
            for span_key, args_key in SPAN_ARGS[tool_name].items()
            if span_key in attributes
        }
    else:
        args = {k: v for k, v in attributes.items() if k not in ENVELOPE_KEYS}
    if output_event is not None:
        for event_key, args_key in OUTPUT_EVENT_ARGS.get(tool_name, {}).items():
            if event_key in output_event:
                args[args_key] = output_event[event_key]
    return args


def _merge_args(
    tool_name: str, logged: dict[str, Any] | None, span_args: dict[str, Any]
) -> tuple[dict[str, Any], ArgsSource | None]:
    """Log arguments first. A span value replaces a log value only when the log value
    is a truncated copy of it, and adds a missing field only when the field is mapped
    for this tool. Unmapped span attributes are used only when there is no log."""
    if not logged:
        return span_args, "span" if span_args else None

    args = dict(logged)
    addable = set(SPAN_ARGS.get(tool_name, {}).values()) | set(
        OUTPUT_EVENT_ARGS.get(tool_name, {}).values()
    )
    merged = False
    for key, value in span_args.items():
        if key in args:
            if _completes(args[key], value):
                args[key], merged = value, True
        elif key in addable:
            args[key], merged = value, True
    return args, "merged" if merged else "log"


def _completes(logged: Any, value: Any) -> bool:
    """True if logged is a tool_input truncation of value."""
    if not (isinstance(logged, str) and isinstance(value, str)):
        return False
    m = TRUNCATED.search(logged)
    return (
        m is not None
        and int(m.group(1)) == len(value)
        and value.startswith(logged[: m.start()])
    )


def _is_truncated(value: Any) -> bool:
    if isinstance(value, str):
        return TRUNCATED.search(value) is not None
    if isinstance(value, dict):
        return any(_is_truncated(v) for v in value.values())
    if isinstance(value, list):
        return any(_is_truncated(v) for v in value)
    return False


def _output(tool_name: str, event: dict[str, Any]) -> str | None:
    if tool_name in OUTPUT_KEY:
        value = event.get(OUTPUT_KEY[tool_name])
        return None if value is None else str(value)
    # Unknown tool: a lone attribute is the output; otherwise keep every field.
    if len(event) == 1:
        return str(next(iter(event.values())))
    return json.dumps(event, sort_keys=True) if event else None


def _child(children: list[Span], name: str) -> Span | None:
    return next((c for c in children if c.name == name), None)


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _iso(unix_nano: int) -> str:
    seconds, nanos = divmod(unix_nano, 1_000_000_000)
    dt = datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=nanos // 1000)
    return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")
