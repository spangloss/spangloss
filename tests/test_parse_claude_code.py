import json
from pathlib import Path

import pytest

from spangloss.cli import main
from spangloss.parse import ParsedToolCall, parse_claude_code
from spangloss.parse.claude_code import _completes
from spangloss.parse.otlp import IDENTITY_KEYS, decode_value

FIXTURES = Path(__file__).parent / "fixtures"
TRACES = FIXTURES / "claude_code_traces.jsonl"
LOGS = FIXTURES / "claude_code_logs.jsonl"


@pytest.fixture(scope="module")
def calls() -> dict[str, ParsedToolCall]:
    return {c.span_id: c for c in parse_claude_code(TRACES, LOGS)}


def test_one_record_per_tool_span_in_start_order(
    calls: dict[str, ParsedToolCall],
) -> None:
    assert [c.tool_name for c in calls.values()] == [
        "Bash",
        "Read",
        "ExampleUnknownTool",
        "ToolSearch",
        "WebSearch",
        "Write",
        "Bash",
        "Edit",
        "Bash",
        "Agent",
    ]


def test_bash_args_come_from_tool_result_log(calls: dict[str, ParsedToolCall]) -> None:
    bash = calls["a000000000000001"]
    assert bash.args == {
        "command": "ls -la /tmp/example",
        "description": "List example files",
    }
    assert bash.args_source == "log"  # span copy is equal, so it contributes nothing
    assert not bash.args_truncated
    assert bash.output == "total 0"
    assert bash.has_output
    assert bash.basis == "content"
    assert bash.session_id == "11111111-2222-4333-8444-555555555555"
    assert bash.start_time == "2026-01-01T00:00:00.100000Z"


def test_bash_without_log_falls_back_to_full_command(
    calls: dict[str, ParsedToolCall],
) -> None:
    bash = calls["a000000000000007"]
    assert bash.args == {"command": "false"}
    assert bash.args_source == "span"
    assert bash.output is None
    assert bash.basis == "args"


def test_read_falls_back_to_span_file_path(calls: dict[str, ParsedToolCall]) -> None:
    read = calls["a000000000000002"]
    assert read.args == {"file_path": "/tmp/example/notes.txt"}
    assert read.args_source == "span"
    assert read.output == "line one\nline two\n"
    assert read.basis == "content"


def test_unknown_tool_uses_generic_mapping(calls: dict[str, ParsedToolCall]) -> None:
    unknown = calls["a000000000000003"]
    assert unknown.args == {
        "widget_id": "w-42"
    }  # envelope keys, agent_id included, are not args
    assert unknown.args_source == "span"
    assert unknown.output == "widget frobbed"
    assert unknown.basis == "content"


def test_span_with_no_output_event_or_args_is_name_only(
    calls: dict[str, ParsedToolCall],
) -> None:
    search = calls["a000000000000004"]
    assert search.args == {}
    assert search.args_source is None
    assert search.output is None
    assert not search.has_output
    assert search.basis == "name_only"


def test_output_without_args_is_not_content(calls: dict[str, ParsedToolCall]) -> None:
    web = calls["a000000000000005"]
    assert web.output == "made-up search results"
    assert web.has_output
    assert web.basis == "name_only"


def test_write_event_restores_truncated_content_and_is_not_output(
    calls: dict[str, ParsedToolCall],
) -> None:
    write = calls["a000000000000006"]
    assert write.args == {
        "file_path": "/tmp/example/out.txt",
        "content": "alpha\nbravo\ncharlie\ndelta\necho\nfoxtrot\ngolf\nhotel\n" * 4,
    }
    assert write.args_source == "merged"
    assert not write.args_truncated
    assert write.output is None
    assert write.basis == "args"


def test_edit_diff_is_kept_in_args_not_output(
    calls: dict[str, ParsedToolCall],
) -> None:
    edit = calls["a000000000000008"]
    assert edit.args == {
        "file_path": "/tmp/example/notes.txt",
        "diff": "@@ -1 +1 @@\n-line one\n+line 1\n",
    }
    assert edit.args_source == "span"
    assert edit.output is None
    assert not edit.has_output
    assert edit.basis == "args"


