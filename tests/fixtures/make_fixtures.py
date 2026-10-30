"""Generate synthetic Claude Code OTLP fixtures: real shapes, made-up values.

Run from the repo root: python tests/fixtures/make_fixtures.py tests/fixtures
"""

import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
TRACE = "aaaaaaaaaaaaaaaa0000000000000001"
SESSION = "11111111-2222-4333-8444-555555555555"
T0 = 1_767_225_600_000_000_000  # 2026-01-01T00:00:00Z
IDENTITY = {
    "user.email": "fake.person@example.com",
    "user.account_uuid": "00000000-0000-4000-8000-000000000001",
    "user.account_id": "acct_fake_000001",
    "organization.id": "00000000-0000-4000-8000-000000000002",
}


def v(x):
    if isinstance(x, bool):
        return {"boolValue": x}
    if isinstance(x, int):
        return {"intValue": str(x)}
    return {"stringValue": x}


def attrs(d):
    return [{"key": k, "value": v(x)} for k, x in d.items()]


def envelope(tool, tool_use_id):
    return {
        "duration_ms": 12,
        "gen_ai.conversation.id": "a000000000000000f",
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.provider.name": "anthropic",
        "gen_ai.tool.call.id": tool_use_id,
        "gen_ai.tool.name": tool,
        "session.id": SESSION,
        "span.type": "tool",
        "terminal.type": "test",
        "tool_name": tool,
        "tool_name_safe": tool,
        "tool_use_id": tool_use_id,
        "user.id": "fake-user-id-0001",
        **IDENTITY,
    }


def span(span_id, name, parent, start, a, events=()):
    return {
        "traceId": TRACE,
        "spanId": span_id,
        "parentSpanId": parent,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(T0 + start * 1_000_000),
        "endTimeUnixNano": str(T0 + start * 1_000_000 + 12_000_000),
        "attributes": attrs(a),
        "events": [
            {
                "timeUnixNano": str(T0 + start * 1_000_000 + 11_000_000),
                "name": n,
                "attributes": attrs(ea),
            }
            for n, ea in events
        ],
        "status": {},
    }


def children(parent, n, start, decision="accept", source="config", success=True):
    blocked = {
        "decision": decision,
        "source": source,
        "duration_ms": 1,
        "span.type": "tool.blocked_on_user",
        "session.id": SESSION,
        **IDENTITY,
    }
    execution = {
        "success": success,
        "duration_ms": 10,
        "span.type": "tool.execution",
        "session.id": SESSION,
        **IDENTITY,
    }
    if not success:
        execution |= {
            "error": "Shell command failed",
            "error.type": "tool_error",
            "error_class": "ShellError",
        }
    return [
        span(f"b{n:015x}", "claude_code.tool.blocked_on_user", parent, start, blocked),
        span(f"e{n:015x}", "claude_code.tool.execution", parent, start + 1, execution),
    ]


def request(spans):
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": attrs({"service.name": "claude-code"})},
                "scopeSpans": [
                    {"scope": {"name": "com.anthropic.claude_code"}, "spans": spans}
                ],
            }
        ]
    }


def truncate(value):
    """Truncate the way Claude Code's tool_input does: 128 chars, then the full length."""
    return value if len(value) <= 128 else f"{value[:128]}…[{len(value)} chars]"


ROOT = "f000000000000001"
WRITE_FULL = "alpha\nbravo\ncharlie\ndelta\necho\nfoxtrot\ngolf\nhotel\n" * 4
LONG_COMMAND = "find /tmp/example -type f -name '*.txt' " + " ".join(
    f"-not -path '/tmp/example/skip-{n}/*'" for n in range(5)
)
LONG_PROMPT = "Summarize the made-up example notes. " * 6
tool_spans = [
    # (span_id, tool, tool_use_id, extra span attrs, tool.output event attrs or None)
    (
        "a000000000000001",
        "Bash",
        "toolu_fake_bash_logged",
        {
            "full_command": "ls -la /tmp/example",
            "bash_argv0": "ls",
            "bash_command_class": "fake_class",
        },
        {"bash_command": "ls -la /tmp/example", "output": "total 0", **IDENTITY},
    ),
    (
        "a000000000000002",
        "Read",
        "toolu_fake_read",
        {"file_path": "/tmp/example/notes.txt"},
        {"file_path": "/tmp/example/notes.txt", "content": "line one\nline two\n"},
    ),
    (
        "a000000000000003",
        "ExampleUnknownTool",
        "toolu_fake_unknown",
        {"widget_id": "w-42", "agent_id": "fake-agent-1"},
        {"result": "widget frobbed"},
    ),
    ("a000000000000004", "ToolSearch", "toolu_fake_toolsearch", {}, None),
    (
        "a000000000000005",
        "WebSearch",
        "toolu_fake_websearch",
        {},
        {"output": "made-up search results"},
    ),
    (
        "a000000000000006",
        "Write",
        "toolu_fake_write",
        {"file_path": "/tmp/example/out.txt"},
        {"file_path": "/tmp/example/out.txt", "content": WRITE_FULL},
    ),
    (
        "a000000000000007",
        "Bash",
        "toolu_fake_bash_span_only",
        {
            "full_command": "false",
            "bash_argv0": "false",
            "bash_command_class": "fake_class",
        },
        None,
    ),
    (
        "a000000000000008",
        "Edit",
        "toolu_fake_edit_span_only",
        {"file_path": "/tmp/example/notes.txt"},
        {
            "file_path": "/tmp/example/notes.txt",
            "diff": "@@ -1 +1 @@\n-line one\n+line 1\n",
        },
    ),
    (
        "a000000000000009",
        "Bash",
        "toolu_fake_bash_truncated",
        {
            "full_command": LONG_COMMAND,
            "bash_argv0": "find",
            "bash_command_class": "fake_class",
        },
        {"bash_command": LONG_COMMAND, "output": ""},
    ),
    (
        "a00000000000000a",
        "Agent",
        "toolu_fake_agent",
        {"subagent_type": "fake-subagent"},
        None,
    ),
    (
        "a00000000000000b",
        "Read",
        "toolu_fake_nested_read",
        {"file_path": "/tmp/example/notes.txt", "agent_id": "fake-agent-1"},
        {"file_path": "/tmp/example/notes.txt", "content": "line one\n"},
    ),
    # Tool loading: a select list naming two made-up MCP servers and a built-in tool,
    # then a keyword query, which names no tools. Neither has a tool.output event.
    ("a00000000000000c", "ToolSearch", "toolu_fake_toolsearch_select", {}, None),
    ("a00000000000000d", "ToolSearch", "toolu_fake_toolsearch_keyword", {}, None),
]

