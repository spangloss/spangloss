"""Screening for hosted backends: what must never leave the machine.

A call is refused when its args or output reference a .env file or match one
of detect-secrets' pattern detectors. The two high-entropy detectors are left
out: on their own they flag ordinary commands such as `ls -la /tmp/example`.
Reasons name the detector only, never the matched text.

The one exemption is by provenance: calls from a seeded-scenario run that verifies
(scenarios/provenance.py: current scenario version, committed fixture hashes, the
isolated network, only its own session's spans). For those calls:

- the .env rule is skipped: a scenario .env holds only listed fakes;
- a detect-secrets hit is still refused unless its matched value is on the committed
  fakes list (scenarios/FAKE_SECRETS.txt), so anything unexpected stays local.

Every other call, including every call from a real session, gets the strict screen
with no exemption.
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
    call: ParsedToolCall,
    fakes: frozenset[str] = frozenset(),
    scenario: bool = False,
) -> str | None:
    """Reason to keep this call off a hosted backend, or None if it may be sent.

    scenario is True only for calls from a verified scenario run; fakes (the listed
    fake values) are exempt only then. Real-session calls get the strict screen."""
    if not scenario:
        fakes = frozenset()
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
    for where, texts in sources.items():
        if not scenario and any(ENV_FILE.search(t) for t in texts):
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
