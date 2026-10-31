import json
from pathlib import Path

import pytest

from spangloss import cli
from spangloss.eval.score import (
    Pair,
    StoredLabel,
    expected_calibration_error,
    format_report,
    score,
    wilson_interval,
)
from spangloss.questions import load_cards
from spangloss.questions.runner import Label
from spangloss.store import LabelStore

CARDS = load_cards()
EFFECT, UNTRUSTED, INJECTION = (
    "sec.effect",
    "sec.trifecta.untrusted_source",
    "sec.injection",
)


def gold(span_id: str, basis: str, labels: dict[str, str]) -> dict:
    return {
        "span_id": span_id,
        "session_id": "session-1",
        "tool_name": "Bash",
        "basis": basis,
        "taxonomy": "0.3.0",
        "sheet": "sheet_test.csv",
        "answers": {},
        "labels": labels,
    }


GOLD = [
    gold("g1", "content", {EFFECT: "read", UNTRUSTED: "yes", INJECTION: "no"}),
    gold("g2", "name_only", {EFFECT: "write", UNTRUSTED: "yes"}),
    gold("g3", "args", {EFFECT: "read", UNTRUSTED: "no"}),
    gold("g4", "args", {EFFECT: "delete"}),
]


def stored(
    span_id: str,
    label: str,
    answer: str,
    p: float,
    model: str = "fake/m",
    taxonomy: str = "0.3.0",
) -> StoredLabel:
    return StoredLabel(span_id, label, answer, p, model, taxonomy)


STORED = [
    stored("g1", EFFECT, "read", 0.9),
    stored("g2", EFFECT, "read", 0.8),  # wrong
    stored("g3", EFFECT, "read", 0.6),
    stored("g4", EFFECT, "delete", 0.9, taxonomy="0.2.1"),  # skipped
    stored("g1", UNTRUSTED, "yes", 0.7),
    stored("g2", UNTRUSTED, "no", 0.9),  # wrong; g3 is missing
    stored("g2", UNTRUSTED, "yes", 1.0, model="derived"),
    stored("not-in-gold", EFFECT, "read", 0.9),  # ignored
]


@pytest.fixture(scope="module")
def results() -> dict:
    out = score(GOLD, STORED, CARDS)
    return {r.model: r for r in out} | {"_order": [r.model for r in out]}


def by_label(model_score) -> dict:
    return {s.label: s for s in model_score.labels}


def test_derived_is_its_own_model_listed_last(results: dict) -> None:
    assert results["_order"] == ["fake/m", "derived"]


def test_accuracy_precision_recall_and_confusion(results: dict) -> None:
    effect = by_label(results["fake/m"])[EFFECT]
    assert effect.n == 3
    assert effect.accuracy == pytest.approx(2 / 3)
    answers = {a.answer: a for a in effect.answers}
    assert list(answers) == ["read", "write"]  # card order, seen answers only
    assert answers["read"].precision == pytest.approx(2 / 3)
    assert answers["read"].recall == 1.0
    assert answers["write"].precision is None  # never predicted
    assert answers["write"].recall == 0.0
    assert (answers["write"].gold_count, answers["write"].predicted_count) == (1, 0)
    assert effect.confusion == {("read", "read"): 2, ("write", "read"): 1}


def test_calibration(results: dict) -> None:
    effect = by_label(results["fake/m"])[EFFECT]
    # Bins 9, 8 and 6, one call each: |1-.9|, |0-.8|, |1-.6|, averaged.
    assert effect.ece == pytest.approx((0.1 + 0.8 + 0.4) / 3)
    untrusted = by_label(results["fake/m"])[UNTRUSTED]
    assert untrusted.ece == pytest.approx((0.3 + 0.9) / 2)


def test_p_of_one_falls_in_the_top_bin() -> None:
    pairs = [Pair("yes", "yes", 1.0, "args"), Pair("yes", "no", 0.95, "args")]
    # Both in the top bin: accuracy 0.5, mean p 0.975.
    assert expected_calibration_error(pairs) == pytest.approx(0.475)


def test_wilson_interval() -> None:
    # Reference values for 2/3 and 20/20 at z = 1.96.
    low, high = wilson_interval(2, 3)
    assert (low, high) == (
        pytest.approx(0.2077, abs=1e-4),
        pytest.approx(0.9385, abs=1e-4),
    )
    low, high = wilson_interval(20, 20)
    assert (low, high) == (pytest.approx(0.8389, abs=1e-4), 1.0)
    assert wilson_interval(0, 0) is None


def test_accuracy_carries_its_interval(results: dict) -> None:
    labels = by_label(results["fake/m"])
    assert labels[EFFECT].ci == wilson_interval(2, 3)
    assert labels[INJECTION].ci is None


