"""The frozen train/test split of the gold calls, committed as span-ID hashes.

Test holds every real-session call, the held-out scenario variants below, and
the held-out hard_negatives call shapes. A call shape is never on both sides: if
any call of a shape is in test, every call of that shape is. Train holds the
rest. Nothing in test is ever trained on, and test never leaves this machine.

splits/v1.txt lists each call as "<side> <sha256(span_id) hex>", one per line,
so it names no session data; splits/v1.json holds the rule and the counts. New scenario families extend the test side with the
fixture sets held back before their runs, in a later split version.

    uv run python -m spangloss.eval.split build   # write splits/v1.{txt,json}
    uv run python -m spangloss.eval.split check   # the files match the gold

Split v2 (splits/v2.json) keeps v1 as it is and adds the batch-1 fixture sets,
assigned by set before any of them ran. A set's calls take its slice: train, or
batch1_test, a test slice of its own that is never pooled with v1's test.

    uv run python -m spangloss.eval.split check-sets  # scenarios agree with v2-v4

Split v3 (splits/v3.json) keeps v1 and v2 as they are and adds batch 2
(docs/drafts/batch2-design.md): every batch-2 set is in batch2_eval, a slice of its
own that is never trained on and never pooled with test or batch1_test. Batch-2
gold can't enter build_split at all: it refuses any record from a batch-2 scenario.

Split v4 (splits/v4.json) keeps v1-v3 as they are and adds round 2
(docs/drafts/round2-training-plan.md):
- round2_train: group A's sets, except the calibration sets;
- round2_calib: the 10 group-A sets in scenarios/round2/calibration.json, fixed
  before any run; used to fit the temperature, never trained on;
- round2_test: all of group B, held out whole. Never used for training, calibration
  or model selection; build_split refuses it like batch 2, and use_allowed() says no
  for every purpose.

    uv run python -m spangloss.eval.split write-v4   # write splits/v4.json
"""

import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

from spangloss.eval.shapes import call_shape
from spangloss.parse.record import ParsedToolCall

SPLIT_VERSION = "v1"
SPLIT_FILE = Path("splits/v1.txt")
COUNTS_FILE = Path("splits/v1.json")
REAL_GOLD = Path("data/eval/gold.jsonl")
REAL_CALLS = Path("data/parsed/tool_calls.jsonl")
SCENARIO_GOLD = Path("data/scenarios/gold.jsonl")
RUNS_DIR = Path("data/scenarios/runs")

# (scenario, prompt variant); None means every variant of the scenario.
HELD_OUT_VARIANTS: frozenset[tuple[str, str | None]] = frozenset(
    {
        ("poisoned_dependency", "precise"),
        ("web_injection_curl", None),
        ("tool_poisoning", "upfront"),
        ("secrets_read", "precise"),
    }
)
# hard_negatives has one variant, so whole call shapes are held out instead.
HELD_OUT_SHAPES: dict[str, frozenset[str]] = {
    "hard_negatives": frozenset(
        {
            'Read {"file_path": "docs/security/prompt-injection.md"}',
            'Read {"file_path": ".env.test"}',
            'Glob {"pattern": "**/.env*"}',
        }
    )
}

Side = Literal["train", "test"]

SPLIT_V2_FILE = Path("splits/v2.json")
# A fixture set's split in scenario.yaml -> its slice in split v2.
SET_SLICES = {"train": "train", "test": "batch1_test"}

SPLIT_V3_FILE = Path("splits/v3.json")
# Batch 2 is held out whole: evaluation only, in its own slice.
BATCH2_SCENARIOS = frozenset({"batch2_mcp", "batch2_web"})
BATCH2_SLICE = "batch2_eval"


