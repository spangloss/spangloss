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
