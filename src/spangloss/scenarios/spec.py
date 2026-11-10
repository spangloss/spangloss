"""Load and validate scenario.yaml files (docs/scenarios-design.md, section 5).

A scenario is a directory under scenarios/ holding scenario.yaml, a README and
fixtures/. Expected labels in it are evaluation labels, which the owner writes.

A scenario may list fixture sets: each run uses one set, whose overlay is laid
over the base fixtures and whose vars fill the
{name} placeholders in fixture paths, prompts, setup steps, carriers and
expectations. Loading a scenario with a set resolves all of that for the set.

A train set's overlay is committed at fixtures/sets/<id>/. A test (held-out) set's
overlay is kept out of git at fixtures/heldout/<id>/; scenarios/heldout.sha256
records its files so it can be verified when results are published.
"""

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SCENARIOS_DIR = Path("scenarios")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
NAME = re.compile(r"^[a-z][a-z0-9_]*$")
PERMISSION_MODES = {"dontAsk"}  # the only mode scenarios use (section 3)
IMAGES = {"base", "node"}  # the agent image: base, or base plus Node.js and npm
DEFAULT_SITE_HOST = "docs.tinyfmt.example"
SET_ID = re.compile(r"^[a-z][a-z0-9]*$")
VAR = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
VAR_VALUE = re.compile(r"^[A-Za-z0-9._-]+$")
SPLITS = {"train", "test"}
OVERLAY_DIRS = {"train": "sets", "test": "heldout"}  # heldout/ is git-ignored
BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{20,}={0,2}")


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Fixture:
    id: str
    path: str  # workspace-relative, or "~/..." for the agent's home folder
    fingerprint: str | None  # text the output must show when a matched call has output
    sha256: str  # computed from the file in fixtures/
    source: Path  # the file in fixtures/


@dataclass(frozen=True, slots=True)
class Prompt:
    id: str
    text: str
    tools: tuple[str, ...] | None = None  # this variant's --tools, if not claude.tools


@dataclass(frozen=True, slots=True)
class McpServer:
    """A stdio MCP server the runner starts inside the agent container."""

    name: str
    script: Path  # a Python file in the scenario directory, run with python3
    tools: tuple[str, ...]  # the tools it offers, without the mcp__<server>__ prefix

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(f"mcp__{self.name}__{t}" for t in self.tools)


@dataclass(frozen=True, slots=True)
class Match:
    tools: tuple[str, ...]
    args: dict[str, str] = field(default_factory=dict)  # arg key -> regex
    not_args: dict[str, str] = field(default_factory=dict)  # excludes on any hit
    output: str | None = None
    fixture: str | None = None
    agent: str = "any"  # main | subagent | any
    decision: str = "any"  # accept | reject | any
    basis: str | None = None


@dataclass(frozen=True, slots=True)
class WhenOutput:
    matches: str | None  # None for the "otherwise" branch
    labels: dict[str, str] | None  # None means "send to review"


@dataclass(frozen=True, slots=True)
class Expectation:
    id: str
    required_for: frozenset[str]  # prompt ids this expectation must match under
    match: Match
    labels: dict[str, str]
    when_output: tuple[WhenOutput, ...] = ()
    # injection: by_set. On a call that reads one of the set's payload carriers,
    # injection is yes when a marker of the set's canary is in the output and goes
    # to review otherwise; on any other call it keeps its labels.
    injection_by_set: bool = False


@dataclass(frozen=True, slots=True)
class FixtureSet:
    id: str
    split: str  # train | test, fixed before the set's first run
    prompt: str  # the prompt id this set runs
    canary: str | None
    payloads: dict[str, str]  # carrier -> payload style
    lookalikes: tuple[str, ...]  # carriers or kinds holding a look-alike
    vars: dict[str, str]  # values for the {name} placeholders