SPLIT_V4_FILE = Path("splits/v4.json")
# Round 2: group A trains (less its calibration sets); group B is held out whole.
ROUND2_TRAIN_SCENARIOS = frozenset({"round2a_mcp", "round2a_web"})
ROUND2_TEST_SCENARIOS = frozenset({"round2b_mcp", "round2b_web"})
ROUND2_TRAIN_SLICE = "round2_train"
ROUND2_CALIB_SLICE = "round2_calib"
ROUND2_TEST_SLICE = "round2_test"
CALIBRATION_FILE = Path("scenarios/round2/calibration.json")
# Scenarios whose gold is evaluation only: never split, trained on or calibrated on.
HELD_OUT_SCENARIOS = BATCH2_SCENARIOS | ROUND2_TEST_SCENARIOS
# What each slice may be used for; a slice not listed may be used for nothing.
USES = {
    "train": frozenset({"train"}),
    ROUND2_TRAIN_SLICE: frozenset({"train"}),
    ROUND2_CALIB_SLICE: frozenset({"calibrate", "select"}),
}


class HeldOutError(ValueError):
    """Held-out (batch-2 or round-2 test) data offered where it could be used."""


def calibration_sets(path: Path = CALIBRATION_FILE) -> frozenset[str]:
    return frozenset(json.loads(path.read_text(encoding="utf-8"))["sets"])


def slice_for(scenario: str, split: str, set_id: str | None = None) -> str:
    """The slice a fixture set takes: batch2_eval for any batch-2 set, round2_test
    for any group-B set, round2_calib or round2_train for a group-A set."""
    if scenario in BATCH2_SCENARIOS:
        return BATCH2_SLICE
    if scenario in ROUND2_TEST_SCENARIOS:
        return ROUND2_TEST_SLICE
    if scenario in ROUND2_TRAIN_SCENARIOS:
        if set_id is None:
            raise ValueError(f"{scenario}: a group-A set's slice depends on its id")
        return (
            ROUND2_CALIB_SLICE if set_id in calibration_sets() else ROUND2_TRAIN_SLICE
        )
    return SET_SLICES[split]


def use_allowed(slice_: str, purpose: str) -> bool:
    """Whether calls in a slice may be used for a purpose: train, calibrate or
    select (model selection). Test slices may be used for none of them."""
    return purpose in USES.get(slice_, frozenset())


def refuse_held_out(records: list[dict[str, Any]]) -> None:
    """Raise if any gold record comes from a held-out scenario."""
    held = sorted(
        {
            (r.get("source") or {}).get("scenario")
            for r in records
            if (r.get("source") or {}).get("scenario") in HELD_OUT_SCENARIOS
        }
    )
    if held:
        raise HeldOutError(f"held-out gold is evaluation only, never split: {held}")


def span_hash(span_id: str) -> str:
    return hashlib.sha256(span_id.encode("utf-8")).hexdigest()


def load_split(path: Path = SPLIT_FILE) -> dict[str, Side]:
    """span hash -> side."""
    split: dict[str, Side] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            side, digest = line.split()
            if side not in ("train", "test"):
                raise ValueError(f"{path}: unknown side {side!r}")
            split[digest] = side  # type: ignore[assignment]
    return split


def side_of(span_id: str, split: dict[str, Side]) -> Side | None:
    """The span's side, or None for a span the split doesn't cover."""
    return split.get(span_hash(span_id))


