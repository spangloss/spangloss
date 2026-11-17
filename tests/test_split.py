from pathlib import Path

import pytest

from spangloss.eval.split import (
    BATCH2_SCENARIOS,
    BATCH2_SLICE,
    ROUND2_CALIB_SLICE,
    ROUND2_TEST_SCENARIOS,
    ROUND2_TEST_SLICE,
    ROUND2_TRAIN_SCENARIOS,
    ROUND2_TRAIN_SLICE,
    SPLIT_V3_FILE,
    SPLIT_V4_FILE,
    HeldOutError,
    all_set_slices,
    build_split,
    calibration_sets,
    check_sets,
    load_split,
    set_slices,
    side_of,
    slice_for,
    span_hash,
    summary,
    use_allowed,
    v4_body,
)
from spangloss.parse.record import ParsedToolCall
from spangloss.scenarios import load_scenario


def _call(span_id: str, tool: str, args: dict) -> ParsedToolCall:
    return ParsedToolCall(
        trace_id="t",
        span_id=span_id,
        parent_tool_span_id=None,
        session_id="s",
        start_time="2026-11-01T00:00:00Z",
        end_time="2026-11-01T00:00:01Z",
        tool_name=tool,
        args=args,
        args_source="log",
        args_truncated=False,
        output=None,
        has_output=False,
        basis="args",
        success=True,
        permission_decision=None,
        permission_source=None,
    )


def _gold(span_id: str, **labels: str) -> dict:
    return {"span_id": span_id, "labels": labels}


CALLS = {
    "real1": _call("real1", "Read", {"file_path": "/home/me/notes.md"}),
    "pd_precise": _call("pd_precise", "Read", {"file_path": "/workspace/README.md"}),
    "pd_loose_same": _call("pd_loose_same", "Read", {"file_path": "README.md"}),
    "pd_loose_other": _call("pd_loose_other", "Glob", {"pattern": "*.js"}),
    "hn_held": _call("hn_held", "Read", {"file_path": ".env.test"}),
    "hn_kept": _call("hn_kept", "Read", {"file_path": "AGENTS.md"}),
    "curl": _call("curl", "Bash", {"command": "curl http://x"}),
}
VARIANT = {
    "pd_precise": ("poisoned_dependency", "precise"),
    "pd_loose_same": ("poisoned_dependency", "loose"),
    "pd_loose_other": ("poisoned_dependency", "loose"),
    "hn_held": ("hard_negatives", "default"),
    "hn_kept": ("hard_negatives", "default"),
    "curl": ("web_injection_curl", "default"),
}
REAL = [_gold("real1", **{"sec.effect": "read"})]
SCEN = [_gold(s, **{"sec.injection": "no"}) for s in VARIANT]


def test_split_rules():
    side = build_split(REAL, SCEN, CALLS, VARIANT)
    assert side == {
        "real1": "test",  # every real-session call
        "pd_precise": "test",  # a held-out variant
        "pd_loose_same": "test",  # shares a shape with a test call
        "pd_loose_other": "train",
        "hn_held": "test",  # a held-out hard_negatives shape
        "hn_kept": "train",
        "curl": "test",  # every variant of web_injection_curl
    }


def test_summary_counts_shapes_once():
    side = build_split(REAL, SCEN, CALLS, VARIANT)
    counts = summary(side, REAL + SCEN, CALLS)
    # pd_precise and pd_loose_same normalize to one shape.
    assert counts["test"]["calls"] == 5
    assert counts["test"]["shapes"] == 4
    assert counts["train"]["labels"] == {"sec.injection": {"no": 2}}


def test_load_split_by_hash(tmp_path: Path):
    path = tmp_path / "split.txt"
    path.write_text(f"test {span_hash('a')}\ntrain {span_hash('b')}\n")
    split = load_split(path)
    assert side_of("a", split) == "test"
    assert side_of("b", split) == "train"
    assert side_of("c", split) is None


# --- split v2: batch-1 fixture sets ----------------------------------------------


def test_split_v2_was_fixed_from_the_scenarios_sets() -> None:
    sets = {
        name: {s.id: s.split for s in load_scenario(name).fixture_sets}
        for name in ("repo_review", "command_output", "hard_negatives_v2")
    }
    assert check_sets(set_slices(), sets) == []
    slices = set_slices()
    assert sorted(k[1] for k, v in slices.items() if v == "batch1_test") == [
        "co01", "co02", "co09", "co17", "hn03", "hn08", "hn12",
        "rr01", "rr02", "rr03", "rr04", "rr11",
    ]  # fmt: skip


def test_a_set_on_the_wrong_side_or_missing_is_reported() -> None:
    slices = {("s", "a1"): "train", ("s", "a2"): "batch1_test"}
    assert check_sets(slices, {"s": {"a1": "train", "a2": "test"}}) == []
    problems = check_sets(slices, {"s": {"a1": "test", "a3": "train"}})
    assert len(problems) == 3


# --- split v3: batch 2, held out whole ---------------------------------------------


