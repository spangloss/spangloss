"""Training records for a Kev fine-tune: the requests spangloss sends, answered from gold.

Each gold call runs through label_call against a backend that records every request
(evidence and question specs) and answers nothing of consequence. A record is one
request: {"state": evidence, "questions": {question: spec + gold label}}, so it
carries the same evidence, question specs and output chunks as labeling.

- data_class questions are left out (no sensitive positive in training data).
- Chunked injection, gold yes: a chunk holding the whole payload is yes, a chunk
  holding none of it is no, and a chunk holding part of it (the payload straddles a
  chunk boundary) loses its injection question. A payload is located by its token
  sequence, which survives comment, string and log-line wrapping.
- A record left with no question is dropped.
- Calibration and development records whose state also occurs in train are dropped.

Payload text is never in this module: callers pass the payloads of each call, read
at run time from local overlays.

    uv run python -m spangloss.eval.records pilot    # rebuild the pilot's records, diff
    uv run python -m spangloss.eval.records round2   # build round 2's records

Both read only local files and make no network calls.
"""

import json
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from spangloss.backends.base import AskResult, ModelInfo, QuestionSpec
from spangloss.parse.record import ParsedToolCall
from spangloss.questions import label_call, load_cards

LEFT_OUT = frozenset({"data_class", "data_class.sensitive"})
TOKEN = re.compile(r"[a-z0-9]+")


class Recorder:
    """A backend that keeps each request and answers uniformly."""

    name = "recorder"
    model = "recorder"

    def __init__(self) -> None:
        self.requests: list[tuple[dict[str, Any], dict[str, QuestionSpec]]] = []

    def screen(self, call: ParsedToolCall) -> str | None:
        return None

    def ask(
        self, evidence: dict[str, Any], questions: dict[str, QuestionSpec]
    ) -> AskResult:
        self.requests.append((evidence, questions))
        answers = {}
        for q, spec in questions.items():
            keys = ["yes", "no"] if spec.kind == "noul" else list(spec.criteria)
            answers[q] = {k: 1.0 / len(keys) for k in keys}
        return AskResult(answers=answers, input_tokens=None)

    def describe(self) -> ModelInfo:
        return ModelInfo(
            name="recorder", model="recorder", checkpoint=None, card=None, served=()
        )


MIN_OVERLAP = (
    8  # characters of payload across a chunk boundary that count as straddling
)


def locate(payload: str, chunk: str) -> str:
    """ "contains", "clear" or "straddles": where a payload's token sequence sits in
    a chunk. Straddling: the chunk starts with the payload's tail or ends with its
    head, compared by characters, since a boundary can cut a token (a base64 blob)."""
    want, have = TOKEN.findall(payload.lower()), TOKEN.findall(chunk.lower())
    if not want:
        return "clear"
    p, c = " ".join(want), " ".join(have)
    if f" {p} " in f" {c} ":
        return "contains"
    # A page can show the payload escaped (&lt;user&gt;) or as markup: by_text's reading.
    from spangloss.scenarios.check import payload_shown

    if payload_shown(payload, chunk) == "all":
        return "contains"
    for k in range(min(len(p), len(c)) - 1, MIN_OVERLAP - 1, -1):
        if c.startswith(p[-k:]) or c.endswith(p[:k]):
            return "straddles"
    return "clear"


def spec_record(spec: QuestionSpec, label: str) -> dict[str, Any]:
    if spec.kind == "noul":
        return {
            "type": "noul",
            "instructions": spec.instructions,
            "criteria": {"true": spec.criteria["yes"], "false": spec.criteria["no"]},
            "label": label == "yes",
        }
    return {
        "type": "choice",
        "instructions": spec.instructions,
        "criteria": dict(spec.criteria),
        "label": label,
    }


@dataclass
class Item:
    """One gold call to turn into records."""

    call: ParsedToolCall
    answers: dict[str, str]  # gold card answers, by question
    payloads: list[str]  # the call's payload texts (for the chunk rule)
    partition: str
    meta: dict[str, Any] = field(default_factory=dict)


