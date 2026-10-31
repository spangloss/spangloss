"""Screening for hosted backends: what must never leave the machine.

A call is refused when its args or output reference a .env file or match one
of detect-secrets' pattern detectors. The two high-entropy detectors are left
out: on their own they flag ordinary commands such as `ls -la /tmp/example`.
Reasons name the detector only, never the matched text.

The one exemption is the committed list of scenario fakes (scenarios/FAKE_SECRETS.txt,
docs/scenarios-design.md section 8), compared as exact strings:

- a detect-secrets hit is ignored only when its matched value is a listed fake;
- the .env rule, a path check, is waived only when the output is captured, holds
  at least one listed fake, and has no unlisted hit. A .env call without output
  is still refused, since nothing shows what the file holds.
"""

import json
import re
from collections.abc import Iterator
from typing import Any

from detect_secrets.core.plugins.util import get_mapping_from_secret_type_to_class
from detect_secrets.core.scan import scan_line
from detect_secrets.settings import transient_settings

from spangloss.parse.record import ParsedToolCall

ENV_FILE = re.compile(r"\.env(?:\.[\w.-]+)?(?![\w])")

PATTERN_PLUGINS = sorted(
    cls.__name__
    for cls in get_mapping_from_secret_type_to_class().values()
    if "HighEntropy" not in cls.__name__
)


def screen_for_hosted(
    call: ParsedToolCall, fakes: frozenset[str] = frozenset()
) -> str | None:
    """Reason to keep this call off a hosted backend, or None if it may be sent.

    fakes are the exact values exempt from screening; nothing else is exempt."""
    sources = {
        "args": list(_strings(call.args)),
        "output": [call.output] if call.output is not None else [],
    }
    unlisted: dict[str, list[str]] = {}
    with transient_settings({"plugins_used": [{"name": n} for n in PATTERN_PLUGINS]}):
        for where, texts in sources.items():
            unlisted[where] = sorted(
                {
                    s.type
                    for t in texts
                    for line in t.splitlines()
                    for s in scan_line(line)
                    if s.secret_value not in fakes
                }
            )
    shows_fake = call.output is not None and any(f in call.output for f in fakes)
    env_waived = shows_fake and not any(unlisted.values())
    for where, texts in sources.items():
        if not env_waived and any(ENV_FILE.search(t) for t in texts):
            return f"{where} references a .env file"
    for where, found in unlisted.items():
        if found:
            return f"{where} matches detect-secrets: {', '.join(found)}"
    return None


def _strings(value: Any) -> Iterator[str]:
    """Every key and string value, so nested arguments are scanned too."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif value is not None:
        yield json.dumps(value)