def test_split_v3_holds_every_batch2_set_in_batch2_eval() -> None:
    sets = {
        name: {s.id: s.split for s in load_scenario(name).fixture_sets}
        for name in sorted(BATCH2_SCENARIOS)
    }
    v3 = set_slices(SPLIT_V3_FILE)
    assert check_sets(v3, sets) == []
    assert len(v3) == 24 and set(v3.values()) == {BATCH2_SLICE}


def test_check_sets_covers_v2_and_v3_together() -> None:
    names = (
        "repo_review",
        "command_output",
        "hard_negatives_v2",
        *sorted(BATCH2_SCENARIOS),
        *sorted(ROUND2_TRAIN_SCENARIOS | ROUND2_TEST_SCENARIOS),
    )
    sets = {n: {s.id: s.split for s in load_scenario(n).fixture_sets} for n in names}
    assert check_sets(all_set_slices(), sets) == []


def test_a_batch2_set_takes_batch2_eval_whatever_its_split() -> None:
    assert slice_for("batch2_mcp", "test") == BATCH2_SLICE
    assert slice_for("batch2_web", "train") == BATCH2_SLICE
    assert slice_for("repo_review", "test") == "batch1_test"


def test_build_split_refuses_batch2_gold() -> None:
    gold = [{"span_id": "b1", "source": {"scenario": "batch2_mcp"}}]
    with pytest.raises(HeldOutError, match="batch2_mcp"):
        build_split([], gold, {}, {"b1": ("batch2_mcp", "digest")})
    # Even with the record's source missing, the run it came from decides.
    with pytest.raises(HeldOutError, match="batch2_web"):
        build_split([], [{"span_id": "b2"}], {}, {"b2": ("batch2_web", "review")})


# --- split v4: round 2 ---------------------------------------------------------------


def _round2_sets() -> dict[str, dict[str, str]]:
    return {
        n: {s.id: s.split for s in load_scenario(n).fixture_sets}
        for n in sorted(ROUND2_TRAIN_SCENARIOS | ROUND2_TEST_SCENARIOS)
    }


def test_split_v4_file_matches_the_round2_scenarios() -> None:
    import json

    assert json.loads(SPLIT_V4_FILE.read_text(encoding="utf-8")) == v4_body(
        _round2_sets()
    )


def test_split_v4_holds_all_of_group_b_in_round2_test() -> None:
    v4 = set_slices(SPLIT_V4_FILE)
    group_b = {k: v for k, v in v4.items() if k[0] in ROUND2_TEST_SCENARIOS}
    assert len(group_b) == 24 and set(group_b.values()) == {ROUND2_TEST_SLICE}
    assert ROUND2_TEST_SLICE not in {
        v for k, v in v4.items() if k[0] in ROUND2_TRAIN_SCENARIOS
    }


def test_split_v4_puts_exactly_the_calibration_sets_in_round2_calib() -> None:
    v4 = set_slices(SPLIT_V4_FILE)
    calib = {k[1] for k, v in v4.items() if v == ROUND2_CALIB_SLICE}
    assert calib == calibration_sets() and len(calib) == 10
    train = [k for k, v in v4.items() if v == ROUND2_TRAIN_SLICE]
    assert len(train) == 50 and not {k[1] for k in train} & calib


def test_a_group_b_set_takes_round2_test_whatever_its_split() -> None:
    for split in ("train", "test", "local"):
        assert slice_for("round2b_mcp", split, "r2bm01") == ROUND2_TEST_SLICE
    assert slice_for("round2b_web", "train") == ROUND2_TEST_SLICE


def test_a_group_a_set_needs_its_id_for_a_slice() -> None:
    cal = min(calibration_sets())
    scenario = "round2a_mcp" if cal.startswith("r2am") else "round2a_web"
    assert slice_for(scenario, "local", cal) == ROUND2_CALIB_SLICE
    with pytest.raises(ValueError, match="depends on its id"):
        slice_for("round2a_mcp", "local")


def test_round2_test_may_be_used_for_nothing() -> None:
    for purpose in ("train", "calibrate", "select"):
        assert not use_allowed(ROUND2_TEST_SLICE, purpose)
        assert not use_allowed(BATCH2_SLICE, purpose)
        assert not use_allowed("batch1_test", purpose)
    assert use_allowed(ROUND2_TRAIN_SLICE, "train")
    assert not use_allowed(ROUND2_TRAIN_SLICE, "calibrate")
    assert use_allowed(ROUND2_CALIB_SLICE, "calibrate")
    assert not use_allowed(ROUND2_CALIB_SLICE, "train")


def test_build_split_refuses_group_b_gold() -> None:
    gold = [{"span_id": "g1", "source": {"scenario": "round2b_web"}}]
    with pytest.raises(HeldOutError, match="round2b_web"):
        build_split([], gold, {}, {"g1": ("round2b_web", "review")})
    with pytest.raises(HeldOutError, match="round2b_mcp"):
        build_split([], [{"span_id": "g2"}], {}, {"g2": ("round2b_mcp", "digest")})
