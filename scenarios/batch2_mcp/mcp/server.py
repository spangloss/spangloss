"""A stand-in MCP server for batch 2 (stdio, no deps). Its tools and their results
come from <name>.json beside this script, written per fixture set into the set's
held-out overlay (scenarios/_gen/batch2.py), so the payload text never sits in git.

<name>.json: {"server": "<name>", "tools": [{"name", "description", "inputSchema",
"blocks": ["text", ...]}]}. A tool returns its blocks as text content whatever its
arguments: results are text-only, never structuredContent (batch2-design.md,
capture check). A tool with "ack" instead of "blocks" returns that one line: the
stand-in send tool, whose arguments show whether the agent obeyed.

Speaks newline-delimited JSON-RPC 2.0, the MCP stdio transport.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
DATA = json.loads(HERE.with_suffix(".json").read_text(encoding="utf-8"))
TOOLS = {t["name"]: t for t in DATA["tools"]}


def call_tool(name: str) -> dict:
    tool = TOOLS.get(name)
    if tool is None:
        return {"content": [{"type": "text", "text": "Unknown tool."}], "isError": True}
    if "ack" in tool:
        return {"content": [{"type": "text", "text": tool["ack"]}]}
    return {"content": [{"type": "text", "text": b} for b in tool["blocks"]]}


def handle(request: dict) -> dict | None:
    method, params = request.get("method"), request.get("params") or {}
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": DATA["server"], "version": "1.0.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {
            "tools": [
                {k: t[k] for k in ("name", "description", "inputSchema")}
                for t in DATA["tools"]
            ]
        }
    if method == "tools/call":
        return call_tool(params.get("name", ""))
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
