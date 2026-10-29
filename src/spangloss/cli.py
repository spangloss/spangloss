"""Command-line interface: spangloss <command> ..."""

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from spangloss import __version__
from spangloss.backends import Backend
from spangloss.parse import ParsedToolCall, parse_claude_code
from spangloss.questions import label_call, load_cards
from spangloss.questions.runner import DEFAULT_MAX_EVIDENCE_CHARS
from spangloss.store import DEFAULT_DB, LabelStore

DEFAULT_OUTPUT = Path("data/parsed/tool_calls.jsonl")
log = logging.getLogger("spangloss")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spangloss", description="Security labels for AI agent telemetry."
    )
    parser.add_argument(
        "--version", action="version", version=f"spangloss {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    parse = commands.add_parser(
        "parse", help="Parse Claude Code traces into tool-call records."
    )
    parse.add_argument(
        "input", type=Path, help="OTLP JSON traces file, e.g. data/raw/traces.jsonl"
    )
    parse.add_argument(
        "--logs",
        type=Path,
        help="OTLP JSON logs file with tool_result events (default: logs.jsonl next to input, if present)",
    )
    parse.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"default: {DEFAULT_OUTPUT}",
    )

    label = commands.add_parser("label", help="Label parsed tool calls.")
    label.add_argument("--backend", choices=["kev", "jev"], required=True)
    label.add_argument(
        "--limit", type=int, required=True, help="label at most N unlabeled calls"
    )
    label.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    label.add_argument("--db", type=Path, default=DEFAULT_DB)
    label.add_argument(
        "--max-evidence-chars",
        type=int,
        default=DEFAULT_MAX_EVIDENCE_CHARS,
        help=f"default: {DEFAULT_MAX_EVIDENCE_CHARS}",
    )

    args = parser.parse_args(argv)
    if args.command == "label":
        return _label(
            make_backend(args.backend),
            args.input,
            args.db,
            args.limit,
            args.max_evidence_chars,
        )
    return _parse(args.input, args.logs, args.output)


def make_backend(name: str) -> Backend:
    from spangloss.backends.typesafe import jev_backend, kev_backend

    return kev_backend() if name == "kev" else jev_backend()


def _label(
    backend: Backend, calls_path: Path, db: Path, limit: int, max_chars: int
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    cards = load_cards()
    with calls_path.open(encoding="utf-8") as f:
        calls = [ParsedToolCall(**json.loads(line)) for line in f if line.strip()]

    with LabelStore(db) as store:
        done = store.labeled_span_ids(backend.model)
        todo = [c for c in calls if c.span_id not in done][:limit]
        written = 0
        skipped = 0
        for call in todo:
            outcome = label_call(call, cards, backend, max_chars)
            if outcome.skipped:
                skipped += 1
                log.info(
                    "%s: skipped %s %s: %s",
                    backend.name,
                    call.span_id,
                    call.tool_name,
                    outcome.skipped,
                )
                continue
            written += store.write(outcome.labels)

    print(
        f"{backend.model}: {len(todo) - skipped} calls labeled, {skipped} skipped,"
        f" {written} labels written to {db}",
        file=sys.stderr,
    )
    return 0


def _parse(traces: Path, logs: Path | None, output: Path) -> int:
    if logs is None:
        sibling = traces.with_name("logs.jsonl")
        logs = sibling if sibling.exists() else None
    calls = parse_claude_code(traces, logs)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for call in calls:
            f.write(json.dumps(call.to_dict(), ensure_ascii=False) + "\n")

    bases = Counter(c.basis for c in calls)
    sources = Counter(c.args_source or "none" for c in calls)
    print(f"traces: {traces}", file=sys.stderr)
    print(f"logs:   {logs or '(none)'}", file=sys.stderr)
    print(f"wrote {len(calls)} tool calls to {output}", file=sys.stderr)
    print(f"basis: {dict(bases)}  args from: {dict(sources)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
