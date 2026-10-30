import json
from pathlib import Path
from typing import Any

import pytest

from spangloss import cli
from spangloss.parse import ParsedToolCall
from spangloss.parse.record import basis_for
from spangloss.questions.runner import Label
from spangloss.rules import Finding, subagent_taint
from spangloss.store import LabelStore

UNTRUSTED = "sec.trifecta.untrusted_source"


def call(span_id: str, tool_name: str, parent: str | None = None) -> ParsedToolCall:
    base: dict[str, Any] = {
        "trace_id": "t" * 32,
        "span_id": span_id,
        "parent_tool_span_id": parent,
        "session_id": "session-1",
        "start_time": "2026-01-01T00:00:00.000000Z",
        "end_time": "2026-01-01T00:00:01.000000Z",
        "tool_name": tool_name,
        "args": {},
        "args_source": None,
        "args_truncated": False,
        "output": None,
        "has_output": False,
        "success": True,
        "permission_decision": "accept",
        "permission_source": "config",
    }
    base["basis"] = basis_for(base["args"], base["output"])
    return ParsedToolCall(**base)


# outer Agent
# ├── read     Read
# ├── fetch    WebFetch
# ├── inner    Agent
# │   ├── search   WebSearch
# │   └── back_in  SubagentHandback (to the outer subagent)
# └── back_out SubagentHandback (to the main agent)
SESSION = [
    call("outer", "Agent"),
    call("read", "Read", "outer"),
    call("fetch", "WebFetch", "outer"),
    call("inner", "Agent", "outer"),
    call("search", "WebSearch", "inner"),
    call("back_in", "SubagentHandback", "inner"),
    call("back_out", "SubagentHandback", "outer"),
]


def untrusted(*yes: str) -> dict[str, dict[str, str]]:
    return {
        c.span_id: {UNTRUSTED: "yes" if c.span_id in yes else "no"} for c in SESSION
    }


def test_untrusted_call_under_a_spawn_taints_its_handback() -> None:
    findings = subagent_taint(SESSION, untrusted("fetch"))
    assert findings == [
        Finding("subagent_taint", "back_out", "session-1", ("fetch",))
    ]  # back_in's subagent read nothing untrusted


def test_sub_subagent_taint_reaches_both_handbacks() -> None:
    findings = {f.span_id: f for f in subagent_taint(SESSION, untrusted("search"))}
    assert findings["back_in"].trigger_span_ids == ("search",)
    assert findings["back_out"].trigger_span_ids == ("search",)  # transitive


def test_triggers_list_every_untrusted_call_in_order() -> None:
    findings = {
        f.span_id: f for f in subagent_taint(SESSION, untrusted("search", "fetch"))
    }
    assert findings["back_out"].trigger_span_ids == ("fetch", "search")
    assert findings["back_in"].trigger_span_ids == ("search",)


def test_no_untrusted_calls_no_findings() -> None:
    assert subagent_taint(SESSION, untrusted()) == []
    assert subagent_taint(SESSION, {}) == []  # unlabeled calls never trigger


def test_handback_own_label_and_other_parents_do_not_count() -> None:
    # A handback labeled yes does not taint itself, and a handback whose parent is
    # not a spawn is not matched.
    calls = [
        call("spawn", "Agent"),
        call("hb", "SubagentHandback", "spawn"),
        call("odd", "SubagentHandback", "not-a-spawn"),
        call("fetch", "WebFetch"),  # not nested under the spawn
    ]
    answers = {s: {UNTRUSTED: "yes"} for s in ("hb", "odd", "fetch")}
    assert subagent_taint(calls, answers) == []


def test_parent_cycles_terminate() -> None:
    calls = [
        call("a", "Agent", "b"),
        call("b", "WebFetch", "a"),
        call("hb", "SubagentHandback", "a"),
    ]
    findings = subagent_taint(calls, {"b": {UNTRUSTED: "yes"}})
    assert [f.trigger_span_ids for f in findings] == [("b",)]


# --- store and CLI -----------------------------------------------------------


def label(span_id: str, answer: str, model: str) -> Label:
    return Label(
        span_id=span_id,
        session_id="session-1",
        label=UNTRUSTED,
        answer=answer,
        p=1.0 if model == "derived" else 0.9,
        basis="name_only",
        model=model,
        taxonomy="0.3.0",
        output_truncated=False,
        input_tokens=None,
        chunks=0 if model == "derived" else 1,
    )


def write_calls(tmp_path: Path, calls: list[ParsedToolCall]) -> Path:
    parsed = tmp_path / "tool_calls.jsonl"
    parsed.write_text("".join(json.dumps(c.to_dict()) + "\n" for c in calls))
    return parsed


def test_answers_include_derived_labels(tmp_path: Path) -> None:
    db = tmp_path / "labels.duckdb"
    with LabelStore(db) as store:
        store.write([label("read", "no", "kev"), label("search", "yes", "derived")])
        store.write([label("read", "yes", "other-model")])
        assert store.models() == ["kev", "other-model"]
        assert store.answers("kev") == {
            "read": {UNTRUSTED: "no"},
            "search": {UNTRUSTED: "yes"},
        }


def test_findings_are_replaced_per_rule_and_model(tmp_path: Path) -> None:
    db = tmp_path / "labels.duckdb"
    first = [Finding("subagent_taint", "back_out", "session-1", ("fetch",))]
    with LabelStore(db) as store:
        store.replace_findings("subagent_taint", "kev", first)
        store.replace_findings("subagent_taint", "other-model", first)
        store.replace_findings("subagent_taint", "kev", [])  # rerun found nothing
        assert store.findings() == [
            ("subagent_taint", "back_out", "session-1", ["fetch"], "other-model")
        ]
        with pytest.raises(ValueError, match="another rule"):
            store.replace_findings("some_other_rule", "kev", first)


def test_cli_rules_writes_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parsed = write_calls(tmp_path, SESSION)
    db = tmp_path / "labels.duckdb"
    with LabelStore(db) as store:
        # The sub-subagent's call is untrusted only by a derived label.
        store.write([label("read", "no", "kev"), label("search", "yes", "derived")])
    args = ["rules", "--input", str(parsed), "--db", str(db)]
    assert cli.main(args) == 0
    assert "subagent_taint: 2 finding(s) over kev labels" in capsys.readouterr().err
    with LabelStore(db) as store:
        assert [(r[1], r[3]) for r in store.findings()] == [
            ("back_in", ["search"]),
            ("back_out", ["search"]),
        ]


def test_cli_rules_needs_a_model_when_ambiguous(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parsed = write_calls(tmp_path, SESSION)
    db = tmp_path / "labels.duckdb"
    args = ["rules", "--input", str(parsed), "--db", str(db)]
    with LabelStore(db):
        pass
    assert cli.main(args) == 1  # no labels at all
    with LabelStore(db) as store:
        store.write([label("fetch", "yes", "kev"), label("fetch", "no", "other")])
    assert cli.main(args) == 1
    assert "choose a model with --model" in capsys.readouterr().err
    assert cli.main([*args, "--model", "missing"]) == 1
    assert cli.main([*args, "--model", "kev"]) == 0
    with LabelStore(db) as store:
        assert [r[1] for r in store.findings()] == ["back_out"]
