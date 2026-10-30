"""Command-line interface: spangloss <command> ..."""

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from spangloss import __version__
from spangloss.backends import Backend
from spangloss.config import DEFAULT_CONFIG, ConfigError, load_config
from spangloss.eval import (
    DEFAULT_GOLD,
    DEFAULT_SHEET,
    SheetError,
    export_sheet,
    import_sheet,
)
from spangloss.parse import ParsedToolCall, parse_claude_code
from spangloss.questions import label_call, load_cards
from spangloss.questions.runner import DEFAULT_MAX_EVIDENCE_CHARS
from spangloss.rules import RULES
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
    label.add_argument(
        "--config",
        type=Path,
        help=f"first-party MCP servers and other settings (default: {DEFAULT_CONFIG},"
        " if present)",
    )

    export = commands.add_parser(
        "export-sheet", help="Write a CSV sheet of tool calls to label by hand."
    )
    export.add_argument("--limit", type=int, required=True, help="at most N calls")
    export.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    export.add_argument("--out", type=Path, default=DEFAULT_SHEET)
    export.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    export.add_argument(
        "--force", action="store_true", help="overwrite an existing sheet"
    )

    imp = commands.add_parser(
        "import-sheet", help="Validate a filled sheet and add it to the gold labels."
    )
    imp.add_argument("sheet", type=Path)
    imp.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    imp.add_argument("--gold", type=Path, default=DEFAULT_GOLD)

    rules = commands.add_parser(
        "rules", help="Run the detection rules over stored labels."
    )
    rules.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    rules.add_argument("--db", type=Path, default=DEFAULT_DB)
    rules.add_argument(
        "--model", help="whose labels to read (default: the only model in the store)"
    )

    args = parser.parse_args(argv)
    if args.command == "rules":
        return _rules(args.input, args.db, args.model)
    if args.command == "label":
        try:
            config = (
                load_config(args.config, required=True)
                if args.config
                else load_config()
            )
        except ConfigError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return _label(
            make_backend(args.backend),
            args.input,
            args.db,
            args.limit,
            args.max_evidence_chars,
            config.first_party_mcp_servers,
        )
    if args.command == "export-sheet":
        return _export_sheet(args.input, args.out, args.gold, args.limit, args.force)
    if args.command == "import-sheet":
        return _import_sheet(args.sheet, args.input, args.gold)
    return _parse(args.input, args.logs, args.output)


def read_calls(path: Path) -> list[ParsedToolCall]:
    with path.open(encoding="utf-8") as f:
        return [ParsedToolCall(**json.loads(line)) for line in f if line.strip()]


def _export_sheet(calls: Path, out: Path, gold: Path, limit: int, force: bool) -> int:
    try:
        n = export_sheet(read_calls(calls), out, limit, gold=gold, force=force)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {n} tool calls to {out}", file=sys.stderr)
    return 0


def _import_sheet(sheet: Path, calls: Path, gold: Path) -> int:
    try:
        result = import_sheet(sheet, read_calls(calls), gold=gold)
    except SheetError as e:
        print(
            f"{sheet}: {len(e.problems)} problem(s), nothing written:", file=sys.stderr
        )
        for problem in e.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(
        f"{sheet}: {result.labeled} calls imported, {result.skipped_blank} blank rows"
        f" skipped; {gold} now holds {result.gold_total} calls",
        file=sys.stderr,
    )
    return 0


def _rules(calls_path: Path, db: Path, model: str | None) -> int:
    calls = read_calls(calls_path)
    with LabelStore(db) as store:
        models = store.models()
        if model is None:
            if len(models) != 1:
                found = ", ".join(models) or "none"
                print(
                    f"error: choose a model with --model (models in {db}: {found})",
                    file=sys.stderr,
                )
                return 1
            model = models[0]
        elif model not in models:
            print(f"error: no labels from {model} in {db}", file=sys.stderr)
            return 1
        answers = store.answers(model)
        for name, rule in RULES.items():
            n = store.replace_findings(name, model, rule(calls, answers))
            print(f"{name}: {n} finding(s) over {model} labels", file=sys.stderr)
    return 0


def make_backend(name: str) -> Backend:
    from spangloss.backends.typesafe import jev_backend, kev_backend

    return kev_backend() if name == "kev" else jev_backend()


def _label(
    backend: Backend,
    calls_path: Path,
    db: Path,
    limit: int,
    max_chars: int,
    first_party: frozenset[str] = frozenset(),
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    cards = load_cards()
    calls = read_calls(calls_path)

    with LabelStore(db) as store:
        done = store.labeled_span_ids(backend.model)
        todo = [c for c in calls if c.span_id not in done][:limit]
        written = 0
        skipped = 0
        for call in todo:
            outcome = label_call(call, cards, backend, max_chars, first_party)
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
