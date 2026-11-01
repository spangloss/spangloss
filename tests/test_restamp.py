import json
from pathlib import Path
from typing import Any

import pytest

from spangloss import cli
from spangloss.eval import read_gold, restamp
from spangloss.parse import ParsedToolCall
from spangloss.parse.record import basis_for
from spangloss.questions import load_cards

CARDS = load_cards()
TO = CARDS[0].taxonomy  # restamp targets the cards' version

# Every question the 0.3.1 cards ask for a Bash call with args and output.
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
ARGS_ONLY = {k: v for k, v in FULL.items() if k != "injection"}


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


def record(c: ParsedToolCall, answers: dict[str, str], taxonomy: str = "0.3.0"):
    return {
        "span_id": c.span_id,
        "session_id": c.session_id,
        "tool_name": c.tool_name,
        "basis": c.basis,
        "taxonomy": taxonomy,
        "sheet": "sheet_test.csv",
        "answers": answers,
        "labels": {"sec.effect": answers.get("effect", "")},
    }


BASH = call("bash")
HANDBACK = call(
    "hb", tool_name="SubagentHandback", args={"message": "done"}, output=None
)
NO_OUTPUT = call("nout", output=None)
DONE = call("done")
CALLS = [BASH, HANDBACK, NO_OUTPUT, DONE]


@pytest.fixture
def gold(tmp_path: Path) -> Path:
    path = tmp_path / "gold.jsonl"
    rows = [
        record(BASH, FULL),
        # Under 0.3.0 a handback without output had no injection answer.
        record(HANDBACK, ARGS_ONLY),
        # Answered injection though the call has no incoming content.
        record(NO_OUTPUT, FULL),
        record(DONE, FULL, taxonomy=TO),
        record(call("gone"), FULL),  # not in the parsed calls
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_unaffected_records_are_restamped_with_provenance(gold: Path) -> None:
    result = restamp(gold, CALLS, CARDS, TO)
    assert (result.restamped, result.already) == (1, 1)
    bash = next(r for r in read_gold(gold) if r["span_id"] == "bash")
    assert bash["taxonomy"] == TO
    assert bash["restamped_from"] == "0.3.0"
    assert "changelog checks" in bash["restamp_reason"]
    assert bash["answers"] == FULL  # unchanged
    assert bash["sheet"] == "sheet_test.csv"  # provenance kept


def test_affected_records_are_listed_and_left_alone(gold: Path) -> None:
    before = {r["span_id"]: r for r in read_gold(gold)}
    result = restamp(gold, CALLS, CARDS, TO)
    affected = {a.span_id: a for a in result.affected}
    assert set(affected) == {"hb", "nout", "gone"}
    assert any("return-path" in r for r in affected["hb"].reasons)
    # The gating re-check catches the missing injection answer too.
    assert any(
        "needed because the call has incoming content" in r
        for r in affected["hb"].reasons
    )
    assert any("must be blank" in r for r in affected["nout"].reasons)
    assert any("not in the parsed calls" in r for r in affected["gone"].reasons)
    after = {r["span_id"]: r for r in read_gold(gold)}
    for span_id in affected:
        assert after[span_id] == before[span_id]


def test_already_current_records_are_untouched(gold: Path) -> None:
    restamp(gold, CALLS, CARDS, TO)
    done = next(r for r in read_gold(gold) if r["span_id"] == "done")
    assert "restamped_from" not in done


def test_target_must_match_the_cards(gold: Path) -> None:
    with pytest.raises(ValueError, match=f"cards are at {TO}"):
        restamp(gold, CALLS, CARDS, "0.4.0")


def test_versions_without_rules_are_counted_not_restamped(tmp_path: Path) -> None:
    path = tmp_path / "gold.jsonl"
    path.write_text(json.dumps(record(BASH, FULL, taxonomy="0.2.1")) + "\n")
    result = restamp(path, CALLS, CARDS, TO)
    assert (result.restamped, result.other_versions) == (0, 1)
    assert read_gold(path)[0]["taxonomy"] == "0.2.1"


def test_cli_restamp_lists_affected_records(
    gold: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parsed = tmp_path / "tool_calls.jsonl"
    parsed.write_text("".join(json.dumps(c.to_dict()) + "\n" for c in CALLS))
    args = ["gold", "restamp", "--to", TO, "--gold", str(gold)]
    assert cli.main([*args, "--input", str(parsed)]) == 0
    err = capsys.readouterr().err
    assert f"1 restamped to {TO}, 1 already {TO}, 3 affected" in err
    assert "hb SubagentHandback:" in err


def test_changes_chain_through_versions() -> None:
    from spangloss.eval.restamp import changes_between

    assert len(changes_between("0.3.0", "0.3.1") or []) == 4
    assert changes_between("0.3.1", "0.3.1") == []
    assert changes_between("0.2.1", "0.3.1") is None