def build(items: Iterable[Item]) -> tuple[dict[str, list[tuple[dict, dict]]], Counter]:
    """partition -> [(record, meta)], and counts of what the rules did."""
    cards = load_cards()
    out: dict[str, list[tuple[dict, dict]]] = {}
    stats: Counter[str] = Counter()
    for item in items:
        rec = Recorder()
        label_call(item.call, cards, rec)
        # Chunked: the output was split across requests (other evidence may differ too).
        chunked = len({e.get("output") for e, _ in rec.requests if e.get("output")}) > 1
        for evidence, questions in rec.requests:
            qs = {}
            for q, spec in questions.items():
                if q in LEFT_OUT or q not in item.answers:
                    continue
                answer = item.answers[q]
                if q == "injection" and answer == "yes" and chunked:
                    where = {
                        locate(p, evidence.get("output") or "") for p in item.payloads
                    }
                    if "contains" in where:
                        stats["chunk: contains"] += 1
                    elif where <= {"clear"}:
                        answer = "no"
                        stats["chunk: clear"] += 1
                    else:
                        stats["chunk: straddles (question dropped)"] += 1
                        continue
                qs[q] = spec_record(spec, answer)
            if qs:
                meta = {"span_id": item.call.span_id, **item.meta, "chunked": chunked}
                out.setdefault(item.partition, []).append(
                    ({"state": evidence, "questions": qs}, meta)
                )
            else:
                stats["request dropped: no question left"] += 1
    return out, stats


def drop_seen(
    parts: dict[str, list[tuple[dict, dict]]], train: str = "train"
) -> Counter:
    """Drop non-train records whose state also occurs in train."""
    seen = {json.dumps(r["state"], sort_keys=True) for r, _ in parts.get(train, [])}
    dropped: Counter[str] = Counter()
    for name, rows in list(parts.items()):
        if name == train:
            continue
        keep = [
            (r, m)
            for r, m in rows
            if json.dumps(r["state"], sort_keys=True) not in seen
        ]
        dropped[name] = len(rows) - len(keep)
        parts[name] = keep
    return dropped


def write(parts: dict[str, list[tuple[dict, dict]]], out: Path) -> None:
    (out / "split").mkdir(parents=True, exist_ok=True)
    (out / "meta").mkdir(parents=True, exist_ok=True)
    for name, rows in parts.items():
        (out / f"split/{name}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r, _ in rows),
            encoding="utf-8",
        )
        (out / f"meta/{name}.meta.jsonl").write_text(
            "".join(json.dumps(m) + "\n" for _, m in rows), encoding="utf-8"
        )


def summary(parts: dict[str, list[tuple[dict, dict]]]) -> dict[str, dict[str, int]]:
    out = {}
    for name, rows in parts.items():
        inj = Counter(
            r["questions"]["injection"]["label"]
            for r, _ in rows
            if "injection" in r["questions"]
        )
        out[name] = {"records": len(rows), "yes": inj[True], "no": inj[False]}
    return out


# --- sources ---------------------------------------------------------------------------


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


def batch1_payloads() -> dict[str, list[str]]:
    """canary -> payload texts, from the local batch-1 generator (git-ignored), by
    recording what it writes."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "gen_batch1", Path("scenarios/_gen/batch1.py")
    )
    assert spec and spec.loader
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    found: dict[str, list[str]] = {}
    original = gen.Words.payload

    def recording(self: Any, style: str, canary: str, pkg: str) -> str:
        text = original(self, style, canary, pkg)
        found.setdefault(canary, []).append(text)
        return text

    gen.Words.payload = recording
    for family in gen.FAMILIES:
        gen.family_files(family)
    return found


def pilot_items(
    canary_of: Callable[[str], str | None], payloads: dict[str, list[str]]
) -> list[Item]:
    """The pilot's calls, in its record order, with its partitions."""
    root = Path("data/scenarios")
    gold: dict[str, dict] = {}
    for f in (
        root / "batch1/gold.jsonl",
        root / "batch1/owner_resolved.jsonl",
        root / "gold.jsonl",
    ):
        gold |= {r["span_id"]: r for r in _jsonl(f)}
    calls = {r["span_id"]: r for r in _jsonl(root / "batch1/all_calls.jsonl")}
    items, seen = [], set()
    for part in ("train", "calibration", "development"):
        for m in _jsonl(Path(f"data/finetune/pilot/meta/{part}.meta.jsonl")):
            sid = m["span_id"]
            if sid in seen:
                continue
            seen.add(sid)
            canary = canary_of(sid)
            items.append(
                Item(
                    ParsedToolCall(**calls[sid]),
                    gold[sid]["answers"],
                    payloads.get(canary, []) if canary else [],
                    part,
                    {"group": m["group"], "family": m["family"]},
                )
            )
    return items


