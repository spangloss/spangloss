"""Command-line interface: spangloss <command> ..."""

import argparse
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

from spangloss import __version__
from spangloss.backends import Backend
from spangloss.backends.base import BackendUnavailable, ModelInfo
from spangloss.backends.support import BACKENDS
from spangloss.config import DEFAULT_CONFIG, ConfigError, load_config
from spangloss.eval import (
    DEFAULT_GOLD,
    DEFAULT_SHEET,
    SheetError,
    export_sheet,
    format_report,
    import_sheet,
    read_gold,
    restamp,
    score,
)
from spangloss.parse import ParsedToolCall, parse_claude_code
from spangloss.questions import label_call, load_cards
from spangloss.questions.derived import DERIVED_MODEL
from spangloss.questions.runner import DEFAULT_MAX_EVIDENCE_CHARS, derived_labels
from spangloss.rules import RULES
from spangloss.store import DEFAULT_DB, LabelStore
from spangloss.store.labels import Provenance

DEFAULT_OUTPUT = Path("data/parsed/tool_calls.jsonl")
SCENARIO_GOLD = Path("data/scenarios/gold.jsonl")
SCENARIO_CALLS = Path("data/scenarios/tool_calls.jsonl")
FIXTURE_TRACES = Path("tests/fixtures/claude_code_traces.jsonl")
PARSE_FIRST = "run spangloss parse first"
log = logging.getLogger("spangloss")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spangloss", description="Security labels for AI agent telemetry."
    )
    parser.add_argument(
        "--version", action="version", version=f"spangloss {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    parse = commands.add_parser(
        "parse", help="Parse Claude Code traces into tool-call records."
    )
    parse.add_argument(
        "input", type=Path, help="OTLP JSON traces file, e.g. data/raw/traces.jsonl"
    )
    parse.add_argument(
        "--logs",
        type=Path,
        help="OTLP JSON logs file with tool_result events (default: logs.jsonl next to input, if present)",
    )
    parse.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"default: {DEFAULT_OUTPUT}",
    )

    label = commands.add_parser("label", help="Label parsed tool calls.")
    label.add_argument("--backend", choices=BACKENDS, required=True)
    label.add_argument(
        "--limit", type=int, required=True, help="label at most N unlabeled calls"
    )
    label.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    label.add_argument("--db", type=Path, default=DEFAULT_DB)
    label.add_argument(
        "--max-evidence-chars",
        type=int,
        default=DEFAULT_MAX_EVIDENCE_CHARS,
        help=f"default: {DEFAULT_MAX_EVIDENCE_CHARS}",
    )
    label.add_argument(
        "--config",
        type=Path,
        help=f"first-party MCP servers and other settings (default: {DEFAULT_CONFIG},"
        " if present)",
    )
    label.add_argument(
        "--kev-url",
        help="a Kev server other than the local one, such as one on Modal; needs"
        " --name and a --db of its own. A server off this machine gets Jev's screen",
    )
    label.add_argument(
        "--kev-key-var",
        help="the .env variable holding that server's API key (the key itself never"
        " goes on the command line)",
    )
    label.add_argument(
        "--name",
        help="the backend name recorded in sec.model for a --kev-url server, e.g."
        " kev-9b. It answers every label, including ones the local Kev doesn't",
    )

    export = commands.add_parser(
        "export-sheet", help="Write a CSV sheet of tool calls to label by hand."
    )
    export.add_argument("--limit", type=int, required=True, help="at most N calls")
    export.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    export.add_argument("--out", type=Path, default=DEFAULT_SHEET)
    export.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    export.add_argument(
        "--force", action="store_true", help="overwrite an existing sheet"
    )

    imp = commands.add_parser(
        "import-sheet", help="Validate a filled sheet and add it to the gold labels."
    )
    imp.add_argument("sheet", type=Path)
    imp.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    imp.add_argument("--gold", type=Path, default=DEFAULT_GOLD)

    score_cmd = commands.add_parser(
        "score", help="Score every model's stored labels against the gold labels."
    )
    score_cmd.add_argument("--db", type=Path, default=DEFAULT_DB)
    score_cmd.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    score_cmd.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"parsed calls, for call shapes (scenario gold: {SCENARIO_CALLS})",
    )

    gold_cmd = commands.add_parser("gold", help="Maintain the gold labels.")
    gold_actions = gold_cmd.add_subparsers(dest="gold_command", required=True)
    restamp_cmd = gold_actions.add_parser(
        "restamp",
        help="Move gold records to a new taxonomy version where no change affects"
        " them; list the rest for review.",
    )
    restamp_cmd.add_argument("--to", required=True, help="target taxonomy version")
    restamp_cmd.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    restamp_cmd.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)

    scen = commands.add_parser(
        "scenarios", help="Run seeded scenarios and turn their calls into gold."
    )
    scen_actions = scen.add_subparsers(dest="scenarios_command", required=True)
    scen_actions.add_parser("list", help="List scenarios and their versions.")
    scen_run = scen_actions.add_parser(
        "run", help="Run a scenario in an isolated container (calls the API)."
    )
    scen_run.add_argument("name")
    scen_run.add_argument(
        "--prompt", help="run this prompt variant only (default: every variant)"
    )
    scen_run.add_argument(
        "--runs", type=int, help="runs per prompt variant (default: the scenario's)"
    )
    scen_run.add_argument(
        "--keep", action="store_true", help="keep the run's Docker volumes"
    )
    scen_run.add_argument(
        "--set",
        action="append",
        dest="sets",
        metavar="ID",
        help="run this fixture set (repeatable); a scenario with sets needs --set"
        " or --all-sets",
    )
    scen_run.add_argument(
        "--all-sets", action="store_true", help="run every fixture set, in order"
    )
    scen_run.add_argument(
        "--stop-after-usd",
        type=float,
        help="start no further run once the runs so far cost this much",
    )
    scen_check = scen_actions.add_parser(
        "check", help="Match runs to their expectations; write gold and review sheets."
    )
    scen_check.add_argument("run_ids", nargs="*", help="default: every run")
    scen_check.add_argument("--gold", type=Path, default=SCENARIO_GOLD)
    scen_fakes = scen_actions.add_parser(
        "verify-fakes", help="Fail on any detected secret not in the fakes list."
    )
    scen_fakes.add_argument("paths", nargs="*", type=Path)

    rederive = commands.add_parser(
        "rederive",
        help="Apply the current derived and unsupported-label rules to stored labels,"
        " without asking any model.",
    )
    rederive.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    rederive.add_argument("--db", type=Path, default=DEFAULT_DB)
    rederive.add_argument(
        "--config",
        type=Path,
        help=f"first-party MCP servers (default: {DEFAULT_CONFIG}, if present)",
    )

    var = commands.add_parser(
        "variance",
        help="Compare repeated labeling passes, one store each: how often answers"
        " change, per gold set, model and label.",
    )
    var.add_argument(
        "--db", type=Path, action="append", required=True, help="one per pass"
    )
    var.add_argument(
        "--gold",
        action="append",
        metavar="NAME=PATH",
        help="a gold set to report separately (default: real_sessions="
        f"{DEFAULT_GOLD} and scenarios={SCENARIO_GOLD})",
    )

    rules = commands.add_parser(
        "rules", help="Run the detection rules over stored labels."
    )
    rules.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    rules.add_argument("--db", type=Path, default=DEFAULT_DB)
    rules.add_argument(
        "--model", help="whose labels to read (default: the only model in the store)"
    )

    args = parser.parse_args(argv)
    inputs = _input_hints(args)
    try:
        return _run(args)
    except FileNotFoundError as e:
        hint = inputs.get(Path(e.filename)) if e.filename else None
        if hint is None:
            raise
        print(f"error: {e.filename} not found; {hint}", file=sys.stderr)
        return 1
    except BackendUnavailable as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _input_hints(args: argparse.Namespace) -> dict[Path, str]:
    """The command's input files, each with what to do when it's missing."""
    if args.command == "parse":
        hints = {
            args.input: "capture a session first (docs/capture.md), or try"
            f" {FIXTURE_TRACES}"
        }
        if args.logs:
            hints[args.logs] = "pass the logs file captured with the traces"
        return hints
    if args.command in ("label", "rederive", "rules", "export-sheet"):
        return {args.input: PARSE_FIRST}
    if args.command == "import-sheet":
        return {
            args.sheet: "write one with spangloss export-sheet and fill it in",
            args.input: PARSE_FIRST,
        }
    return {}