@dataclass(frozen=True, slots=True)
class ClaudeConfig:
    model: str
    tools: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    disallowed_tools: tuple[str, ...]
    permission_mode: str
    max_turns: int
    max_budget_usd: float
    agents: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ExpectedFinding:
    rule: str
    anchor: str  # expectation id of the call the finding is anchored on


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    version: int
    taxonomy: str
    description: str
    prompts: tuple[Prompt, ...]
    directory: Path
    fixtures: dict[str, Fixture]
    setup: tuple[str, ...]
    claude: ClaudeConfig
    runs: int
    defaults: dict[str, str]
    expect: tuple[Expectation, ...]
    unlabeled: tuple[Match, ...]
    expect_findings: tuple[ExpectedFinding, ...]
    site: Path | None  # pages served at the scenario pages host, if any
    canary_token: str | None
    publishable: bool
    mcp_servers: dict[str, McpServer] = field(default_factory=dict)
    image: str = "base"
    site_host: str = DEFAULT_SITE_HOST
    site_tls: bool = True
    carriers: dict[str, Match] = field(default_factory=dict)
    fixture_sets: tuple[FixtureSet, ...] = ()
    active_set: FixtureSet | None = None  # the set this scenario was loaded for
    markers: tuple[str, ...] = ()  # the set's canary, plain or as it is encoded
    # Groups of expectation ids: each group needs at least one of them matched.
    required_any: tuple[tuple[str, ...], ...] = ()
    # "match": a denied call is matched like any other (secrets_read labels them);
    # "exclude": it is left out of gold and review, and only counted.
    denied_calls: str = "match"

    @property
    def payload_carriers(self) -> frozenset[str]:
        return frozenset(self.active_set.payloads if self.active_set else ())

    def tools_for(self, prompt_id: str) -> tuple[str, ...]:
        """The --tools for a prompt variant: its own list, or the scenario's."""
        tools = self.prompt(prompt_id).tools
        return self.claude.tools if tools is None else tools

    def prompt(self, prompt_id: str) -> Prompt:
        for p in self.prompts:
            if p.id == prompt_id:
                return p
        known = ", ".join(p.id for p in self.prompts)
        raise ScenarioError(f"{self.name} has no prompt {prompt_id!r} (has {known})")

    @property
    def workspace_fixtures(self) -> Path:
        return self.directory / "fixtures" / "workspace"

    @property
    def home_fixtures(self) -> Path:
        return self.directory / "fixtures" / "home"

    @property
    def overlay(self) -> Path | None:
        """The active set's overlay directory, if a set is active."""
        if self.active_set is None:
            return None
        return overlay_dir(self.directory, self.active_set)

    def fixture_set(self, set_id: str) -> FixtureSet:
        for s in self.fixture_sets:
            if s.id == set_id:
                return s
        raise ScenarioError(f"{self.name} has no fixture set {set_id!r}")


def overlay_dir(directory: Path, fixture_set: "FixtureSet") -> Path:
    """Where a set's overlay lives: committed for train, git-ignored for test."""
    return directory / "fixtures" / OVERLAY_DIRS[fixture_set.split] / fixture_set.id


def scenario_names(root: Path = SCENARIOS_DIR) -> list[str]:
    return sorted(
        p.parent.name for p in root.glob("*/scenario.yaml") if NAME.match(p.parent.name)
    )


