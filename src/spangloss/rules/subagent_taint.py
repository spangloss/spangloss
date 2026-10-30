"""Subagent taint (TAXONOMY.md, Detection rules).

A subagent's handback is labeled from its own span, which cannot show what the
subagent read. If any call nested under the spawn that the handback returns from,
directly or through further subagents, has sec.trifecta.untrusted_source = yes, the
handback gets a finding: it brings untrusted content into the parent's context.
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
    by_id = {c.span_id: c for c in calls}
    children: dict[str, list[ParsedToolCall]] = defaultdict(list)
    for c in calls:
        if c.parent_tool_span_id:
            children[c.parent_tool_span_id].append(c)

    findings = []
    for handback in calls:
        if handback.tool_name not in HANDBACK_TOOLS:
            continue
        spawn = by_id.get(handback.parent_tool_span_id or "")
        if spawn is None or spawn.tool_name not in SPAWN_TOOLS:
            continue
        triggers = sorted(
            c.span_id
            for c in _nested(spawn.span_id, children)
            if c.span_id != handback.span_id
            and answers.get(c.span_id, {}).get(UNTRUSTED_SOURCE) == "yes"
        )
        if triggers:
            findings.append(
                Finding(RULE, handback.span_id, handback.session_id, tuple(triggers))
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