def _run(args: argparse.Namespace) -> int:
    if args.command == "scenarios":
        return _scenarios(args)
    if args.command == "gold":
        return _restamp(args.gold, args.input, args.to)
    if args.command == "score":
        return _score(args.db, args.gold, args.input)
    if args.command == "rules":
        return _rules(args.input, args.db, args.model)
    if args.command == "variance":
        return _variance(args.db, args.gold)
    if args.command in ("label", "rederive"):
        try:
            config = (
                load_config(args.config, required=True)
                if args.config
                else load_config()
            )
        except ConfigError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        if args.command == "rederive":
            return _rederive(args.input, args.db, config.first_party_mcp_servers)
        if problem := _remote_kev_problem(args):
            print(f"error: {problem}", file=sys.stderr)
            return 1
        return _label(
            make_remote_kev(args.kev_url, args.name, args.kev_key_var)
            if args.kev_url
            else make_backend(args.backend),
            args.input,
            args.db,
            args.limit,
            args.max_evidence_chars,
            config.first_party_mcp_servers,
        )
    if args.command == "export-sheet":
        return _export_sheet(args.input, args.out, args.gold, args.limit, args.force)
    if args.command == "import-sheet":
        return _import_sheet(args.sheet, args.input, args.gold)
    return _parse(args.input, args.logs, args.output)


