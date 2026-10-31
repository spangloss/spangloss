"""Command-line interface: spangloss <command> ..."""

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from spangloss import __version__
from spangloss.backends import Backend
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
from spangloss.questions.runner import DEFAULT_MAX_EVIDENCE_CHARS
from spangloss.rules import RULES
from spangloss.store import DEFAULT_DB, LabelStore

DEFAULT_OUTPUT = Path("data/parsed/tool_calls.jsonl")
SCENARIO_GOLD = Path("data/scenarios/gold.jsonl")
SCENARIO_CALLS = Path("data/scenarios/tool_calls.jsonl")
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
    label.add_argument("--backend", choices=["kev", "jev"], required=True)
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
    scen_check = scen_actions.add_parser(
        "check", help="Match runs to their expectations; write gold and review sheets."
    )
    scen_check.add_argument("run_ids", nargs="*", help="default: every run")
    scen_check.add_argument("--gold", type=Path, default=SCENARIO_GOLD)
    scen_fakes = scen_actions.add_parser(
        "verify-fakes", help="Fail on any detected secret not in the fakes list."
    )
    scen_fakes.add_argument("paths", nargs="*", type=Path)

    rules = commands.add_parser(
        "rules", help="Run the detection rules over stored labels."
    )
    rules.add_argument("--input", type=Path, default=DEFAULT_OUTPUT)
    rules.add_argument("--db", type=Path, default=DEFAULT_DB)
    rules.add_argument(
        "--model", help="whose labels to read (default: the only model in the store)"
    )

    args = parser.parse_args(argv)
    if args.command == "scenarios":
        return _scenarios(args)
    if args.command == "gold":
        return _restamp(args.gold, args.input, args.to)
    if args.command == "score":
        return _score(args.db, args.gold)
    if args.command == "rules":
        return _rules(args.input, args.db, args.model)
    if args.command == "label":
        try:
            config = (
                load_config(args.config, required=True)
                if args.config
                else load_config()
            )
        except ConfigError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return _label(
            make_backend(args.backend),
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


def _score(db: Path, gold_path: Path) -> int:
    gold = read_gold(gold_path)
    if not gold:
        print(f"error: no gold labels in {gold_path}", file=sys.stderr)
        return 1
    with LabelStore(db) as store:
        stored = store.stored_labels()
    print(format_report(score(gold, stored, load_cards()), len(gold)))
    return 0


def _scenarios(args: argparse.Namespace) -> int:
    from spangloss.scenarios import ScenarioError, load_scenario, scenario_names

    try:
        if args.scenarios_command == "list":
            for name in scenario_names():
                s = load_scenario(name)
                print(f"{name}  v{s.version}  taxonomy {s.taxonomy}  {s.description}")
            return 0
        if args.scenarios_command == "verify-fakes":
            return _verify_fakes(args.paths)
        if args.scenarios_command == "run":
            return _scenario_run(
                load_scenario(args.name), args.prompt, args.runs, args.keep
            )
        return _scenario_check(args.run_ids, args.gold)
    except ScenarioError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _verify_fakes(paths: list[Path]) -> int:
    from spangloss.scenarios.fakes import read_fakes, verify_fakes

    found = verify_fakes(paths or [Path("scenarios")], read_fakes())
    for u in found:
        print(f"{u.path}:{u.line}: {u.kind} not in the fakes list", file=sys.stderr)
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
        scenario = load_scenario(run["scenario"])
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
        for call in todo:
            outcome = label_call(call, cards, backend, max_chars, first_party)
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
            written += store.write(outcome.labels)

    print(
        f"{backend.model}: {len(todo) - skipped} calls labeled, {skipped} skipped,"
        f" {written} labels written to {db}",
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
