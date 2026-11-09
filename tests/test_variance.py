"""spangloss variance: answer changes across repeated labeling passes."""

from pathlib import Path

import pytest

from spangloss.cli import main
from spangloss.eval.score import StoredLabel
from spangloss.eval.variance import format_variance, variance, yes_no_labels
from spangloss.questions import load_cards
from spangloss.questions.runner import Label
from spangloss.store import LabelStore

CARDS = load_cards()
M = "kev/kev-latest"


def _x(span: str, label: str, answer: str, p: float, model: str = M) -> StoredLabel:
    return StoredLabel(span, label, answer, p, model, "0.4.0")


def _by_label(rows):
    return {r.label: r for r in rows}


def test_yes_no_and_two_step_labels():
    yes_no = yes_no_labels(CARDS)
    assert "sec.injection" in yes_no
    assert "sec.intent" not in yes_no
    assert "sec.data_class" not in yes_no  # stores its second step's answer


def test_yes_no_change_and_swing_in_p_yes():
    passes = [
        [_x("a", "sec.injection", "yes", 0.6), _x("b", "sec.injection", "no", 0.9)],
        [_x("a", "sec.injection", "no", 0.7), _x("b", "sec.injection", "no", 0.9)],
        [_x("a", "sec.injection", "yes", 0.55), _x("b", "sec.injection", "no", 0.8)],
    ]
    [r] = variance(passes, M, {"a", "b"}, CARDS)
    assert (r.pairs, r.changed, r.crossed, r.partial) == (2, 1, None, 0)
    # a: p(yes) 0.6, 0.3, 0.55
    assert r.max_swing == pytest.approx(0.3)


def test_choice_crossing_without_an_answer_change():
    passes = [
        [_x("a", "sec.intent", "retrieve", 0.62)],
        [_x("a", "sec.intent", "retrieve", 0.48)],
        [_x("a", "sec.intent", "retrieve", 0.60)],
    ]
    [r] = variance(passes, M, {"a"}, CARDS)
    assert (r.changed, r.crossed) == (0, 1)
    assert r.max_swing == pytest.approx(0.14)


def test_partial_pairs_other_models_and_spans_are_left_out():
    passes = [
        [
            _x("a", "sec.intent", "explore", 0.9),
            _x("b", "sec.intent", "explore", 0.9),  # missing from pass 2
            _x("a", "sec.intent", "modify", 0.9, model="jev/jev-latest"),
            _x("z", "sec.intent", "modify", 0.9),  # not in the gold set
            _x("a", "sec.effect", "read", 1.0, model="derived"),
        ],
        [_x("a", "sec.intent", "explore", 0.9)],
    ]
    rows = _by_label(variance(passes, M, {"a", "b"}, CARDS))
    assert set(rows) == {"sec.intent"}
    assert (rows["sec.intent"].pairs, rows["sec.intent"].partial) == (1, 1)
    assert rows["sec.intent"].changed == 0


def test_format_flags_different_checkpoints():
    text = format_variance("set, m:", [], ["ckpt@v1", "ckpt@v2", None])
    assert "warning: passes used different checkpoints: ckpt@v1, ckpt@v2" in text
    assert "not recorded" in text
    assert "ckpt@v1 in all 2 passes" not in text
    assert "in all 2 passes" in format_variance("t", [], ["c", "c"])


def test_cli_reports_each_gold_set(tmp_path: Path, capsys):
    gold_a = tmp_path / "a.jsonl"
    gold_b = tmp_path / "b.jsonl"
    gold_a.write_text('{"span_id": "a", "labels": {}}\n')
    gold_b.write_text('{"span_id": "b", "labels": {}}\n')
    dbs = []
    for i, answer in enumerate(["yes", "no"]):
        db = tmp_path / f"p{i}.duckdb"
        with LabelStore(db) as store:
            store.write(
                [
                    Label(
                        s,
                        None,
                        "sec.injection",
                        answer,
                        0.8,
                        "content",
                        M,
                        "0.4.0",
                        False,
                        10,
                        1,
                    )
                    for s in ("a", "b")
                ]
            )
        dbs += ["--db", str(db)]
    args = ["variance", *dbs, "--gold", f"one={gold_a}", "--gold", f"two={gold_b}"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "one, kev/kev-latest:" in out and "two, kev/kev-latest:" in out
    assert out.count("1 (100.0%)") == 2


def test_cli_needs_two_passes(tmp_path: Path, capsys):
    assert main(["variance", "--db", str(tmp_path / "x.duckdb")]) == 1
    assert "at least two --db" in capsys.readouterr().err