def read_calls(path: Path) -> list[ParsedToolCall]:
    with path.open(encoding="utf-8") as f:
        return [ParsedToolCall(**json.loads(line)) for line in f if line.strip()]


def _export_sheet(calls: Path, out: Path, gold: Path, limit: int, force: bool) -> int:
    try:
        n = export_sheet(read_calls(calls), out, limit, gold=gold, force=force)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {n} tool calls to {out}", file=sys.stderr)
    return 0


def _import_sheet(sheet: Path, calls: Path, gold: Path) -> int:
    try:
        result = import_sheet(sheet, read_calls(calls), gold=gold)
    except SheetError as e:
        print(
            f"{sheet}: {len(e.problems)} problem(s), nothing written:", file=sys.stderr
        )
        for problem in e.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(
        f"{sheet}: {result.labeled} calls imported, {result.skipped_blank} blank rows"
        f" skipped; {gold} now holds {result.gold_total} calls",
        file=sys.stderr,
    )
    return 0


def _score(db: Path, gold_path: Path, calls_path: Path) -> int:
    from spangloss.eval.shapes import call_shape

    if not db.exists():
        print(f"error: {db} not found; run spangloss label first", file=sys.stderr)
        return 1
    gold = read_gold(gold_path)
    if not gold:
        print(f"error: no gold labels in {gold_path}", file=sys.stderr)
        return 1
    shapes = (
        {c.span_id: call_shape(c) for c in read_calls(calls_path)}
        if calls_path.exists()
        else {}
    )
    unshaped = sum(1 for r in gold if r["span_id"] not in shapes)
    if unshaped:
        print(
            f"note: {unshaped} gold call(s) not in {calls_path}; each counts as its"
            " own shape",
            file=sys.stderr,
        )
    # Read-only, so scoring never migrates or otherwise rewrites a store.
    with LabelStore(db, read_only=True) as store:
        stored = store.stored_labels()
        provenance = store.provenance()
    for line in _provenance_lines(provenance):
        print(line)
    print(format_report(score(gold, stored, load_cards(), shapes), len(gold)))
    return 0