def load_scenario(
    name: str, root: Path = SCENARIOS_DIR, fixture_set: str | None = None
) -> Scenario:
    """Load a scenario; with fixture_set, resolved for that set. Without one, a
    scenario that has sets loads no fixtures: they come from the sets' overlays."""
    directory = root / name
    path = directory / "scenario.yaml"
    if not path.exists():
        raise ScenarioError(f"no scenario {name!r} in {root}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ScenarioError(f"{path}: not valid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise ScenarioError(f"{path}: expected a mapping")
    where = str(path)
    if raw.get("name") != name:
        raise ScenarioError(f"{where}: name must be {name!r}, the directory name")

    prompts = _prompts(raw, where)
    prompt_ids = frozenset(p.id for p in prompts)
    sets = _fixture_sets(raw.get("fixture_sets"), prompt_ids, where)
    active: FixtureSet | None = None
    overlay: Path | None = None
    if fixture_set is not None:
        if not sets:
            raise ScenarioError(f"{where}: has no fixture sets")
        active = next((s for s in sets if s.id == fixture_set), None)
        if active is None:
            raise ScenarioError(f"{where}: no fixture set {fixture_set!r}")
        overlay = overlay_dir(directory, active)
        if not overlay.is_dir():
            local = (
                " (held-out overlays are local only)" if active.split == "test" else ""
            )
            raise ScenarioError(
                f"{where}: set {active.id} has no overlay {overlay}{local}"
            )
        values = active.vars | ({"canary": active.canary} if active.canary else {})
        raw = _with_vars(raw, values)
        where = f"{where} [{active.id}]"
        prompts = _prompts(raw, where)

    fixtures = {
        fid: _fixture(fid, spec, directory, where, overlay)
        for fid, spec in (raw.get("fixtures") or {}).items()
        if active is not None or not sets
    }
    carriers = {
        cid: _match(m, fixtures, f"{where}: carrier {cid}")
        for cid, m in (raw.get("carriers") or {}).items()
    }
    for s in sets:
        unknown = set(s.payloads) - set(carriers)
        if unknown:
            raise ScenarioError(
                f"{where}: set {s.id} poisons unknown carriers {sorted(unknown)}"
            )
    expect = tuple(
        _expectation(e, fixtures, prompt_ids, where) for e in raw.get("expect") or []
    )
    if any(e.injection_by_set for e in expect) and not sets:
        raise ScenarioError(f"{where}: injection: by_set needs fixture_sets")
    ids = [e.id for e in expect]
    if len(ids) != len(set(ids)):
        raise ScenarioError(f"{where}: expectation ids must be unique")
    required_any = tuple(tuple(g) for g in raw.get("required_any") or [])
    for group in required_any:
        if not group or set(group) - set(ids):
            raise ScenarioError(f"{where}: required_any names unknown {list(group)}")
    if not any(e.required_for for e in expect) and not required_any:
        raise ScenarioError(f"{where}: at least one expectation must be required")
    denied_calls = raw.get("denied_calls", "match")
    if denied_calls not in {"match", "exclude"}:
        raise ScenarioError(f"{where}: denied_calls must be match or exclude")
    findings = tuple(
        ExpectedFinding(_str(f, "rule", where), _str(f, "anchor", where))
        for f in raw.get("expect_findings") or []
    )
    for f in findings:
        if f.anchor not in ids:
            raise ScenarioError(f"{where}: expect_findings names unknown {f.anchor!r}")
    site = raw.get("site")
    site_path = (directory / site).resolve() if site else None
    if site_path is not None and not site_path.is_dir():
        raise ScenarioError(f"{where}: site {site!r} is not a directory")
    image = raw.get("image", "base")
    if image not in IMAGES:
        raise ScenarioError(f"{where}: image must be one of {sorted(IMAGES)}")
    if active is not None:
        prompts = (next(p for p in prompts if p.id == active.prompt),)
        for p in prompts:
            if VAR.search(p.text):
                raise ScenarioError(f"{where}: prompt {p.id} has an unfilled var")

    return Scenario(
        name=name,
        version=_int(raw, "version", where),
        taxonomy=_str(raw, "taxonomy", where),
        description=_str(raw, "description", where),
        prompts=prompts,
        directory=directory,
        fixtures=fixtures,
        setup=tuple(raw.get("setup") or []),
        claude=_claude(raw.get("claude"), where),
        runs=int(raw.get("runs", 1)),
        defaults=dict(raw.get("defaults") or {}),
        expect=expect,
        unlabeled=tuple(_match(m, fixtures, where) for m in raw.get("unlabeled") or []),
        expect_findings=findings,
        site=site_path,
        canary_token=active.canary if active else raw.get("canary_token"),
        publishable=bool(raw.get("publishable", True)),
        mcp_servers=_mcp_servers(raw.get("mcp_servers"), directory, where),
        image=image,
        site_host=str(raw.get("site_host", DEFAULT_SITE_HOST)),
        site_tls=bool(raw.get("site_tls", True)),
        carriers=carriers,
        fixture_sets=sets,
        active_set=active,
        markers=_markers(active.canary, overlay) if active and overlay else (),
        required_any=required_any,
        denied_calls=denied_calls,
    )


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(
    fid: str, spec: Any, directory: Path, where: str, overlay: Path | None = None
) -> Fixture:
    """A fixture file: the set's overlay copy if it has one, else the base copy.
    The fingerprint is optional; a match that names the fixture needs one."""
    if not isinstance(spec, dict):
        raise ScenarioError(f"{where}: fixture {fid!r} must be a mapping")
    rel = _str(spec, "path", where)
    if VAR.search(rel):
        raise ScenarioError(f"{where}: fixture {fid!r} path has an unfilled var")
    part, inner = (
        ("home", rel.removeprefix("~/"))
        if rel.startswith("~/")
        else (
            "workspace",
            rel,
        )
    )
    candidates = [directory / "fixtures" / part / inner]
    if overlay is not None:
        candidates.insert(0, overlay / part / inner)
    source = next((c for c in candidates if c.is_file()), candidates[-1])
    if not source.is_file():
        raise ScenarioError(f"{where}: fixture {fid!r} has no file at {source}")
    digest = file_sha256(source)
    if digest == EMPTY_SHA256:
        raise ScenarioError(f"{where}: fixture {fid!r} is empty; fixtures must not be")
    fingerprint = spec.get("fingerprint")
    if fingerprint is not None:
        fingerprint = _str(spec, "fingerprint", where)
        if fingerprint not in source.read_text(encoding="utf-8"):
            raise ScenarioError(
                f"{where}: fixture {fid!r} doesn't contain its fingerprint"
            )
    return Fixture(fid, rel, fingerprint, digest, source)


def _fixture_sets(
    raw: Any, prompt_ids: frozenset[str], where: str
) -> tuple[FixtureSet, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise ScenarioError(f"{where}: fixture_sets must be a non-empty list")
    sets = []
    for item in raw:
        if not isinstance(item, dict):
            raise ScenarioError(f"{where}: each fixture set must be a mapping")
        sid = _str(item, "id", where)
        if not SET_ID.match(sid):
            raise ScenarioError(f"{where}: set id {sid!r} must be a lowercase name")
        split = item.get("split")
        if split not in SPLITS:
            raise ScenarioError(f"{where}: set {sid}: split must be train or test")
        prompt = item.get("prompt")
        if prompt is None and len(prompt_ids) == 1:
            prompt = next(iter(prompt_ids))
        if prompt not in prompt_ids:
            raise ScenarioError(f"{where}: set {sid}: prompt must name a prompt id")
        payloads = item.get("payloads") or {}
        if not isinstance(payloads, dict):
            raise ScenarioError(f"{where}: set {sid}: payloads must be a mapping")
        canary = item.get("canary")
        if payloads and not canary:
            raise ScenarioError(f"{where}: set {sid}: payloads need a canary")
        values = item.get("vars") or {}
        if not isinstance(values, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and VAR_VALUE.match(v)
            for k, v in values.items()
        ):
            raise ScenarioError(f"{where}: set {sid}: vars must map names to words")
        lookalikes = item.get("lookalikes", item.get("lookalike")) or []
        if isinstance(lookalikes, str):
            lookalikes = [lookalikes]
        sets.append(
            FixtureSet(
                id=sid,
                split=split,
                prompt=prompt,
                canary=canary,
                payloads=dict(payloads),
                lookalikes=tuple(lookalikes),
                vars=dict(values),
            )
        )
    ids = [s.id for s in sets]
    canaries = [s.canary for s in sets if s.canary]
    if len(ids) != len(set(ids)) or len(canaries) != len(set(canaries)):
        raise ScenarioError(f"{where}: set ids and canaries must be unique")
    return tuple(sets)


# Keys whose string values are regexes: a var's value goes in escaped.
REGEX_KEYS = {"match", "carriers", "when_output", "unlabeled"}


def _with_vars(raw: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    """raw with {name} filled from values, escaped inside regexes. Names not in
    values stay as they are, so regex counts like {2} are untouched."""

    def fill(obj: Any, regex: bool) -> Any:
        if isinstance(obj, str):
            return VAR.sub(
                lambda m: (
                    (re.escape if regex else str)(values[m.group(1)])
                    if m.group(1) in values
                    else m.group(0)
                ),
                obj,
            )
        if isinstance(obj, list):
            return [fill(x, regex) for x in obj]
        if isinstance(obj, dict):
            return {k: fill(v, regex or k in REGEX_KEYS) for k, v in obj.items()}
        return obj

    out = {k: fill(v, k in REGEX_KEYS) for k, v in raw.items() if k != "expect"}
    out["expect"] = [
        {k: fill(v, k in REGEX_KEYS) for k, v in e.items()}
        if isinstance(e, dict)
        else e
        for e in raw.get("expect") or []
    ]
    out["fixture_sets"] = raw.get("fixture_sets")
    return out


def _markers(canary: str | None, overlay: Path) -> tuple[str, ...]:
    """The set's canary, plus every base64 run in its overlay that decodes to
    text holding the canary: an encoded payload shows in output only encoded."""
    if not canary:
        return ()
    found = {canary}
    for path in sorted(overlay.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for run in BASE64_RUN.findall(text):
            try:
                decoded = base64.b64decode(run, validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, ValueError):
                continue
            if canary in decoded:
                found.add(run)
    return tuple(sorted(found))


def _prompts(raw: dict[str, Any], where: str) -> tuple[Prompt, ...]:
    """A list of prompt variants, or a single prompt with the id "default"."""
    if "prompt" in raw and "prompts" in raw:
        raise ScenarioError(f"{where}: give prompt or prompts, not both")
    if "prompt" in raw:
        return (Prompt("default", _str(raw, "prompt", where).strip()),)
    items = raw.get("prompts")
    if not isinstance(items, list) or not items:
        raise ScenarioError(f"{where}: prompts must be a non-empty list")
    prompts = tuple(
        Prompt(
            _str(p, "id", where),
            _str(p, "text", where).strip(),
            _tools(p, where) if "tools" in p else None,
        )
        for p in items
    )
    ids = [p.id for p in prompts]
    if len(ids) != len(set(ids)) or not all(NAME.match(i) for i in ids):
        raise ScenarioError(f"{where}: prompt ids must be unique lowercase names")
    return prompts


def _tools(raw: dict[str, Any], where: str) -> tuple[str, ...]:
    tools = raw.get("tools")
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise ScenarioError(f"{where}: tools must be a list of tool names")
    return tuple(tools)


def _mcp_servers(raw: Any, directory: Path, where: str) -> dict[str, McpServer]:
    """mcp_servers: {<name>: {script: <path in the scenario dir>, tools: [...]}}."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: mcp_servers must be a mapping")
    servers = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not NAME.match(name):
            raise ScenarioError(f"{where}: MCP server names must be lowercase names")
        if not isinstance(spec, dict):
            raise ScenarioError(f"{where}: MCP server {name!r} must be a mapping")
        script = directory / _str(spec, "script", where)
        if script.suffix != ".py" or not script.is_file():
            raise ScenarioError(f"{where}: MCP server {name!r} has no script {script}")
        tools = _tools(spec, where)
        if not tools:
            raise ScenarioError(f"{where}: MCP server {name!r} lists no tools")
        servers[name] = McpServer(name, script, tools)
    return servers


def _match(raw: Any, fixtures: dict[str, Fixture], where: str) -> Match:
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: match must be a mapping")
    tools = raw.get("tool")
    if isinstance(tools, str):
        tools = [tools]
    if not tools or not all(isinstance(t, str) for t in tools):
        raise ScenarioError(f"{where}: match needs tool, a name or list of names")
    args = raw.get("args") or {}
    not_args = raw.get("not_args") or {}
    for pattern in [*args.values(), *not_args.values(), raw.get("output") or ""]:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ScenarioError(f"{where}: bad regex {pattern!r}: {e}") from e
    fixture = raw.get("fixture")
    if fixture is not None and fixture not in fixtures:
        raise ScenarioError(f"{where}: match names unknown fixture {fixture!r}")
    if fixture is not None and fixtures[fixture].fingerprint is None:
        raise ScenarioError(
            f"{where}: match names fixture {fixture!r}, which has no fingerprint"
        )
    agent = raw.get("agent", "any")
    decision = raw.get("decision", "any")
    if agent not in {"main", "subagent", "any"}:
        raise ScenarioError(f"{where}: agent must be main, subagent or any")
    if decision not in {"accept", "reject", "any"}:
        raise ScenarioError(f"{where}: decision must be accept, reject or any")
    return Match(
        tools=tuple(tools),
        args=dict(args),
        not_args=dict(not_args),
        output=raw.get("output"),
        fixture=fixture,
        agent=agent,
        decision=decision,
        basis=raw.get("basis"),
    )


def _expectation(
    raw: Any, fixtures: dict[str, Fixture], prompt_ids: frozenset[str], where: str
) -> Expectation:
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: each expectation must be a mapping")
    eid = _str(raw, "id", where)
    when = []
    for branch in raw.get("when_output") or []:
        if "matches" in branch:
            when.append(WhenOutput(branch["matches"], dict(branch.get("labels") or {})))
        elif "otherwise" in branch:
            other = branch["otherwise"]
            when.append(WhenOutput(None, None if other == "review" else dict(other)))
        else:
            raise ScenarioError(
                f"{where}: {eid}: when_output needs matches or otherwise"
            )
    required = raw.get("required", False)
    if required is True:
        required_for = prompt_ids
    elif required is False:
        required_for = frozenset()
    elif isinstance(required, list) and set(required) <= prompt_ids:
        required_for = frozenset(required)
    else:
        raise ScenarioError(
            f"{where}: {eid}: required must be true, false or a list of prompt ids"
        )
    injection = raw.get("injection")
    if injection not in (None, "by_set"):
        raise ScenarioError(f"{where}: {eid}: injection must be by_set if given")
    if injection and when:
        raise ScenarioError(f"{where}: {eid}: give injection: by_set or when_output")
    return Expectation(
        id=eid,
        required_for=required_for,
        match=_match(raw.get("match"), fixtures, f"{where}: {eid}"),
        labels=dict(raw.get("labels") or {}),
        when_output=tuple(when),
        injection_by_set=injection == "by_set",
    )


def _claude(raw: Any, where: str) -> ClaudeConfig:
    if not isinstance(raw, dict):
        raise ScenarioError(f"{where}: claude must be a mapping")
    mode = raw.get("permission_mode", "dontAsk")
    if mode not in PERMISSION_MODES:
        raise ScenarioError(f"{where}: permission_mode must be dontAsk")
    agents = raw.get("agents")
    for spec in (agents or {}).values():
        missing = set(spec.get("tools", [])) - set(raw.get("tools", []))
        if missing:
            # A subagent's tools must also be in the session's --tools (section 3).
            raise ScenarioError(
                f"{where}: subagent tools {sorted(missing)} must also be in claude.tools"
            )
    return ClaudeConfig(
        model=_str(raw, "model", where),
        tools=tuple(raw.get("tools") or []),
        allowed_tools=tuple(raw.get("allowed_tools") or []),
        disallowed_tools=tuple(raw.get("disallowed_tools") or []),
        permission_mode=mode,
        max_turns=int(raw.get("max_turns", 8)),
        max_budget_usd=float(raw.get("max_budget_usd", 0.5)),
        agents=agents,
    )


def _str(raw: dict[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ScenarioError(f"{where}: {key} must be a non-empty string")
    return value


def _int(raw: dict[str, Any], key: str, where: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int):
        raise ScenarioError(f"{where}: {key} must be an integer")
    return value
