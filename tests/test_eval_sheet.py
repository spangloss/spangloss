import csv
import json
from pathlib import Path
from typing import Any

import pytest

from spangloss import cli
from spangloss.eval import SheetError, export_sheet, import_sheet, read_gold
from spangloss.eval.sheet import CONTEXT_COLUMNS, output_head, summarize_args
from spangloss.parse import ParsedToolCall
from spangloss.parse.record import basis_for
from spangloss.questions import load_cards

CARD_COLUMNS = [c.name for c in load_cards()]


def call(span_id: str, **overrides: Any) -> ParsedToolCall:
    base: dict[str, Any] = {
        "trace_id": "t" * 32,
        "span_id": span_id,
        "parent_tool_span_id": None,
        "session_id": "session-1",
        "start_time": "2026-01-01T00:00:00.000000Z",
        "end_time": "2026-01-01T00:00:01.000000Z",
        "tool_name": "Bash",
        "args": {"command": "ls -la /tmp/example"},
        "args_source": "log",
        "args_truncated": False,
        "output": "total 0",
        "success": True,
        "permission_decision": "accept",
        "permission_source": "config",
    } | overrides
    base["has_output"] = base["output"] is not None
    base["basis"] = basis_for(base["args"], base["output"])
    return ParsedToolCall(**base)


CALLS = [call(f"s{i}") for i in range(1, 6)]


def read_sheet(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_sheet(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CONTEXT_COLUMNS + CARD_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: "" for c in CONTEXT_COLUMNS + CARD_COLUMNS} | row)


FULL = {
    "effect": "read",
    "reversibility": "reversible",
    "intent": "explore",
    "trifecta.private_source": "yes",
    "trifecta.sink": "no",
    "trifecta.untrusted_source": "no",
    "injection": "no",
    "data_class.sensitive": "no",
}


# --- export ------------------------------------------------------------------


def test_export_writes_context_and_empty_answer_columns(tmp_path: Path) -> None:
    sheet = tmp_path / "eval" / "sheet_001.csv"
    assert export_sheet(CALLS, sheet, limit=3, gold=tmp_path / "gold.jsonl") == 3
    rows = read_sheet(sheet)
    assert list(rows[0]) == CONTEXT_COLUMNS + CARD_COLUMNS
    assert [r["span_id"] for r in rows] == ["s1", "s2", "s3"]
    assert rows[0]["args_summary"] == "command: ls -la /tmp/example"
    assert rows[0]["output_head"] == "total 0"
    assert rows[0]["basis"] == "content"
    for row in rows:  # no model answers, nothing pre-filled
        assert all(row[c] == "" for c in CARD_COLUMNS)


def test_export_skips_calls_already_in_gold(tmp_path: Path) -> None:
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        json.dumps({"span_id": "s1"}) + "\n" + json.dumps({"span_id": "s2"}) + "\n"
    )
    export_sheet(CALLS, tmp_path / "sheet.csv", limit=2, gold=gold)
    assert [r["span_id"] for r in read_sheet(tmp_path / "sheet.csv")] == ["s3", "s4"]