def _diff_pilot(out: Path) -> int:
    from spangloss.scenarios import load_scenario

    runs = Path("data/scenarios/runs")
    set_of: dict[str, tuple[str, str]] = {}
    for d in runs.glob("*"):
        if not (d / "run.json").exists() or not (d / "tool_calls.jsonl").exists():
            continue
        run = json.loads((d / "run.json").read_text())
        if run.get("fixture_set"):
            for c in _jsonl(d / "tool_calls.jsonl"):
                set_of[c["span_id"]] = (run["scenario"], run["fixture_set"])
    canaries = {}

    def canary_of(sid: str) -> str | None:
        if sid not in set_of:
            return None
        key = set_of[sid]
        if key not in canaries:
            canaries[key] = load_scenario(key[0]).fixture_set(key[1]).canary
        return canaries[key]

    parts, stats = build(pilot_items(canary_of, batch1_payloads()))
    dropped = drop_seen(parts)
    write(parts, out)
    print("rules:", dict(stats), "dropped (state in train):", dict(dropped))
    differ = 0
    for part in ("train", "calibration", "development"):
        mine = [json.dumps(r, sort_keys=True) for r, _ in parts.get(part, [])]
        theirs = [
            json.dumps(r, sort_keys=True)
            for r in _jsonl(Path(f"data/finetune/pilot/split/{part}.jsonl"))
        ]
        same_order = mine == theirs
        only_mine = Counter(mine) - Counter(theirs)
        only_theirs = Counter(theirs) - Counter(mine)
        differ += bool(only_mine or only_theirs)
        print(
            f"{part}: rebuilt {len(mine)}, pilot {len(theirs)}; identical"
            f" {'in order' if same_order else 'as sets' if not (only_mine or only_theirs) else 'NO'};"
            f" only rebuilt {sum(only_mine.values())}, only pilot {sum(only_theirs.values())}"
        )
    return 1 if differ else 0


# --- round 2 ---------------------------------------------------------------------------

# The batch-1 generator's largest share of injection-yes train records (exact, so a
# third of 30 is 10, not 9).
GENERATOR_CAP = Fraction(1, 3)
CAP_SEED = 20261121
BATCH1_FAMILIES = frozenset({"repo_review", "command_output", "hard_negatives_v2"})


def _scenario_of_runs() -> dict[str, tuple[str, str]]:
    out = {}
    for d in Path("data/scenarios/runs").glob("*"):
        if (d / "run.json").exists() and (d / "tool_calls.jsonl").exists():
            run = json.loads((d / "run.json").read_text())
            if run.get("fixture_set"):
                for c in _jsonl(d / "tool_calls.jsonl"):
                    out[c["span_id"]] = (run["scenario"], run["fixture_set"])
    return out


def round2_items() -> list[Item]:
    """Group A's final gold (MCP and curl), by split v4. Held-out data is refused."""
    from spangloss.eval.split import (
        ROUND2_CALIB_SLICE,
        ROUND2_TRAIN_SLICE,
        HeldOutError,
        refuse_held_out,
        slice_for,
        use_allowed,
    )
    from spangloss.scenarios import load_scenario
    from spangloss.scenarios.check import _hit_carriers

    root = Path("data/scenarios/round2")
    gold = _jsonl(root / "gold-a.final.jsonl")
    refuse_held_out(gold)
    calls = {c["span_id"]: c for c in _jsonl(root / "tool_calls-a.jsonl")}
    runs = Path("data/scenarios/runs")
    items, cache = [], {}
    for g in gold:
        call = ParsedToolCall(**calls[g["span_id"]])
        if call.tool_name == "WebFetch":
            continue  # not used for training
        run = json.loads((runs / g["source"]["run_id"] / "run.json").read_text())
        slice_ = slice_for(run["scenario"], "local", run["fixture_set"])
        part = {ROUND2_TRAIN_SLICE: "train", ROUND2_CALIB_SLICE: "calibration"}.get(
            slice_
        )
        purpose = "train" if part == "train" else "calibrate"
        if part is None or not use_allowed(slice_, purpose):
            raise HeldOutError(f"{run['scenario']} {run['fixture_set']}: {slice_}")
        key = (run["scenario"], run["fixture_set"])
        s = cache.setdefault(key, load_scenario(key[0], fixture_set=key[1]))
        hit = sorted(_hit_carriers(s, call))
        payloads = [t for c in hit for t in s.payload_texts.get(c, ())]
        source = next(
            (
                s.active_set.payloads[c].split("/")[0]
                for c in hit
                if c in s.active_set.payloads
            ),
            None,
        )
        items.append(
            Item(
                call,
                g["answers"],
                payloads,
                part,
                {
                    "group": f"r2:{key[1]}",
                    "family": key[0],
                    "carrier": ",".join(hit),
                    "source": source,
                },
            )
        )
    return items


