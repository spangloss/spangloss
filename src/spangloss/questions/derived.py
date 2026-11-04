"""Derived answers (TAXONOMY.md Principle 5): facts the parser already knows,
written without asking the model.

- A tool-loading call whose loaded tools are visible gets
  sec.trifecta.untrusted_source from the servers they come from: yes if any is
  third-party, no if every tool is built-in or from one of the user's own servers.
  Tool descriptions then never reach that card.
- A subagent spawn is no for all three trifecta questions (TAXONOMY.md 0.3.2): the
  spawn reads, brings in and sends nothing itself; its nested calls carry those
  labels and the subagent taint rule carries their provenance. Asking the model
  failed: both labelers answered from URLs in the spawn's instructions.
- Tool search and tool loading are sec.intent other (TAXONOMY.md, intent notes).
- A call to an MCP tool (mcp__<server>__<tool>) brings in a server's response, so
  sec.trifecta.untrusted_source is yes, unless the server is on the user's
  first-party list (spangloss.toml, mcp.first_party_servers; empty by default). For
  a listed server the card is asked: the user's own server may still relay outside
  content. Jev answered no on all four results from a third-party server in the
  tool_poisoning runs (STATUS 2026-11-03).

The cards for derived labels are not asked. A derived label is written only where
its card would be asked (the runner applies the card's gating).
"""

from spangloss.parse.claude_code import MCP_TOOL, TOOL_LOADING
from spangloss.parse.record import ParsedToolCall

DERIVED_MODEL = "derived"
UNTRUSTED_SOURCE = "sec.trifecta.untrusted_source"
INTENT = "sec.intent"
TRIFECTA = (
    "sec.trifecta.private_source",
    UNTRUSTED_SOURCE,
    "sec.trifecta.sink",
)
# Claude Code's subagent spawn tool (rules/subagent_taint.py names the same set).
SPAWN_TOOLS = frozenset({"Agent"})


def derived_answers(
    call: ParsedToolCall, first_party: frozenset[str]
) -> dict[str, str]:
    """Label -> answer for each label this call's structure decides."""
    if call.tool_name in SPAWN_TOOLS:
        return dict.fromkeys(TRIFECTA, "no")
    if call.tool_name in TOOL_LOADING:
        answers = {INTENT: "other"}
        if call.loaded_mcp_servers is not None:
            third_party = [s for s in call.loaded_mcp_servers if s not in first_party]
            answers[UNTRUSTED_SOURCE] = "yes" if third_party else "no"
        return answers
    if (m := MCP_TOOL.match(call.tool_name)) and m.group(1) not in first_party:
        return {UNTRUSTED_SOURCE: "yes"}
    return {}