def test_export_refuses_to_overwrite_a_sheet(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    sheet.write_text("my labels")
    with pytest.raises(FileExistsError):
        export_sheet(CALLS, sheet, limit=1, gold=tmp_path / "gold.jsonl")
    assert sheet.read_text() == "my labels"
    export_sheet(CALLS, sheet, limit=1, gold=tmp_path / "gold.jsonl", force=True)
    assert len(read_sheet(sheet)) == 1


def test_export_neutralizes_formula_cells(tmp_path: Path) -> None:
    risky = [call("s1", args={}, output='=HYPERLINK("http://example.invalid","x")')]
    export_sheet(risky, tmp_path / "sheet.csv", limit=1, gold=tmp_path / "gold.jsonl")
    assert read_sheet(tmp_path / "sheet.csv")[0]["output_head"].startswith("'=")


def test_summaries_are_readable_and_bounded() -> None:
    summary = summarize_args(
        {"file_path": "/tmp/a.txt", "content": "x\n" * 400, "n": 3}
    )
    lines = summary.split("\n")
    assert lines[0] == "file_path: /tmp/a.txt"
    assert lines[1].startswith("content: x ⏎ x ⏎") and "…[+" in lines[1]
    assert lines[2] == "n: 3"
    assert summarize_args({}) == "(no arguments captured)"
    assert output_head(None) == "(no output captured)"
    head = output_head("y" * 900)
    assert head == "y" * 500 + "…[+400 chars]"


# --- import ------------------------------------------------------------------


def test_import_writes_answers_and_implied_labels(tmp_path: Path) -> None:
    sheet, gold = tmp_path / "sheet.csv", tmp_path / "gold.jsonl"
    write_sheet(
        sheet,
        [
            {"span_id": "s1", **FULL},
            {
                "span_id": "s2",
                **FULL,
                "effect": "delete",
                "data_class.sensitive": "yes",
                "data_class": "secrets",
            },
            {"span_id": "s3"},  # not labeled yet
        ],
    )
    result = import_sheet(sheet, CALLS, gold=gold)
    assert (result.labeled, result.skipped_blank, result.gold_total) == (2, 1, 2)
    records = {r["span_id"]: r for r in read_gold(gold)}
    assert records["s1"]["answers"] == FULL
    assert records["s1"]["labels"]["sec.data_class"] == "none"
    assert records["s1"]["labels"]["sec.trifecta.sink"] == "no"
    assert records["s2"]["labels"]["sec.effect"] == "delete"
    assert records["s2"]["labels"]["sec.data_class"] == "secrets"
    assert records["s2"]["sheet"] == "sheet.csv"
    assert records["s2"]["taxonomy"] == "0.3.0"


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        (
            {"span_id": "s1", **FULL, "effect": "Read"},
            "'Read' is not one of read, write",
        ),
        ({"span_id": "s1", **FULL, "trifecta.sink": "maybe"}, "column trifecta.sink"),
        (
            {"span_id": "s1", **FULL, "data_class": "pii"},
            "s1), column data_class: only answered when data_class.sensitive is yes",
        ),
        (
            {"span_id": "s1", **FULL, "data_class.sensitive": "yes"},
            "s1), column data_class: needed because data_class.sensitive is yes",
        ),
        (
            {"span_id": "s1", **FULL, "intent": ""},
            "s1), column intent: needed because basis content meets min_basis args",
        ),
        ({"span_id": "nope", **FULL}, "span_id is not in the parsed calls"),
    ],
)
def test_import_rejects_bad_answers_and_writes_nothing(
    tmp_path: Path, row: dict[str, str], problem: str
) -> None:
    sheet, gold = tmp_path / "sheet.csv", tmp_path / "gold.jsonl"
    write_sheet(sheet, [{"span_id": "s2", **FULL}, row])
    with pytest.raises(SheetError) as e:
        import_sheet(sheet, CALLS, gold=gold)
    assert any(problem in p for p in e.value.problems), e.value.problems
    assert all(p.startswith("row 3 ") for p in e.value.problems)
    assert not gold.exists()  # the valid row was not written either


