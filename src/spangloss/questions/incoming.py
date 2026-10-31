"""Incoming content: what a call brings into the agent's context (TAXONOMY.md 0.3.1).

For most tools that is the output. A return-path tool delivers content in an
argument instead, so for it the incoming content is that argument, and only that.
Cards that require incoming content (sec.injection) are asked when it is present.
"""

from dataclasses import replace

from spangloss.parse.record import ParsedToolCall

# TAXONOMY.md, Input basis, "Incoming content": tool -> content-carrying argument.
RETURN_PATH_ARGS: dict[str, str] = {"SubagentHandback": "message"}

# Evidence key the labeler sees for a return-path tool's content. Ordinary tools
# keep "output", so their prompts are unchanged.
RETURNED_CONTENT_KEY = "returned_content"


def incoming_content(call: ParsedToolCall) -> str | None:
    arg = RETURN_PATH_ARGS.get(call.tool_name)
    if arg is None:
        return call.output
    value = call.args.get(arg)
    return value if isinstance(value, str) and value else None


def has_incoming(call: ParsedToolCall) -> bool:
    return incoming_content(call) is not None


def is_return_path(call: ParsedToolCall) -> bool:
    return call.tool_name in RETURN_PATH_ARGS


def incoming_view(call: ParsedToolCall) -> ParsedToolCall:
    """The call as an incoming-content card sees it: its incoming content in the
    output slot, and for a return-path tool, the content-carrying argument removed
    from args so the content is not sent twice."""
    arg = RETURN_PATH_ARGS.get(call.tool_name)
    if arg is None:
        return call
    args = {k: v for k, v in call.args.items() if k != arg}
    return replace(call, args=args, output=incoming_content(call))
