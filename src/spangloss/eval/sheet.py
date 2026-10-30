"""Hand-labeling sheets: export tool calls to CSV, import the filled answers as gold.

The sheet carries context columns and one empty column per card. It never carries
model answers. Import checks every answer against its card's answer set, checks that
a cell is blank exactly when its question is not asked for that call, and writes
gold.jsonl, one record per labeled call.
"""

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spangloss.parse.record import Basis, ParsedToolCall
from spangloss.questions.cards import Card, load_cards

DEFAULT_SHEET = Path("data/eval/sheet_001.csv")
DEFAULT_GOLD = Path("data/eval/gold.jsonl")
CONTEXT_COLUMNS = ["span_id", "tool_name", "basis", "args_summary", "output_head"]
OUTPUT_HEAD_CHARS = 500
ARG_VALUE_CHARS = 300
# Spreadsheets run cells starting with these as formulas; session text is untrusted.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class SheetError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class ImportResult:
    labeled: int
    skipped_blank: int
    gold_total: int


def answer_columns(cards: list[Card]) -> list[str]:
    return [c.name for c in cards]


def export_sheet(
    calls: list[ParsedToolCall],
    path: Path,
    limit: int,
    gold: Path = DEFAULT_GOLD,
    force: bool = False,
) -> int:
    """Write the first `limit` calls not already in gold. Returns the row count."""
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists and may hold labels; use --force")
    done = {r["span_id"] for r in read_gold(gold)}
    rows = [c for c in calls if c.span_id not in done][:limit]
    columns = CONTEXT_COLUMNS + answer_columns(load_cards())

    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so spreadsheet apps detect the encoding.
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for call in rows:
            writer.writerow(
                {
                    "span_id": call.span_id,
                    "tool_name": call.tool_name,
                    "basis": call.basis,
                    "args_summary": _cell(summarize_args(call.args)),
                    "output_head": _cell(output_head(call.output)),
                }
            )
    return len(rows)


def import_sheet(
    sheet: Path, calls: list[ParsedToolCall], gold: Path = DEFAULT_GOLD
) -> ImportResult:
    """Validate every answer; write nothing unless the whole sheet is valid."""
    cards = load_cards()
    by_name = {c.name: c for c in cards}
    known = {c.span_id: c for c in calls}
    problems: list[str] = []

    with sheet.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in ["span_id", *answer_columns(cards)] if c not in header]
        if missing:
            raise SheetError([f"missing columns: {', '.join(missing)}"])
        rows = list(enumerate(reader, start=2))  # row 1 is the header

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped = 0
    for row_no, row in rows:
        span_id = (row.get("span_id") or "").strip()
        answers = {
            name: (row.get(name) or "").strip()
            for name in by_name
            if (row.get(name) or "").strip()
        }
        if not answers:
            skipped += 1
            continue
        where = f"row {row_no} ({span_id or 'no span_id'})"
        row_problems: list[str] = []
        if span_id not in known:
            row_problems.append(f"{where}: span_id is not in the parsed calls")
        if span_id in seen:
            row_problems.append(f"{where}: span_id appears more than once")
        seen.add(span_id)
        for name, answer in answers.items():
            allowed = by_name[name].answers
            if answer not in allowed:
                row_problems.append(
                    f"{where}, column {name}: {answer!r} is not one of"
                    f" {', '.join(allowed)}"
                )
        if span_id in known:
            row_problems += _check_blanks(where, answers, cards, known[span_id].basis)
        if row_problems:
            problems += row_problems
        else:
            records.append(_record(known[span_id], answers, cards, sheet))

    if problems:
        raise SheetError(problems)

    merged = {r["span_id"]: r for r in read_gold(gold)}
    merged |= {r["span_id"]: r for r in records}
    gold.parent.mkdir(parents=True, exist_ok=True)
    tmp = gold.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for record in merged.values():
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp.replace(gold)
    return ImportResult(len(records), skipped, len(merged))


def read_gold(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def summarize_args(args: dict[str, Any]) -> str:
    """One line per argument, long values cut, newlines shown as ⏎."""
    if not args:
        return "(no arguments captured)"
    lines = []
    for key, value in args.items():
        text = (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        )
        text = text.replace("\r\n", "\n").replace("\n", " ⏎ ")
        if len(text) > ARG_VALUE_CHARS:
            text = f"{text[:ARG_VALUE_CHARS]}…[+{len(text) - ARG_VALUE_CHARS} chars]"
        lines.append(f"{key}: {text}")
    return "\n".join(lines)


def output_head(output: str | None) -> str:
    if output is None:
        return "(no output captured)"
    if len(output) <= OUTPUT_HEAD_CHARS:
        return output
    return f"{output[:OUTPUT_HEAD_CHARS]}…[+{len(output) - OUTPUT_HEAD_CHARS} chars]"


def _cell(text: str) -> str:
    return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text


def _check_blanks(
    where: str, answers: dict[str, str], cards: list[Card], basis: Basis
) -> list[str]:
    """A cell is answered exactly when the labeler would ask its question.

    That is when the call's basis reaches the card's min_basis and, for a step-2
    card, its step-1 answer is yes. Basis comes from the parsed call, not the
    sheet's basis column, which is only context.
    """
    problems = []
    for card in cards:
        column = f"{where}, column {card.name}"
        if not card.applies_to(basis):
            if card.name in answers:
                problems.append(
                    f"{column}: must be blank because basis {basis} is below"
                    f" min_basis {card.min_basis}"
                )
            continue
        if card.step == 2:
            step1 = next(c for c in cards if c.label == card.label and c.step == 1)
            if answers.get(step1.name) != "yes":
                if card.name in answers:
                    problems.append(f"{column}: only answered when {step1.name} is yes")
                continue
            if card.name not in answers:
                problems.append(f"{column}: needed because {step1.name} is yes")
            continue
        if card.name not in answers:
            problems.append(
                f"{column}: needed because basis {basis} meets min_basis"
                f" {card.min_basis}"
            )
    return problems


def _record(
    call: ParsedToolCall, answers: dict[str, str], cards: list[Card], sheet: Path
) -> dict[str, Any]:
    """Card answers as entered, plus the label each implies."""
    labels: dict[str, str] = {}
    for card in cards:
        if card.step is None and card.name in answers:
            labels[card.label] = answers[card.name]
        elif card.step == 1 and card.name in answers:
            step2 = next(c for c in cards if c.label == card.label and c.step == 2)
            labels[card.label] = (
                answers[step2.name] if answers[card.name] == "yes" else "none"
            )
    return {
        "span_id": call.span_id,
        "session_id": call.session_id,
        "tool_name": call.tool_name,
        "basis": call.basis,
        "taxonomy": cards[0].taxonomy,
        "sheet": sheet.name,
        "answers": answers,
        "labels": labels,
    }
