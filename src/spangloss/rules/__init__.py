"""Deterministic session-level detection rules over labels (TAXONOMY.md, Detection rules).

Rules read labels and span structure. They never change labels and never ask a model.
"""

from spangloss.rules.findings import Answers, Finding, Rule
from spangloss.rules.subagent_taint import RULE as SUBAGENT_TAINT
from spangloss.rules.subagent_taint import subagent_taint

RULES: dict[str, Rule] = {SUBAGENT_TAINT: subagent_taint}

__all__ = ["RULES", "Answers", "Finding", "Rule", "subagent_taint"]