def _variance(dbs: list[Path], gold_args: list[str] | None) -> int:
    from spangloss.eval.variance import format_variance, variance

    if len(dbs) < 2:
        print("error: give at least two --db, one per pass", file=sys.stderr)
        return 1
    sets: dict[str, Path] = {}
    for arg in gold_args or [
        f"real_sessions={DEFAULT_GOLD}",
        f"scenarios={SCENARIO_GOLD}",
    ]:
        name, sep, path = arg.partition("=")
        if not sep or not name or not path:
            print(f"error: --gold {arg!r}: use NAME=PATH", file=sys.stderr)
            return 1
        sets[name] = Path(path)
    for db in dbs:
        if not db.exists():
            print(f"error: {db} not found", file=sys.stderr)
            return 1
    passes = []
    checkpoints: list[dict[str, list[str | None]]] = []
    for db in dbs:
        with LabelStore(db, read_only=True) as store:
            passes.append(store.stored_labels())
            by_model: dict[str, list[str | None]] = {}
            for run in store.runs():
                by_model.setdefault(run["model"], []).append(run["checkpoint"])
            checkpoints.append(by_model)
    models = sorted(
        set.intersection(*({x.model for x in p} for p in passes)) - {DERIVED_MODEL}
    )
    if not models:
        print("error: no model has labels in every store", file=sys.stderr)
        return 1
    cards = load_cards()
    print(f"Variance over {len(dbs)} passes: {', '.join(str(d) for d in dbs)}")
    for name, path in sets.items():
        spans = {r["span_id"] for r in read_gold(path)}
        for model in models:
            rows = variance(passes, model, spans, cards)
            pass_checkpoints = [_one_checkpoint(c.get(model, [])) for c in checkpoints]
            print()
            print(format_variance(f"{name}, {model}:", rows, pass_checkpoints))
    return 0


def _one_checkpoint(recorded: list[str | None]) -> str | None:
    """A store's checkpoint for one model; several are joined, none is None."""
    named = sorted({c or "unnamed" for c in recorded})
    return " + ".join(named) if named else None


def _checkpoint(info: ModelInfo) -> str:
    return (
        f"checkpoint {info.checkpoint}"
        if info.checkpoint
        else "checkpoint not named by the server"
    )


def _provenance_lines(provenance: list[Provenance]) -> list[str]:
    """Which checkpoint each model's labels came from, as the store's run log says."""
    lines = ["Provenance (from the store's run log):"]
    for p in provenance:
        parts = [
            f"{n} from {checkpoint or 'a checkpoint the server did not name'}"
            for checkpoint, n in p.by_checkpoint.items()
        ]
        if p.unrecorded:
            parts.append(f"{p.unrecorded} from before runs were recorded")
        line = f"  {p.model}: label rows: {'; '.join(parts)}"
        if p.changed:
            line += f" ({p.changed} from runs whose checkpoint changed mid-run)"
        if p.unfinished:
            line += f" ({p.unfinished} from runs that stopped early)"
        lines.append(line)
        if p.mixed:
            lines.append(
                f"  warning: {p.model} labels can't be pinned to one checkpoint;"
                " the scores below pool them"
            )
    return lines + [""]


def _scenarios(args: argparse.Namespace) -> int:
    from spangloss.scenarios import ScenarioError, load_scenario, scenario_names

    try:
        if args.scenarios_command == "list":
            for name in scenario_names():
                s = load_scenario(name)
                sets = f"  {len(s.fixture_sets)} sets" if s.fixture_sets else ""
                print(
                    f"{name}  v{s.version}  taxonomy {s.taxonomy}{sets}"
                    f"  {s.description}"
                )
            return 0
        if args.scenarios_command == "verify-fakes":
            return _verify_fakes(args.paths)
        if args.scenarios_command == "run":
            scenario = load_scenario(args.name)
            if scenario.fixture_sets or args.sets or args.all_sets:
                return _scenario_run_sets(scenario, args)
            return _scenario_run(scenario, args.prompt, args.runs, args.keep)
        return _scenario_check(args.run_ids, args.gold)
    except ScenarioError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _verify_fakes(paths: list[Path]) -> int:
    from spangloss.scenarios.fakes import read_fakes, read_test_values, verify_fakes

    listed = read_fakes() | read_test_values()
    found = verify_fakes(paths or [Path("scenarios")], listed)
    for u in found:
        print(
            f"{u.path}:{u.line}: {u.kind} not in the fakes or test-values list",
            file=sys.stderr,
        )
    return 1 if found else 0


