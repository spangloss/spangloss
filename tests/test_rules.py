import json
from pathlib import Path
from typing import Any

import pytest

from spangloss import cli
from spangloss.parse import ParsedToolCall
from spangloss.parse.record import basis_for
from spangloss.questions.runner import Label
from spangloss.rules import RULES, Finding, subagent_taint
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


def test_tainted_spawn_with_a_handback_gets_one_finding_naming_it() -> None:
    findings = subagent_taint(SESSION, untrusted("fetch"))
    assert findings == [
        Finding("subagent_taint", "outer", "session-1", ("fetch",), ("back_out",))
    ]  # inner's subagent read nothing untrusted


def test_tainted_spawn_without_a_handback_still_gets_a_finding() -> None:
    # Headless runs emit no handback: the finding is anchored on the spawn.
    calls = [call("spawn", "Agent"), call("fetch", "WebFetch", "spawn")]
    findings = subagent_taint(calls, {"fetch": {UNTRUSTED: "yes"}})
    assert findings == [Finding("subagent_taint", "spawn", "session-1", ("fetch",))]
    assert findings[0].handback_span_ids == ()


def test_sub_subagent_taint_gives_one_finding_per_spawn() -> None:
    findings = {f.span_id: f for f in subagent_taint(SESSION, untrusted("search"))}
    assert set(findings) == {"outer", "inner"}  # spawns, not handbacks
    assert findings["inner"].trigger_span_ids == ("search",)
    assert findings["inner"].handback_span_ids == ("back_in",)
    assert findings["outer"].trigger_span_ids == ("search",)  # transitive
    assert findings["outer"].handback_span_ids == ("back_out",)  # direct child only


def test_untainted_spawn_has_no_finding() -> None:
    assert subagent_taint(SESSION, untrusted()) == []
    assert subagent_taint(SESSION, {}) == []  # unlabeled calls never trigger


def test_triggers_list_every_untrusted_call_in_order() -> None:
    findings = {
        f.span_id: f for f in subagent_taint(SESSION, untrusted("search", "fetch"))
    }
    assert findings["outer"].trigger_span_ids == ("fetch", "search")
    assert findings["inner"].trigger_span_ids == ("search",)


def test_several_handbacks_are_counted_once() -> None:
    calls = [
        call("spawn", "Agent"),
        call("fetch", "WebFetch", "spawn"),
        call("hb2", "SubagentHandback", "spawn"),
        call("hb1", "SubagentHandback", "spawn"),
    ]
    findings = subagent_taint(calls, {"fetch": {UNTRUSTED: "yes"}})
    assert len(findings) == 1
    assert findings[0].handback_span_ids == ("hb1", "hb2")


