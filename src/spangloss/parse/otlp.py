"""Read OTLP JSON export files, as written by the Collector's file exporter.

Each line is one export request and can hold many spans or log records.
Identity attributes are dropped from every attribute map read here.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IDENTITY_KEYS = frozenset(
    {"user.email", "user.account_uuid", "user.account_id", "organization.id"}
)


@dataclass(frozen=True, slots=True)
class SpanEvent:
    name: str
    attributes: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Span:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_time_unix_nano: int
    end_time_unix_nano: int
    attributes: dict[str, Any]
    events: list[SpanEvent] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LogRecord:
    attributes: dict[str, Any]


def decode_value(value: dict[str, Any]) -> Any:
    """Decode one OTLP JSON AnyValue. intValue arrives as a string."""
    if not value:
        return None
    ((kind, raw),) = value.items()
    match kind:
        case "intValue":
            return int(raw)
        case "doubleValue":
            return float(raw)
        case "arrayValue":
            return [decode_value(v) for v in raw.get("values", [])]
        case "kvlistValue":
            return decode_attributes(raw.get("values", []))
        case _:  # stringValue, boolValue, bytesValue (base64 string)
            return raw


def decode_attributes(attributes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        a["key"]: decode_value(a.get("value", {}))
        for a in attributes
        if a["key"] not in IDENTITY_KEYS
    }


def _requests(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no}: not valid JSON: {e.msg}") from e


def read_spans(path: Path) -> dict[str, Span]:
    """All spans in the file, by span ID. Reads the whole file so that
    parents and children from different export batches can be joined."""
    spans: dict[str, Span] = {}
    for request in _requests(path):
        for resource_spans in request.get("resourceSpans", []):
            for scope_spans in resource_spans.get("scopeSpans", []):
                for s in scope_spans.get("spans", []):
                    spans[s["spanId"]] = Span(
                        trace_id=s["traceId"],
                        span_id=s["spanId"],
                        parent_span_id=s.get("parentSpanId") or None,
                        name=s["name"],
                        start_time_unix_nano=int(s["startTimeUnixNano"]),
                        end_time_unix_nano=int(s["endTimeUnixNano"]),
                        attributes=decode_attributes(s.get("attributes", [])),
                        events=[
                            SpanEvent(
                                e["name"], decode_attributes(e.get("attributes", []))
                            )
                            for e in s.get("events", [])
                        ],
                    )
    return spans


def read_log_records(path: Path) -> list[LogRecord]:
    records: list[LogRecord] = []
    for request in _requests(path):
        for resource_logs in request.get("resourceLogs", []):
            for scope_logs in resource_logs.get("scopeLogs", []):
                for r in scope_logs.get("logRecords", []):
                    records.append(
                        LogRecord(decode_attributes(r.get("attributes", [])))
                    )
    return records