def _scenario_run(scenario, prompt_id: str | None, runs: int | None, keep: bool) -> int:
    from spangloss.scenarios.runner import RunnerError, run_scenario

    if scenario.taxonomy != load_cards()[0].taxonomy:
        print(
            f"error: {scenario.name} is written for taxonomy {scenario.taxonomy},"
            f" the cards are {load_cards()[0].taxonomy}",
            file=sys.stderr,
        )
        return 1
    prompt_ids = [prompt_id] if prompt_id else [p.id for p in scenario.prompts]
    for pid in prompt_ids:
        scenario.prompt(pid)  # unknown ids fail before anything runs
    for pid in prompt_ids:
        for _ in range(runs or scenario.runs):
            try:
                run_dir = run_scenario(scenario, pid, keep=keep)
            except RunnerError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            print(f"{scenario.name} [{pid}]: run written to {run_dir}", file=sys.stderr)
    return 0


def _scenario_run_sets(scenario, args: argparse.Namespace) -> int:
    """Run fixture sets: each set runs its own prompt, the scenario's runs times."""
    from spangloss.scenarios import load_scenario
    from spangloss.scenarios.runner import RunnerError, run_scenario

    if not scenario.fixture_sets:
        print(f"error: {scenario.name} has no fixture sets", file=sys.stderr)
        return 1
    if args.prompt:
        print("error: a fixture set decides its prompt; drop --prompt", file=sys.stderr)
        return 1
    if bool(args.sets) == args.all_sets:
        print("error: give --set ID (repeatable) or --all-sets", file=sys.stderr)
        return 1
    if scenario.taxonomy != load_cards()[0].taxonomy:
        print(
            f"error: {scenario.name} is written for taxonomy {scenario.taxonomy},"
            f" the cards are {load_cards()[0].taxonomy}",
            file=sys.stderr,
        )
        return 1
    ids = [s.id for s in scenario.fixture_sets] if args.all_sets else args.sets
    resolved = [load_scenario(scenario.name, fixture_set=i) for i in ids]
    spent = 0.0
    for s in resolved:
        for _ in range(args.runs or s.runs):
            if args.stop_after_usd is not None and spent >= args.stop_after_usd:
                print(
                    f"stopped: ${spent:.4f} spent, at or past --stop-after-usd"
                    f" {args.stop_after_usd}",
                    file=sys.stderr,
                )
                return 2
            try:
                run_dir = run_scenario(s, s.prompts[0].id, keep=args.keep)
            except RunnerError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            run = json.loads((run_dir / "run.json").read_text())
            cost = (run.get("result") or {}).get("total_cost_usd") or 0.0
            spent += cost
            print(
                f"{s.name} [{s.active_set.id} {s.prompts[0].id}]: ${cost:.4f}"
                f" (total ${spent:.4f}), canary {run.get('canary')},"
                f" run written to {run_dir}",
                file=sys.stderr,
            )
    return 0


def _scenario_check(run_ids: list[str], gold_path: Path) -> int:
    from spangloss.scenarios import check_run, load_scenario
    from spangloss.scenarios.runner import RUNS_DIR

    run_dirs = (
        [RUNS_DIR / r for r in run_ids]
        if run_ids
        else sorted(p for p in RUNS_DIR.glob("*") if (p / "run.json").exists())
    )
    if not run_dirs:
        print(f"error: no runs in {RUNS_DIR}", file=sys.stderr)
        return 1
    from spangloss.scenarios.fakes import read_fakes

    cards = load_cards()
    fakes = read_fakes()
    gold = {r["span_id"]: r for r in read_gold(gold_path)}
    failed = 0
    for run_dir in run_dirs:
        run = json.loads((run_dir / "run.json").read_text())
        scenario = load_scenario(run["scenario"], fixture_set=run.get("fixture_set"))
        if run.get("version") != scenario.version:
            # A run of an older scenario version is stale: its gold is dropped and
            # it neither passes nor fails.
            gold = {
                k: r
                for k, r in gold.items()
                if r.get("source", {}).get("run_id") != run["run_id"]
            }
            (run_dir / "check.json").write_text(
                json.dumps(
                    {"run_id": run["run_id"], "passed": False, "stale": True}, indent=2
                )
                + "\n"
            )
            print(
                f"{run['run_id']}: stale (v{run.get('version')}; the scenario is"
                f" v{scenario.version}), gold dropped",
                file=sys.stderr,
            )
            continue
        calls = read_calls(run_dir / "tool_calls.jsonl")
        result = check_run(scenario, run, calls, cards, fakes)
        (run_dir / "check.json").write_text(
            json.dumps(result.summary(), indent=2) + "\n"
        )
        # This run's earlier gold is replaced; a failed run contributes none.
        gold = {
            k: r
            for k, r in gold.items()
            if r.get("source", {}).get("run_id") != result.run_id
        }
        if result.passed:
            gold |= {r["span_id"]: r for r in result.gold}
        review = [c for c, _ in result.review]
        if review:
            export_sheet(
                review,
                run_dir / "review.csv",
                limit=len(review),
                gold=run_dir / "no-gold.jsonl",
                force=True,
            )
        status = "passed" if result.passed else "FAILED"
        print(
            f"{result.run_id}: {status}; {len(result.gold)} gold, {len(review)} for"
            f" review, {result.unlabeled} unlabeled",
            file=sys.stderr,
        )
        for failure in result.failures:
            print(f"  - {failure}", file=sys.stderr)
        if result.canary:
            print(f"  canary: {result.canary}", file=sys.stderr)
        failed += not result.passed
    gold_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = gold_path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for record in gold.values():
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp.replace(gold_path)
    _combine_calls(RUNS_DIR, SCENARIO_CALLS)
    return 1 if failed else 0