def test_per_basis_breakdown(results: dict) -> None:
    effect = by_label(results["fake/m"])[EFFECT]
    assert effect.by_basis == {"args": (1, 1), "content": (1, 1), "name_only": (1, 0)}


def test_taxonomy_mismatch_is_skipped_and_counted(results: dict) -> None:
    assert results["fake/m"].skipped_taxonomy == 1
    assert by_label(results["fake/m"])[EFFECT].missing == 0  # skipped, not missing


def test_missing_gold_labels_are_counted_not_scored(results: dict) -> None:
    labels = by_label(results["fake/m"])
    assert labels[UNTRUSTED].missing == 1  # g3
    assert (labels[INJECTION].n, labels[INJECTION].missing) == (0, 1)
    assert labels[INJECTION].accuracy is None


def test_derived_row_covers_only_its_labels(results: dict) -> None:
    derived = by_label(results["derived"])
    assert list(derived) == [UNTRUSTED]
    assert derived[UNTRUSTED].accuracy == 1.0
    assert derived[UNTRUSTED].missing is None


def test_report_shows_every_section() -> None:
    report = format_report(score(GOLD, STORED, CARDS), len(GOLD))
    assert "only indicative" in report
    assert "=== fake/m ===" in report and "=== derived ===" in report
    assert "skipped 1 label(s): taxonomy differs from gold" in report
    assert "accuracy by basis" in report
    assert "gold \\ pred" in report
    assert "95% CI" in report and "20.8%–93.9%" in report


def to_label(x: StoredLabel) -> Label:
    return Label(
        span_id=x.span_id,
        session_id="session-1",
        label=x.label,
        answer=x.answer,
        p=x.p,
        basis="args",
        model=x.model,
        taxonomy=x.taxonomy,
        output_truncated=False,
        input_tokens=None,
        chunks=1,
    )


def test_cli_score_reads_the_store_and_gold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, gold_path = tmp_path / "labels.duckdb", tmp_path / "gold.jsonl"
    gold_path.write_text("".join(json.dumps(r) + "\n" for r in GOLD))
    with LabelStore(db) as store:
        store.write(to_label(x) for x in STORED)
        assert sorted(store.stored_labels(), key=repr) == sorted(STORED, key=repr)
    assert cli.main(["score", "--db", str(db), "--gold", str(gold_path)]) == 0
    out = capsys.readouterr().out
    assert "=== fake/m ===" in out and "=== derived ===" in out

    assert cli.main(["score", "--db", str(db), "--gold", str(tmp_path / "x")]) == 1


# --- call shapes -------------------------------------------------------------


def test_call_shape_normalizes_paths_whitespace_and_descriptions() -> None:
    from spangloss.eval.shapes import call_shape
    from spangloss.parse import ParsedToolCall
    from spangloss.parse.record import basis_for

    def bash(span_id: str, command: str, description: str) -> ParsedToolCall:
        args = {"command": command, "description": description}
        return ParsedToolCall(
            trace_id="t" * 32,
            span_id=span_id,
            parent_tool_span_id=None,
            session_id="s",
            start_time="2026-01-01T00:00:00.000000Z",
            end_time="2026-01-01T00:00:01.000000Z",
            tool_name="Bash",
            args=args,
            args_source="log",
            args_truncated=False,
            output=None,
            has_output=False,
            basis=basis_for(args, None),
            success=True,
            permission_decision="accept",
            permission_source="config",
        )

    a = bash("a", "cat /workspace/app/.env", "Show the env file")
    b = bash("b", "cat  app/.env ", "Read it")
    c = bash("c", "cat app/client.py", "Read it")
    assert call_shape(a) == call_shape(b)
    assert call_shape(a) != call_shape(c)


def test_repeated_shapes_count_once_in_shape_accuracy() -> None:
    records = [gold(f"r{i}", "content", {EFFECT: "read"}) for i in range(4)]
    # Three records of one shape, all wrong; one record of another shape, right.
    stored_labels = [stored("r0", EFFECT, "write", 0.9)] + [
        stored(f"r{i}", EFFECT, "write" if i < 3 else "read", 0.9) for i in (1, 2, 3)
    ]
    shapes = {"r0": "Read a", "r1": "Read a", "r2": "Read a", "r3": "Read b"}
    (result,) = score(records, stored_labels, CARDS, shapes)
    effect = by_label(result)[EFFECT]
    assert effect.accuracy == pytest.approx(1 / 4)  # per record
    assert (effect.shapes, effect.shape_accuracy) == (2, pytest.approx(1 / 2))


def test_unknown_shapes_count_as_their_own(results: dict) -> None:
    effect = by_label(results["fake/m"])[EFFECT]
    assert effect.shapes == effect.n  # no shapes given: every record is unique
    assert effect.shape_accuracy == pytest.approx(effect.accuracy)