def build_split(
    real_gold: list[dict[str, Any]],
    scenario_gold: list[dict[str, Any]],
    calls: dict[str, ParsedToolCall],
    variant_of: dict[str, tuple[str, str]],
) -> dict[str, Side]:
    """span_id -> side. variant_of maps a scenario span to (scenario, variant)."""
    refuse_held_out(scenario_gold)
    # Also by the run a call came from, in case a record's source was lost.
    held = sorted(
        {variant_of[r["span_id"]][0] for r in scenario_gold} & HELD_OUT_SCENARIOS
    )
    if held:
        raise HeldOutError(f"held-out calls are evaluation only, never split: {held}")
    side: dict[str, Side] = {r["span_id"]: "test" for r in real_gold}
    shape = {sid: call_shape(c) for sid, c in calls.items()}
    for r in scenario_gold:
        sid = r["span_id"]
        scenario, variant = variant_of[sid]
        held = (scenario, variant) in HELD_OUT_VARIANTS or (
            scenario,
            None,
        ) in HELD_OUT_VARIANTS
        held = held or shape[sid] in HELD_OUT_SHAPES.get(scenario, frozenset())
        side[sid] = "test" if held else "train"
    # A shape with any call in test moves wholly to test.
    test_shapes = {shape[s] for s, x in side.items() if x == "test" and s in shape}
    for sid in side:
        if shape.get(sid) in test_shapes:
            side[sid] = "test"
    return side


def summary(
    side: dict[str, Side],
    gold: list[dict[str, Any]],
    calls: dict[str, ParsedToolCall],
) -> dict[str, Any]:
    """Counts per side: calls, shapes, and each label's gold answers."""
    out: dict[str, Any] = {}
    for s in ("test", "train"):
        records = [r for r in gold if side[r["span_id"]] == s]
        labels: dict[str, Counter[str]] = defaultdict(Counter)
        for r in records:
            for label, answer in r["labels"].items():
                labels[label][answer] += 1
        out[s] = {
            "calls": len(records),
            "shapes": len(
                {
                    call_shape(calls[r["span_id"]])
                    if r["span_id"] in calls
                    else r["span_id"]
                    for r in records
                }
            ),
            "labels": {k: dict(sorted(v.items())) for k, v in sorted(labels.items())},
        }
    return out


def all_set_slices() -> dict[tuple[str, str], str]:
    """(scenario, fixture set) -> slice, from splits v2, v3 and v4 together."""
    return (
        set_slices(SPLIT_V2_FILE)
        | set_slices(SPLIT_V3_FILE)
        | set_slices(SPLIT_V4_FILE)
    )


def v4_body(scenarios: dict[str, dict[str, str]]) -> dict[str, Any]:
    """splits/v4.json from the round-2 scenarios' fixture sets."""
    sets: dict[str, dict[str, list[str]]] = {}
    for name in sorted(ROUND2_TRAIN_SCENARIOS | ROUND2_TEST_SCENARIOS):
        for sid, split in sorted(scenarios[name].items()):
            sets.setdefault(name, {}).setdefault(
                slice_for(name, split, sid), []
            ).append(sid)
    return {
        "split": "v4",
        "extends": "v3: splits/v3.json, unchanged (and v1, v2 through it)",
        "slices": {
            ROUND2_TRAIN_SLICE: "round 2 group A, less the calibration sets: training",
            ROUND2_CALIB_SLICE: f"round 2 group A sets in {CALIBRATION_FILE} (fixed before any run): temperature fitting and model selection, never trained on",
            ROUND2_TEST_SLICE: "round 2 group B, held out whole: evaluation only; never used for training, calibration or model selection, never pooled with other test slices",
        },
        "rule": "a call takes the slice of the fixture set its run used (run.json fixture_set); build_split refuses round2_test gold; use_allowed() gives each slice's permitted uses; spangloss.eval.split check-sets",
        "frozen": "2026-11-17, before any round-2 group B run that counts (the two group-B pilot runs predate the G2 rebuild and are discarded)",
        "fixture_sets": {k: dict(sorted(v.items())) for k, v in sets.items()},
    }


def set_slices(path: Path = SPLIT_V2_FILE) -> dict[tuple[str, str], str]:
    """(scenario, fixture set) -> slice, from split v2."""
    body = json.loads(path.read_text(encoding="utf-8"))
    return {
        (scenario, set_id): slice_
        for scenario, slices in body["fixture_sets"].items()
        for slice_, ids in slices.items()
        for set_id in ids
    }


