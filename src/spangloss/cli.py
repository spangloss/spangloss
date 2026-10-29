"""Command-line interface: spangloss <command> ..."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from spangloss import __version__
from spangloss.parse import parse_claude_code

DEFAULT_OUTPUT = Path("data/parsed/tool_calls.jsonl")


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

    args = parser.parse_args(argv)
    return _parse(args.input, args.logs, args.output)


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