def test_handback_labels_and_calls_outside_spawns_do_not_count() -> None:
    # A spawn's own handback labeled yes does not taint the spawn, a stray handback
    # is not a spawn, and a call outside the spawn is not nested under it.
    calls = [
        call("spawn", "Agent"),
        call("hb", "SubagentHandback", "spawn"),
        call("odd", "SubagentHandback", "not-a-spawn"),
        call("fetch", "WebFetch"),
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
    assert [(f.span_id, f.trigger_span_ids) for f in findings] == [("a", ("b",))]


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
    first = [Finding("subagent_taint", "outer", "session-1", ("fetch",), ("back_out",))]
    with LabelStore(db) as store:
        store.replace_findings("subagent_taint", "kev", first)
        store.replace_findings("subagent_taint", "other-model", first)
        store.replace_findings("subagent_taint", "kev", [])  # rerun found nothing
        assert store.findings() == [
            (
                "subagent_taint",
                "outer",
                "session-1",
                ["fetch"],
                "other-model",
                ["back_out"],
            )
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
        assert [(r[1], r[3], r[5]) for r in store.findings()] == [
            ("inner", ["search"], ["back_in"]),
            ("outer", ["search"], ["back_out"]),
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
        assert [r[1] for r in store.findings()] == ["outer"]


def test_stores_from_before_handbacks_were_named_gain_the_column(
    tmp_path: Path,
) -> None:
    import duckdb

    db = tmp_path / "labels.duckdb"
    conn = duckdb.connect(str(db))
    conn.execute(
        "CREATE TABLE findings (rule VARCHAR NOT NULL, span_id VARCHAR NOT NULL,"
        " session_id VARCHAR, trigger_span_ids VARCHAR[] NOT NULL,"
        " model VARCHAR NOT NULL, found_at TIMESTAMP NOT NULL DEFAULT"
        " current_timestamp, PRIMARY KEY (rule, span_id, model))"
    )
    conn.execute(
        "INSERT INTO findings (rule, span_id, session_id, trigger_span_ids, model)"
        " VALUES ('subagent_taint', 'back_out', 'session-1', ['fetch'], 'kev')"
    )
    conn.close()
    with LabelStore(db) as store:
        assert store.findings() == [
            ("subagent_taint", "back_out", "session-1", ["fetch"], "kev", [])
        ]


# --- a missing label is not "no" ---------------------------------------------

INJECTION = "sec.injection"
KEV = "kev/kev-latest"


def _kev_answers(injection: dict[str, str] | None, tmp_path: Path) -> dict:
    """A trifecta session labeled by Kev through the store: the fetch is untrusted
    and a sink, the read is private. injection maps span -> answer; None leaves it
    out, as Kev's labels do (backends/support.py)."""
    trifecta = {
        "read": {"sec.trifecta.private_source": "yes"},
        "fetch": {UNTRUSTED: "yes", "sec.trifecta.sink": "yes"},
        "search": {UNTRUSTED: "yes", "sec.trifecta.sink": "yes"},
    }
    rows = []
    for c in SESSION:
        labels = dict(trifecta.get(c.span_id, {}))
        if injection is not None and c.span_id in injection:
            labels[INJECTION] = injection[c.span_id]
        for label, answer in labels.items():
            rows.append(
                Label(
                    c.span_id,
                    "session-1",
                    label,
                    answer,
                    0.9,
                    "content",
                    KEV,
                    "0.4.0",
                    False,
                    None,
                    1,
                )
            )
    db = tmp_path / f"labels-{len(list(tmp_path.iterdir()))}.duckdb"
    with LabelStore(db) as store:
        store.write(rows)
        return store.answers(KEV)


def reads_missing_injection_as_no(rule, tmp_path: Path) -> bool:
    """True when the rule depends on sec.injection and gives a call with injection
    missing the same result as injection no."""
    incoming = ("fetch", "search", "back_in", "back_out")
    absent = rule(SESSION, _kev_answers(None, tmp_path))
    no = rule(SESSION, _kev_answers(dict.fromkeys(incoming, "no"), tmp_path))
    for span in incoming:
        yes = dict.fromkeys(incoming, "no") | {span: "yes"}
        if rule(SESSION, _kev_answers(yes, tmp_path)) != no:  # injection matters
            return absent == no
    return False


@pytest.mark.parametrize("name", sorted(RULES))
def test_no_rule_reads_a_missing_injection_as_no(name: str, tmp_path: Path) -> None:
    assert not reads_missing_injection_as_no(RULES[name], tmp_path)


def test_the_check_catches_a_rule_that_reads_missing_as_no(tmp_path: Path) -> None:
    def careless(calls, answers):  # treats a missing injection as no
        return [
            Finding("careless", c.span_id, c.session_id, (c.span_id,))
            for c in calls
            if answers.get(c.span_id, {}).get(INJECTION, "no") == "yes"
        ]

    def careful(calls, answers):  # a missing injection is unknown, flagged as such
        return [
            Finding("careful", c.span_id, c.session_id, (c.span_id,))
            for c in calls
            if answers.get(c.span_id, {}).get(INJECTION) in ("yes", None)
            and c.tool_name in ("WebFetch", "WebSearch")
        ]

    assert reads_missing_injection_as_no(careless, tmp_path)
    assert not reads_missing_injection_as_no(careful, tmp_path)
