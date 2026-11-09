from pathlib import Path

from spangloss.eval.split import (
    build_split,
    load_split,
    side_of,
    span_hash,
    summary,
)
from spangloss.parse.record import ParsedToolCall


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
