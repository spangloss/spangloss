"""Which tool calls provably come from a seeded-scenario run.

The Jev screen relaxes only for these (backends/safety.py). A run counts when its
record shows it ran the current version of its scenario, on the committed fixtures,
in the isolated network, and holds only its own session's spans:

- run.json's scenario version equals the scenario's current version;
- every fixture's pre-session hash equals the committed fixture's hash;
- the network check found the internet blocked and the proxy refusing its probe;
- every parsed call carries the run's session id.

A call from a real session can never qualify: its span id isn't in any such run.
"""

import json
from pathlib import Path

from spangloss.parse.record import ParsedToolCall
from spangloss.scenarios.spec import SCENARIOS_DIR, ScenarioError, load_scenario

RUNS_DIR = Path("data/scenarios/runs")
ISOLATED = {"internet": "blocked", "proxy_probe": "403"}


def verified_run(run_dir: Path, root: Path = SCENARIOS_DIR) -> list[str] | None:
    """The run's span ids if it verifies, else None."""
    try:
        run = json.loads((run_dir / "run.json").read_text())
        scenario = load_scenario(
            run["scenario"], root=root, fixture_set=run.get("fixture_set")
        )
        lines = (run_dir / "tool_calls.jsonl").read_text().splitlines()
    except (OSError, KeyError, ValueError, ScenarioError):
        return None
    if run.get("version") != scenario.version:
        return None
    if run.get("network_check") != ISOLATED:
        return None
    listing = run.get("pre_listing") or {}
    for fixture in scenario.fixtures.values():
        if (listing.get(fixture.path) or {}).get("sha256") != fixture.sha256:
            return None
    calls = [ParsedToolCall(**json.loads(line)) for line in lines if line.strip()]
    if any(c.session_id != run.get("session_id") for c in calls):
        return None
    return [c.span_id for c in calls]


def verified_scenario_spans(
    runs_dir: Path = RUNS_DIR, root: Path = SCENARIOS_DIR
) -> frozenset[str]:
    spans: set[str] = set()
    for run_dir in sorted(runs_dir.glob("*")) if runs_dir.is_dir() else []:
        spans |= set(verified_run(run_dir, root) or [])
    return frozenset(spans)