def test_truncated_log_command_is_replaced_by_complete_span_copy(
    calls: dict[str, ParsedToolCall],
) -> None:
    bash = calls["a000000000000009"]
    assert bash.args["command"].startswith("find /tmp/example -type f")
    assert bash.args["command"].endswith("-not -path '/tmp/example/skip-4/*'")
    assert len(bash.args["command"]) == 214
    assert bash.args["description"] == "Find text files"  # only in the log
    assert bash.args_source == "merged"
    assert not bash.args_truncated


def test_truncated_with_no_complete_copy_is_flagged(
    calls: dict[str, ParsedToolCall],
) -> None:
    agent = calls["a00000000000000a"]
    assert agent.args["prompt"].endswith("…[222 chars]")
    assert agent.args["subagent_type"] == "fake-subagent"
    assert agent.args_source == "log"  # span copy of subagent_type is equal
    assert agent.args_truncated


@pytest.mark.parametrize(
    ("logged", "value", "expected"),
    [
        ("abc…[6 chars]", "abcdef", True),
        ("abc…[7 chars]", "abcdef", False),  # length must match
        ("abx…[6 chars]", "abcdef", False),  # prefix must match
        ("abcdef", "abcdef", False),  # not truncated
    ],
)
def test_completes(logged: str, value: str, expected: bool) -> None:
    assert _completes(logged, value) is expected


def test_children_in_a_later_batch_are_joined(calls: dict[str, ParsedToolCall]) -> None:
    bash = calls["a000000000000001"]  # children on line 2
    assert bash.success is True
    assert (bash.permission_decision, bash.permission_source) == ("accept", "config")

    denied = calls["a000000000000007"]
    assert denied.success is False
    assert (denied.permission_decision, denied.permission_source) == (
        "reject",
        "user_temporary",
    )


def test_children_in_the_same_batch_are_joined(
    calls: dict[str, ParsedToolCall],
) -> None:
    unknown = calls["a000000000000003"]
    assert unknown.success is True
    assert unknown.permission_decision == "accept"


def test_identity_attributes_are_dropped_everywhere(
    calls: dict[str, ParsedToolCall],
) -> None:
    serialized = json.dumps([c.to_dict() for c in calls.values()])
    for key in IDENTITY_KEYS:
        assert key not in serialized
    assert "fake.person@example.com" not in serialized
    assert "acct_fake_000001" not in serialized


def test_without_logs_args_come_only_from_spans() -> None:
    calls = {c.span_id: c for c in parse_claude_code(TRACES)}
    assert calls["a000000000000001"].args == {"command": "ls -la /tmp/example"}
    assert calls["a000000000000001"].args_source == "span"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"stringValue": "x"}, "x"),
        ({"intValue": "42"}, 42),
        ({"boolValue": False}, False),
        ({"doubleValue": 0.5}, 0.5),
        (
            {"arrayValue": {"values": [{"stringValue": "a"}, {"intValue": "1"}]}},
            ["a", 1],
        ),
        (
            {"kvlistValue": {"values": [{"key": "k", "value": {"stringValue": "v"}}]}},
            {"k": "v"},
        ),
        ({}, None),
    ],
)
def test_decode_value(value: dict, expected: object) -> None:
    assert decode_value(value) == expected


def test_cli_writes_one_json_record_per_line(tmp_path: Path) -> None:
    traces = tmp_path / "traces.jsonl"
    traces.write_bytes(TRACES.read_bytes())
    (tmp_path / "logs.jsonl").write_bytes(
        LOGS.read_bytes()
    )  # picked up as the sibling logs file
    output = tmp_path / "parsed" / "tool_calls.jsonl"

    assert main(["parse", str(traces), "-o", str(output)]) == 0

    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(records) == 10
    assert records[0]["args_source"] == "log"
    assert set(records[0]) == set(ParsedToolCall.__dataclass_fields__)
