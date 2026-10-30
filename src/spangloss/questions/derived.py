"""Derived answers (TAXONOMY.md Principle 5): facts the parser already knows,
written without asking the model.

A tool-loading call whose loaded tools are visible gets sec.trifecta.untrusted_source
from the servers they come from: yes if any is third-party, no if every tool is
built-in or from one of the user's own servers. The card for that label is then not
asked, so tool descriptions never reach it.
"""

from spangloss.parse.record import ParsedToolCall

DERIVED_MODEL = "derived"
UNTRUSTED_SOURCE = "sec.trifecta.untrusted_source"


def derived_answers(
    call: ParsedToolCall, first_party: frozenset[str]
) -> dict[str, str]:
    """Label -> answer for each label this call's structure decides."""
    if call.loaded_mcp_servers is None:
        return {}
    third_party = [s for s in call.loaded_mcp_servers if s not in first_party]
    return {UNTRUSTED_SOURCE: "yes" if third_party else "no"}
