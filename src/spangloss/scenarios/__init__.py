"""Seeded scenarios: scripted, isolated Claude Code sessions with expected labels.

See docs/scenarios-design.md. spec loads scenario.yaml, runner runs a session in
a container, check matches its calls to the expectations and writes gold, and
fakes keeps fixture secrets to a committed list of fakes.
"""

from spangloss.scenarios.check import CheckResult, check_run
from spangloss.scenarios.spec import (
    Scenario,
    ScenarioError,
    load_scenario,
    scenario_names,
)

__all__ = [
    "CheckResult",
    "Scenario",
    "ScenarioError",
    "check_run",
    "load_scenario",
    "scenario_names",
]
