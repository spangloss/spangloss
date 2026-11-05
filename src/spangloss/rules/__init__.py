"""Deterministic session-level detection rules over labels (TAXONOMY.md, Detection rules).

Rules read labels and span structure. They never change labels and never ask a model.
A missing label means the question wasn't asked, or the backend doesn't answer it
(backends/support.py: Kev and sec.injection); it is never "no". No rule reads
sec.injection today; tests/test_rules.py checks that any rule that does tells a
missing answer apart from "no".
"""

from spangloss.rules.findings import Answers, Finding, Rule
from spangloss.rules.subagent_taint import RULE as SUBAGENT_TAINT
from spangloss.rules.subagent_taint import subagent_taint

RULES: dict[str, Rule] = {SUBAGENT_TAINT: subagent_taint}

__all__ = ["RULES", "Answers", "Finding", "Rule", "subagent_taint"]
