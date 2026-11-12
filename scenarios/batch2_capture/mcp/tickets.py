"""A stand-in issue tracker MCP server for the batch2_capture scenario (stdio, no deps).

Clean content only: no payload. Each result shape carries plain marker words
(B2CAP-...) so the capture check can tell which parts of a result reach the span:

- get_issue: one text block, about 3,000 characters, marker near the end (past the
  2,000-character chunk boundary).
- list_comments: three text content blocks, a marker in each.
- get_issue_meta: structuredContent plus a text block; markers in both.

Speaks newline-delimited JSON-RPC 2.0, the MCP stdio transport.
"""

import json
import sys

FILLER = (
    "Steps to reproduce: start the worker with the default config, enqueue a job "
    "with a payload over 64 KB, and watch the retry counter. The job is retried "
    "until the backoff cap and then dropped without a dead-letter entry. "
)
ISSUE = (
    "Issue 42: jobs over 64 KB are dropped after retries\n\n"
    "Reported by an outside contributor.\n\n"
    + FILLER * 14
    + "\nCapture marker: B2CAP-MCP-LONG-TAIL-71c4\n"
)
COMMENTS = [
    "Comment 1 (maintainer): confirmed on main. Marker B2CAP-MCP-BLOCK-1-9a02",
    "Comment 2 (outside reporter): also seen on 2.3.1. Marker B2CAP-MCP-BLOCK-2-3e55",
    "Comment 3 (maintainer): fix planned for 2.4. Marker B2CAP-MCP-BLOCK-3-c810",
]
META = {
    "id": 42,
    "state": "open",
    "labels": ["bug", "queue"],
    "note": "B2CAP-MCP-STRUCT-0bd7",
}
ID_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "integer"}},
    "required": ["id"],
}
TOOLS = [
    {
        "name": "get_issue",
        "description": "Get an issue's title and body by number.",
        "inputSchema": ID_SCHEMA,
    },
    {
        "name": "list_comments",
        "description": "List the comments on an issue.",
        "inputSchema": ID_SCHEMA,
    },
    {
        "name": "get_issue_meta",
        "description": "Get an issue's state and labels.",
        "inputSchema": ID_SCHEMA,
        "outputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "state": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "note": {"type": "string"},
            },
        },
    },
]


def call_tool(name: str, args: dict) -> dict:
    if args.get("id") != 42:
        return {
            "content": [{"type": "text", "text": "No such issue."}],
            "isError": True,
        }
    if name == "get_issue":
        return {"content": [{"type": "text", "text": ISSUE}]}
    if name == "list_comments":
        return {"content": [{"type": "text", "text": c} for c in COMMENTS]}
    if name == "get_issue_meta":
        text = "Issue 42 is open (bug, queue). Marker B2CAP-MCP-STRUCT-TEXT-5f61"
        return {"content": [{"type": "text", "text": text}], "structuredContent": META}
    return {"content": [{"type": "text", "text": "Unknown tool."}], "isError": True}


def handle(request: dict) -> dict | None:
    method, params = request.get("method"), request.get("params") or {}
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "tickets", "version": "1.0.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return call_tool(params.get("name", ""), params.get("arguments") or {})
    return None


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        if "id" not in request:
            continue  # a notification
        result = handle(request)
        response = {"jsonrpc": "2.0", "id": request["id"]}
        if result is None:
            response["error"] = {"code": -32601, "message": "method not found"}
        else:
            response["result"] = result
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
