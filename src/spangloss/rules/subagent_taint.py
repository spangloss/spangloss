"""Subagent taint (TAXONOMY.md 0.3.1, Detection rules).

A subagent's result reaches the parent agent when the spawn returns, and sometimes
also through a handback span; neither shows what the subagent read. If any call
nested under a spawn, directly or through further subagents, has
sec.trifecta.untrusted_source = yes, the spawn gets one finding. The finding is
anchored on the spawn, which is always captured, and also names the spawn's
handbacks when any were captured; they are not findings of their own. Headless runs
emit no handbacks, which is why the anchor is the spawn.
"""

from collections import defaultdict

from spangloss.parse.record import ParsedToolCall
from spangloss.rules.findings import Answers, Finding

RULE = "subagent_taint"
UNTRUSTED_SOURCE = "sec.trifecta.untrusted_source"

# Claude Code tool names, from captured telemetry.
SPAWN_TOOLS = frozenset({"Agent"})
HANDBACK_TOOLS = frozenset({"SubagentHandback"})


def subagent_taint(calls: list[ParsedToolCall], answers: Answers) -> list[Finding]:
    children: dict[str, list[ParsedToolCall]] = defaultdict(list)
    for c in calls:
        if c.parent_tool_span_id:
            children[c.parent_tool_span_id].append(c)

    findings = []
    for spawn in calls:
        if spawn.tool_name not in SPAWN_TOOLS:
            continue
        handbacks = sorted(
            c.span_id
            for c in children.get(spawn.span_id, [])
            if c.tool_name in HANDBACK_TOOLS
        )
        triggers = sorted(
            c.span_id
            for c in _nested(spawn.span_id, children)
            if c.span_id not in handbacks
            and answers.get(c.span_id, {}).get(UNTRUSTED_SOURCE) == "yes"
        )
        if triggers:
            findings.append(
                Finding(
                    RULE,
                    spawn.span_id,
                    spawn.session_id,
                    tuple(triggers),
                    tuple(handbacks),
                )
            )
    return findings


def _nested(
    span_id: str, children: dict[str, list[ParsedToolCall]]
) -> list[ParsedToolCall]:
    """Every call under span_id, following parent_tool_span_id down any depth."""
    found: list[ParsedToolCall] = []
    seen = {span_id}
    stack = [span_id]
    while stack:
        for child in children.get(stack.pop(), []):
            if child.span_id not in seen:
                seen.add(child.span_id)
                found.append(child)
                stack.append(child.span_id)
    return found