def _pilot_rows(part: str) -> list[tuple[dict, dict]]:
    """The pilot's records (identical to a rebuild), with carrier and payload source:
    batch-1 payloads come from the batch-1 generator; v0's are hand-built."""
    from spangloss.scenarios import load_scenario
    from spangloss.scenarios.check import _hit_carriers

    calls = {
        c["span_id"]: c for c in _jsonl(Path("data/scenarios/batch1/all_calls.jsonl"))
    }
    where = _scenario_of_runs()
    cache: dict[tuple[str, str], Any] = {}
    rows = []
    for r, m in zip(
        _jsonl(Path(f"data/finetune/pilot/split/{part}.jsonl")),
        _jsonl(Path(f"data/finetune/pilot/meta/{part}.meta.jsonl")),
        strict=True,
    ):
        carrier = None
        if m["span_id"] in where:
            key = where[m["span_id"]]
            s = cache.setdefault(key, load_scenario(key[0], fixture_set=key[1]))
            carrier = (
                ",".join(
                    sorted(_hit_carriers(s, ParsedToolCall(**calls[m["span_id"]])))
                )
                or None
            )
        source = "generator" if m["family"] in BATCH1_FAMILIES else "v0"
        rows.append((r, {**m, "carrier": carrier, "source": source, "from": "pilot"}))
    return rows


def _is_yes(r: dict) -> bool:
    return bool(r["questions"].get("injection", {}).get("label") is True)


def cap_generator(
    rows: list[tuple[dict, dict]],
) -> tuple[list[tuple[dict, dict]], Counter]:
    """Drop generator injection-yes records, seeded and stratified by carrier, until
    the generator holds at most GENERATOR_CAP of the injection-yes records."""
    import random

    yes = [i for i, (r, _) in enumerate(rows) if _is_yes(r)]
    gen = [i for i in yes if rows[i][1].get("source") == "generator"]
    other = len(yes) - len(gen)
    keep_gen = int(other * GENERATOR_CAP / (1 - GENERATOR_CAP))
    n_drop = max(0, len(gen) - keep_gen)
    by_carrier: dict[str, list[int]] = {}
    for i in gen:
        by_carrier.setdefault(rows[i][1].get("carrier") or "?", []).append(i)
    # Proportional allocation, largest remainder; random within a carrier.
    quotas = (
        {c: n_drop * len(v) / len(gen) for c, v in by_carrier.items()} if gen else {}
    )
    alloc = {c: int(q) for c, q in quotas.items()}
    for c in sorted(quotas, key=lambda c: (quotas[c] - alloc[c], c), reverse=True)[
        : n_drop - sum(alloc.values())
    ]:
        alloc[c] += 1
    rng = random.Random(CAP_SEED)
    drop: set[int] = set()
    for c in sorted(by_carrier):
        drop |= set(rng.sample(sorted(by_carrier[c]), alloc.get(c, 0)))
    stats = Counter({"injection yes": len(yes), "generator yes before": len(gen),
                     "generator yes dropped": len(drop)})  # fmt: skip
    for c in sorted(by_carrier):
        stats[f"dropped on {c}"] = len(set(by_carrier[c]) & drop)
    return [row for i, row in enumerate(rows) if i not in drop], stats


def _round2(out: Path) -> int:
    parts, stats = build(round2_items())
    r2_train = parts.get("train", [])
    for r, m in r2_train:
        m["from"] = "round2"
    train = _pilot_rows("train") + r2_train
    yes = [m for r, m in train if _is_yes(r)]
    gen = sum(m.get("source") == "generator" for m in yes)
    print(f"train before the cap: {len(train)} records; injection yes {len(yes)},"
          f" generator {gen} ({gen / len(yes):.1%})")  # fmt: skip
    parts["train"] = train
    if gen / len(yes) > GENERATOR_CAP:
        parts["train"], cap = cap_generator(train)
        print("cap:", dict(cap))
    parts["development"] = [
        (r, m) for r, m in zip(
            _jsonl(Path("data/finetune/pilot/split/development.jsonl")),
            _jsonl(Path("data/finetune/pilot/meta/development.meta.jsonl")),
            strict=True,
        )
    ]  # fmt: skip
    dropped = drop_seen(parts)
    write(parts, out)
    yes = [m for r, m in parts["train"] if _is_yes(r)]
    gen = sum(m.get("source") == "generator" for m in yes)
    print("rules:", dict(stats), "dropped (state in train):", dict(dropped))
    print(f"train after: injection yes {len(yes)}, generator {gen} ({gen / len(yes):.1%});"
          f" by source {dict(Counter(m.get('source') for m in yes))}")  # fmt: skip
    for name, c in summary(parts).items():
        print(
            f"{name}: {c['records']} records; injection yes {c['yes']} / no {c['no']}"
        )
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["pilot"]:
        return _diff_pilot(
            Path(argv[1]) if len(argv) > 1 else Path("data/finetune/pilot-rebuilt")
        )
    if argv[:1] == ["round2"]:
        return _round2(Path(argv[1]) if len(argv) > 1 else Path("data/finetune/round2"))
    print(
        "usage: python -m spangloss.eval.records pilot [out] | round2 [out]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