def test_import_reports_every_problem(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    write_sheet(
        sheet,
        [
            {"span_id": "s1", **FULL, "effect": "reed", "injection": "nah"},
            {"span_id": "s1", **FULL},
        ],
    )
    with pytest.raises(SheetError) as e:
        import_sheet(sheet, CALLS, gold=tmp_path / "gold.jsonl")
    assert len(e.value.problems) == 3
    assert any("appears more than once" in p for p in e.value.problems)


NAME_ONLY = call("n1", args={}, output=None)
NAME_ONLY_OUTPUT = call("w1", tool_name="WebSearch", args={}, output="results")
ARGS_ONLY = call("a1", output=None)
TRIFECTA = {
    "trifecta.private_source": "no",
    "trifecta.sink": "yes",
    "trifecta.untrusted_source": "yes",
}


def test_import_accepts_blanks_where_questions_are_not_asked(tmp_path: Path) -> None:
    sheet, gold = tmp_path / "sheet.csv", tmp_path / "gold.jsonl"
    args_row = {k: v for k, v in FULL.items() if k != "injection"}
    write_sheet(
        sheet,
        [
            {"span_id": "n1", "effect": "read", **TRIFECTA},
            {"span_id": "w1", "effect": "read", "injection": "no", **TRIFECTA},
            {"span_id": "a1", **args_row},
        ],
    )
    calls = [NAME_ONLY, NAME_ONLY_OUTPUT, ARGS_ONLY]
    result = import_sheet(sheet, calls, gold=gold)
    assert result.labeled == 3
    records = {r["span_id"]: r for r in read_gold(gold)}
    assert set(records["n1"]["labels"]) == {
        "sec.effect",
        "sec.trifecta.private_source",
        "sec.trifecta.sink",
        "sec.trifecta.untrusted_source",
    }
    assert records["w1"]["labels"]["sec.injection"] == "no"
    assert "sec.injection" not in records["a1"]["labels"]


def test_import_rejects_answers_where_questions_are_not_asked(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [{"span_id": "n1", **FULL}, {"span_id": "a1", **FULL}])
    with pytest.raises(SheetError) as e:
        import_sheet(sheet, [NAME_ONLY, ARGS_ONLY], gold=tmp_path / "gold.jsonl")
    problems = e.value.problems
    # name_only without output: only effect and the trifecta questions are asked.
    assert sorted(
        p.split("column ")[1].split(":")[0] for p in problems if "(n1)" in p
    ) == [
        "data_class.sensitive",
        "injection",
        "intent",
        "reversibility",
    ]
    assert (
        "row 2 (n1), column intent: must be blank because basis name_only is below"
        " min_basis args"
    ) in problems
    # injection is gated on output, not basis.
    assert problems[-1] == (
        "row 3 (a1), column injection: must be blank because the call has no output"
    )


def test_import_requires_injection_when_there_is_output(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [{"span_id": "w1", "effect": "read", **TRIFECTA}])
    with pytest.raises(SheetError) as e:
        import_sheet(sheet, [NAME_ONLY_OUTPUT], gold=tmp_path / "gold.jsonl")
    assert e.value.problems == [
        "row 2 (w1), column injection: needed because the call has output"
    ]


def test_import_uses_the_parsed_basis_not_the_sheet_column(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    write_sheet(sheet, [{"span_id": "n1", "basis": "content", **FULL}])
    with pytest.raises(SheetError, match="basis name_only is below"):
        import_sheet(sheet, [NAME_ONLY], gold=tmp_path / "gold.jsonl")


def test_import_requires_the_answer_columns(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    sheet.write_text("span_id,effect\ns1,read\n")
    with pytest.raises(SheetError, match="missing columns"):
        import_sheet(sheet, CALLS, gold=tmp_path / "gold.jsonl")


def test_reimport_replaces_records_and_keeps_others(tmp_path: Path) -> None:
    gold = tmp_path / "gold.jsonl"
    first, second = tmp_path / "sheet_001.csv", tmp_path / "sheet_002.csv"
    write_sheet(
        first,
        [{"span_id": "s1", **FULL}, {"span_id": "s2", **FULL}],
    )
    import_sheet(first, CALLS, gold=gold)
    write_sheet(
        second,
        [{"span_id": "s2", **FULL, "effect": "write"}, {"span_id": "s3", **FULL}],
    )
    result = import_sheet(second, CALLS, gold=gold)
    records = {r["span_id"]: r for r in read_gold(gold)}
    assert result.gold_total == 3
    assert records["s1"]["labels"]["sec.effect"] == "read"
    assert records["s2"]["labels"]["sec.effect"] == "write"
    assert records["s2"]["sheet"] == "sheet_002.csv"


# --- CLI ---------------------------------------------------------------------


def test_cli_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parsed = tmp_path / "tool_calls.jsonl"
    parsed.write_text("".join(json.dumps(c.to_dict()) + "\n" for c in CALLS))
    sheet, gold = tmp_path / "eval" / "sheet_001.csv", tmp_path / "eval" / "gold.jsonl"
    common = ["--input", str(parsed), "--gold", str(gold)]

    assert cli.main(["export-sheet", "--limit", "2", "--out", str(sheet), *common]) == 0
    assert cli.main(["export-sheet", "--limit", "2", "--out", str(sheet), *common]) == 1

    rows = read_sheet(sheet)
    rows[0] |= FULL
    rows[1] |= FULL | {"effect": "bogus"}
    write_sheet(sheet, rows)
    assert cli.main(["import-sheet", str(sheet), *common]) == 1
    assert "'bogus' is not one of" in capsys.readouterr().err

    rows[1]["effect"] = "write"
    write_sheet(sheet, rows)
    assert cli.main(["import-sheet", str(sheet), *common]) == 0
    assert {r["span_id"] for r in read_gold(gold)} == {"s1", "s2"}
