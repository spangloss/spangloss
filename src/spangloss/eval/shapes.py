"""Call shapes: what makes two tool calls the same call for scoring.

Seeded scenarios repeat calls across runs and prompt variants (the same Read of
app/.env, say). Scoring each record alone lets one repeated call dominate a label,
so score also reports results per unique call shape: the tool name plus its
normalized arguments.

Normalization: container paths are made workspace-relative (/workspace/ dropped,
/home/agent/ -> ~/), whitespace is collapsed, and arguments that describe a call
rather than change it (a Bash description) are dropped.
"""

import json
import re
from typing import Any

from spangloss.parse.record import ParsedToolCall

# Free-text arguments that don't change what a call does.
DESCRIPTIVE_ARGS = frozenset({"description"})
WHITESPACE = re.compile(r"\s+")


def call_shape(call: ParsedToolCall) -> str:
    args = {k: _normalize(v) for k, v in call.args.items() if k not in DESCRIPTIVE_ARGS}
    return f"{call.tool_name} {json.dumps(args, sort_keys=True, ensure_ascii=False)}"


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        text = value.replace("/workspace/", "").replace("/home/agent/", "~/")
        return WHITESPACE.sub(" ", text).strip()
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value