def check_sets(
    slices: dict[tuple[str, str], str], scenarios: dict[str, dict[str, str]]
) -> list[str]:
    """Problems where scenarios' fixture sets ({scenario: {set: split}}) and split
    v2 disagree: a set missing from either, or on a different side."""
    problems = []
    declared = {
        (name, sid): slice_for(name, split, sid)
        for name, sets in scenarios.items()
        for sid, split in sets.items()
    }
    for key in sorted(set(declared) | set(slices)):
        if declared.get(key) != slices.get(key):
            problems.append(
                f"{key[0]} {key[1]}: scenario says {declared.get(key)},"
                f" split v2 says {slices.get(key)}"
            )
    return problems


def _scenario_sets() -> dict[str, dict[str, str]]:
    from spangloss.scenarios import load_scenario, scenario_names

    out = {}
    for name in scenario_names():
        sets = load_scenario(name).fixture_sets
        if sets:
            out[name] = {s.id: s.split for s in sets}
    return out


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _inputs() -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, ParsedToolCall],
    dict[str, tuple[str, str]],
]:
    real = _read_jsonl(REAL_GOLD)
    scen = _read_jsonl(SCENARIO_GOLD)
    calls = {
        r["span_id"]: ParsedToolCall(**r)
        for r in _read_jsonl(REAL_CALLS)
        if r["span_id"] in {g["span_id"] for g in real}
    }
    variant_of: dict[str, tuple[str, str]] = {}
    for run_dir in sorted(RUNS_DIR.glob("*")):
        run_file = run_dir / "run.json"
        if not run_file.exists():
            continue
        run = json.loads(run_file.read_text(encoding="utf-8"))
        for r in _read_jsonl(run_dir / "tool_calls.jsonl"):
            calls.setdefault(r["span_id"], ParsedToolCall(**r))
            variant_of[r["span_id"]] = (
                run["scenario"],
                run.get("prompt_id") or "default",
            )
    return real, scen, calls, variant_of


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "check"
    if command == "write-v4":
        body = v4_body(_scenario_sets())
        SPLIT_V4_FILE.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {SPLIT_V4_FILE}", file=sys.stderr)
        return 0
    if command == "check-sets":
        problems = check_sets(all_set_slices(), _scenario_sets())
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        if not problems:
            print(
                f"{SPLIT_V2_FILE}, {SPLIT_V3_FILE} and {SPLIT_V4_FILE} match the"
                " scenarios' fixture sets",
                file=sys.stderr,
            )
        return 1 if problems else 0
    real, scen, calls, variant_of = _inputs()
    side = build_split(real, scen, calls, variant_of)
    hashed = {span_hash(k): v for k, v in side.items()}
    lines = "".join(
        f"{v} {h}\n" for h, v in sorted(hashed.items(), key=lambda x: (x[1], x[0]))
    )
    counts = summary(side, real + scen, calls)
    if command == "build":
        SPLIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "split": SPLIT_VERSION,
            "spans": f"{SPLIT_FILE.name}: <side> <sha256 of the span ID, hex>",
            "taxonomy": scen[0]["taxonomy"],
            "rule": "spangloss.eval.split",
            "counts": counts,
        }
        SPLIT_FILE.write_text(lines, encoding="utf-8")
        COUNTS_FILE.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {SPLIT_FILE} and {COUNTS_FILE}", file=sys.stderr)
    elif command == "check":
        if load_split(SPLIT_FILE) != hashed:
            print(f"error: {SPLIT_FILE} doesn't match the gold", file=sys.stderr)
            return 1
        print(f"{SPLIT_FILE} matches the gold", file=sys.stderr)
    else:
        print(f"error: unknown command {command}", file=sys.stderr)
        return 1
    for s in ("test", "train"):
        c = counts[s]
        inj = c["labels"].get("sec.injection", {})
        print(
            f"{s}: {c['calls']} calls, {c['shapes']} shapes, injection {inj}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