# A call made inside the Agent call: its parent is the Agent's execution span
# (e00000000000000a), as in captured subagent calls.
NESTED_UNDER = {"a00000000000000b": "e00000000000000a"}

line1 = [
    span(ROOT, "claude_code.interaction", None, 0, {"session.id": SESSION, **IDENTITY})
]
line2 = []
for i, (sid, tool, tuid, extra, event) in enumerate(tool_spans, start=1):
    start = i * 100
    # The nested call arrives in a later batch than the Agent's tool span.
    (line2 if sid in NESTED_UNDER else line1).append(
        span(
            sid,
            "claude_code.tool",
            NESTED_UNDER.get(sid, ROOT),
            start,
            envelope(tool, tuid) | extra,
            [("tool.output", event)] if event is not None else [],
        )
    )
    kw = (
        {"decision": "reject", "source": "user_temporary", "success": False}
        if tool == "Bash" and i == 7
        else {}
    )
    kids = children(sid, i, start + 1, **kw)
    # The unknown tool's children arrive in the same batch; all others in a later batch.
    (line1 if tool == "ExampleUnknownTool" else line2).extend(kids)

(out / "claude_code_traces.jsonl").write_text(
    json.dumps(request(line1)) + "\n" + json.dumps(request(line2)) + "\n"
)


def log(event, a):
    return {
        "timeUnixNano": str(T0),
        "observedTimeUnixNano": str(T0),
        "body": {"stringValue": f"claude_code.{event}"},
        "attributes": attrs(
            {
                "event.name": event,
                "session.id": SESSION,
                "user.id": "fake-user-id-0001",
                **IDENTITY,
            }
            | a
        ),
    }


logs = [
    log("user_prompt", {"prompt": "<REDACTED>", "prompt_length": "12"}),
    log(
        "tool_result",
        {
            "tool_name": "Bash",
            "tool_use_id": "toolu_fake_bash_logged",
            "success": "true",
            "tool_input": json.dumps(
                {"command": "ls -la /tmp/example", "description": "List example files"}
            ),
            "tool_parameters": json.dumps(
                {
                    "bash_command": "ls",
                    "full_command": "ls -la /tmp/example",
                    "description": "List example files",
                }
            ),
        },
    ),
    log(
        "tool_result",
        {
            "tool_name": "Write",
            "tool_use_id": "toolu_fake_write",
            "success": "true",
            "tool_input": json.dumps(
                {"file_path": "/tmp/example/out.txt", "content": truncate(WRITE_FULL)}
            ),
        },
    ),
    log(
        "tool_result",
        {
            "tool_name": "Bash",
            "tool_use_id": "toolu_fake_bash_truncated",
            "success": "true",
            "tool_input": json.dumps(
                {"command": truncate(LONG_COMMAND), "description": "Find text files"}
            ),
        },
    ),
    log(
        "tool_result",
        {
            "tool_name": "Agent",
            "tool_use_id": "toolu_fake_agent",
            "success": "true",
            "tool_input": json.dumps(
                {
                    "description": "Summarize notes",
                    "prompt": truncate(LONG_PROMPT),
                    "subagent_type": "fake-subagent",
                }
            ),
        },
    ),
    log(
        "tool_result",
        {
            "tool_name": "ToolSearch",
            "tool_use_id": "toolu_fake_toolsearch_select",
            "success": "true",
            "tool_input": json.dumps(
                {
                    "query": "select:mcp__example-notes__search,"
                    "mcp__fake_vendor_tools__lookup,mcp__example-notes__fetch,Read",
                    "max_results": 5,
                }
            ),
        },
    ),
    log(
        "tool_result",
        {
            "tool_name": "ToolSearch",
            "tool_use_id": "toolu_fake_toolsearch_keyword",
            "success": "true",
            "tool_input": json.dumps({"query": "example notes", "max_results": 5}),
        },
    ),
    log(
        "tool_result",
        {
            "tool_name": "Bash",
            "tool_use_id": "toolu_fake_no_matching_span",
            "success": "true",
            "tool_input": json.dumps({"command": "echo orphan"}),
        },
    ),
]
(out / "claude_code_logs.jsonl").write_text(
    json.dumps(
        {
            "resourceLogs": [
                {
                    "resource": {"attributes": attrs({"service.name": "claude-code"})},
                    "scopeLogs": [
                        {
                            "scope": {"name": "com.anthropic.claude_code.events"},
                            "logRecords": logs,
                        }
                    ],
                }
            ]
        }
    )
    + "\n"
)
print("ok")