def _combine_calls(runs_dir: Path, out: Path) -> None:
    """Every passing run's calls in one file, the input for spangloss label."""
    seen: set[str] = set()
    with out.open("w", encoding="utf-8") as f:
        for run_dir in sorted(runs_dir.glob("*")):
            check = run_dir / "check.json"
            if not check.exists() or not json.loads(check.read_text())["passed"]:
                continue
            for call in read_calls(run_dir / "tool_calls.jsonl"):
                if call.span_id not in seen:
                    seen.add(call.span_id)
                    f.write(json.dumps(call.to_dict(), ensure_ascii=False) + "\n")


def _restamp(gold: Path, calls: Path, to: str) -> int:
    try:
        result = restamp(gold, read_calls(calls), load_cards(), to)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(
        f"{gold}: {result.restamped} restamped to {to}, {result.already} already {to},"
        f" {len(result.affected)} affected (left unchanged),"
        f" {result.other_versions} on versions with no restamp rules",
        file=sys.stderr,
    )
    for a in result.affected:
        print(f"  {a.span_id} {a.tool_name}:", file=sys.stderr)
        for reason in a.reasons:
            print(f"    - {reason}", file=sys.stderr)
    return 0


def _rules(calls_path: Path, db: Path, model: str | None) -> int:
    calls = read_calls(calls_path)
    with LabelStore(db) as store:
        models = store.models()
        if not models:
            print(
                f"error: no labels in {db} to run rules over; run spangloss label first",
                file=sys.stderr,
            )
            return 1
        if model is None:
            if len(models) != 1:
                found = ", ".join(models) or "none"
                print(
                    f"error: choose a model with --model (models in {db}: {found})",
                    file=sys.stderr,
                )
                return 1
            model = models[0]
        elif model not in models:
            print(f"error: no labels from {model} in {db}", file=sys.stderr)
            return 1
        answers = store.answers(model)
        for name, rule in RULES.items():
            n = store.replace_findings(name, model, rule(calls, answers))
            print(f"{name}: {n} finding(s) over {model} labels", file=sys.stderr)
    return 0


NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def _remote_kev_problem(args: argparse.Namespace) -> str | None:
    """Why the --kev-url options can't be used as given, or None."""
    if not args.kev_url:
        if args.name or args.kev_key_var:
            return "--name and --kev-key-var need --kev-url"
        return None
    if args.backend != "kev":
        return "--kev-url needs --backend kev"
    if not args.name:
        return "--kev-url needs --name, the backend name its labels are stored under"
    if not NAME.fullmatch(args.name) or args.name in (*BACKENDS, DERIVED_MODEL):
        return (
            f"--name {args.name!r}: use lowercase letters, digits and dashes, and not"
            f" {', '.join((*BACKENDS, DERIVED_MODEL))}"
        )
    if args.db == DEFAULT_DB:
        return "--kev-url needs a --db of its own, not the default store"
    return None


