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
- A Read, Grep or Glob whose target is a test-config file (TAXONOMY.md, data_class
  notes: .env.test, .env.example, .env.sample, .env.template) is sec.data_class
  none, unless its content was captured: at content basis the content could show a
  real secret, so the card is asked. The target is Read's file_path, Grep's path or
  glob, or the last segment of Glob's pattern, matched by exact file name. Both
  labelers answered secrets on every .env.test call in the hard_negatives runs
  (STATUS 2026-11-04).

The cards for derived labels are not asked. A derived label is written only where
its card would be asked (the runner applies the card's gating).
"""

from pathlib import PurePosixPath

from spangloss.parse.claude_code import MCP_TOOL, TOOL_LOADING
from spangloss.parse.record import ParsedToolCall

DERIVED_MODEL = "derived"
UNTRUSTED_SOURCE = "sec.trifecta.untrusted_source"
INTENT = "sec.intent"
DATA_CLASS = "sec.data_class"
TRIFECTA = (
    "sec.trifecta.private_source",
    UNTRUSTED_SOURCE,
    "sec.trifecta.sink",
)
# Claude Code's subagent spawn tool (rules/subagent_taint.py names the same set).
SPAWN_TOOLS = frozenset({"Agent"})
# TAXONOMY.md, data_class notes: names that point to test or example configuration.
TEST_CONFIG_NAMES = (".env.test", ".env.example", ".env.sample", ".env.template")
# Where each file tool names the files it touches.
TARGET_ARGS: dict[str, tuple[str, ...]] = {
    "Read": ("file_path",),
    "Grep": ("path", "glob"),
    "Glob": ("pattern",),
}


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
    if call.basis != "content" and targets_test_config(call):
        return {DATA_CLASS: "none"}
    return {}


def targets_test_config(call: ParsedToolCall) -> bool:
    """Whether a file tool's target, as its arguments name it, is a test-config file."""
    for key in TARGET_ARGS.get(call.tool_name, ()):
        value = call.args.get(key)
        if isinstance(value, str) and PurePosixPath(value).name in TEST_CONFIG_NAMES:
            return True
    return False