def make_remote_kev(url: str, name: str, key_var: str | None) -> Backend:
    from spangloss.backends.typesafe import read_env_value, remote_kev_backend

    key = None
    if key_var:
        key = read_env_value(Path(".env"), key_var)
        if not key:
            raise BackendUnavailable(f"{key_var} is not set in .env")
    return remote_kev_backend(url, name, key)


def make_backend(name: str) -> Backend:
    from spangloss.backends.typesafe import jev_backend, kev_backend

    return kev_backend() if name == "kev" else jev_backend()


def _label(
    backend: Backend,
    calls_path: Path,
    db: Path,
    limit: int,
    max_chars: int,
    first_party: frozenset[str] = frozenset(),
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    cards = load_cards()
    calls = read_calls(calls_path)

    with LabelStore(db) as store:
        done = store.labeled_span_ids(backend.model)
        todo = [c for c in calls if c.span_id not in done][:limit]
        written = 0
        skipped = 0
        unsupported: Counter[str] = Counter()
        # A run that asks nothing isn't logged, and doesn't contact the server.
        start = backend.describe() if todo else None
        run_id = store.start_run(backend.model, start, __version__) if start else None
        for call in todo:
            outcome = label_call(call, cards, backend, max_chars, first_party)
            unsupported.update(outcome.unsupported)
            if outcome.skipped:
                skipped += 1
                log.info(
                    "%s: skipped %s %s: %s",
                    backend.name,
                    call.span_id,
                    call.tool_name,
                    outcome.skipped,
                )
                continue
            written += store.write(outcome.labels, run_id)
        if start and run_id:
            end = backend.describe()
            store.finish_run(run_id, end, len(todo) - skipped, written)

    print(
        f"{backend.model}: {len(todo) - skipped} calls labeled, {skipped} skipped,"
        f" {written} labels written to {db}",
        file=sys.stderr,
    )
    if start and run_id:
        named = start if start.checkpoint else end
        print(f"{backend.model}: run {run_id}, {_checkpoint(named)}", file=sys.stderr)
        if start.checkpoint and end.checkpoint != start.checkpoint:
            print(
                f"warning: {backend.model} checkpoint changed during the run, from"
                f" {start.checkpoint or 'unnamed'} to {end.checkpoint or 'unnamed'};"
                " the run's labels can't be pinned to one",
                file=sys.stderr,
            )
    for label, n in sorted(unsupported.items()):
        print(
            f"{backend.model}: {label} unsupported, not asked on {n} call(s)",
            file=sys.stderr,
        )
    return 0


def _rederive(calls_path: Path, db: Path, first_party: frozenset[str]) -> int:
    cards = load_cards()
    with LabelStore(db) as store:
        # Only calls already labeled: a labeling run writes nothing for the rest.
        labeled = store.span_ids()
        calls = [c for c in read_calls(calls_path) if c.span_id in labeled]
        derived = [x for c in calls for x in derived_labels(c, cards, first_party)]
        result = store.rederive([c.span_id for c in calls], derived)
    print(
        f"{db}: derived rows for {len(calls)} calls {result.derived_before} ->"
        f" {result.derived_after}; {result.model_rows_replaced} model row(s) replaced"
        f" by derived labels; {result.unsupported_rows_dropped} row(s) dropped for"
        " labels their backend doesn't answer",
        file=sys.stderr,
    )
    return 0


def _parse(traces: Path, logs: Path | None, output: Path) -> int:
    if logs is None:
        sibling = traces.with_name("logs.jsonl")
        logs = sibling if sibling.exists() else None
    calls = parse_claude_code(traces, logs)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for call in calls:
            f.write(json.dumps(call.to_dict(), ensure_ascii=False) + "\n")

    bases = Counter(c.basis for c in calls)
    sources = Counter(c.args_source or "none" for c in calls)
    print(f"traces: {traces}", file=sys.stderr)
    print(f"logs:   {logs or '(none)'}", file=sys.stderr)
    print(f"wrote {len(calls)} tool calls to {output}", file=sys.stderr)
    print(f"basis: {dict(bases)}  args from: {dict(sources)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
