"""Generate the v0 per-label results: site/results.json and docs/results-v0.md.

    uv run python -m spangloss.resultsgen          # write both from the label stores
    uv run python -m spangloss.resultsgen --check  # fail if either is out of date

The numbers come from eval/score.py over the stored labels and gold, with no model
calls. data/ is git-ignored, so site/results.json is a committed snapshot: it is
generated locally and records its inputs (gold files, label stores, call files) by
hash. docs/results-v0.md is rendered from site/results.json alone, so a fresh clone
can check it. `--check` fails when the Markdown doesn't match the JSON, and, where
the inputs exist locally, when any input's hash has changed since generation.

Only aggregates leave data/: counts, scores and generic shape names, never call
contents, arguments, paths or session IDs. The two gold sets are scored apart and
never pooled.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from spangloss.backends.support import unsupported_labels
from spangloss.cli import read_calls
from spangloss.eval.score import LabelScore, ModelScore, score
from spangloss.eval.shapes import call_shape
from spangloss.eval.sheet import read_gold
from spangloss.questions.cards import Card, load_cards
from spangloss.questions.derived import DERIVED_MODEL
from spangloss.store import LabelStore

JSON_OUTPUT = Path("site/results.json")
MD_OUTPUT = Path("docs/results-v0.md")
GENERATOR = "python -m spangloss.resultsgen"
SCHEMA = "spangloss-results/1"
SMALL_N = 10  # cells resting on fewer calls are flagged
INJECTION = "sec.injection"
DATA_CLASS = "sec.data_class"
KNOWN_LIMITATIONS = "docs/findings/known-limitations.md"
NOT_REPORTED_SEE = f"{KNOWN_LIMITATIONS}#secinjection-needs-jev"
# Sections of known-limitations.md the results link to, by heading text and anchor;
# test_resultsgen checks each anchor against the file's headings.
LIMITATIONS = (
    ("`sec.injection` needs Jev", "secinjection-needs-jev"),
    (
        "`.env.test` Reads with captured content are often labeled `secrets`",
        "envtest-reads-with-captured-content-are-often-labeled-secrets",
    ),
    (
        "`sec.data_class` `secrets` has low precision",
        "secdata_class-secrets-has-low-precision",
    ),
    (
        "Kev's `sec.intent` misses on real-session Bash calls",
        "kevs-secintent-misses-on-real-session-bash-calls",
    ),
    ("Trifecta errors on real sessions", "trifecta-errors-on-real-sessions"),
    ("Kev is less well calibrated than Jev", "kev-is-less-well-calibrated-than-jev"),
)


@dataclass(frozen=True, slots=True)
class GoldSet:
    id: str
    name: str
    description: str
    gold: Path
    labels: Path
    calls: Path


GOLD_SETS = (
    GoldSet(
        "real_sessions",
        "Real sessions",
        "Tool calls from real interactive Claude Code sessions, labeled by hand.",
        Path("data/eval/gold.jsonl"),
        Path("data/labels.duckdb"),
        Path("data/parsed/tool_calls.jsonl"),
    ),
    GoldSet(
        "scenarios",
        "Seeded scenarios",
        "Tool calls from scripted Claude Code runs in an isolated container over"
        " fake data, labeled by expectations written before the runs.",
        Path("data/scenarios/gold.jsonl"),
        Path("data/scenarios/labels.duckdb"),
        Path("data/scenarios/tool_calls.jsonl"),
    ),
)
# (backend, display name, where it runs), in display order.
MODELS = (("kev", "Kev", "local"), ("jev", "Jev", "hosted"))

# Generic names for injection call shapes. Scenario records are named by their
# scenario and expectation, hand-reviewed scenario records by their review sheet.
# A gold `yes` must have a named shape; everything else falls into OTHER_SCENARIO.
NAMED_SHAPES: dict[tuple[str, str], str] = {
    ("web_injection_curl", "curl-notes"): "curl control",
    ("poisoned_dependency", "read-lib-readme"): "dependency README",
    ("hard_negatives", "read-security-doc"): "security doc with framed examples",
    ("hard_negatives", "read-agents-md"): "agent instruction file with build steps",
}
REVIEW_SHAPES: dict[str, str] = {
    "review_001.csv": "WebFetch summary reporting an injection",
}
OTHER_SCENARIO = "other scenario calls"
REAL_SESSION = "real-session calls"
SHAPE_ORDER = (
    "curl control",
    "dependency README",
    "WebFetch summary reporting an injection",
    "security doc with framed examples",
    "agent instruction file with build steps",
    OTHER_SCENARIO,
    REAL_SESSION,
)

# The owner's approved lead.
LEAD = {
    "status": "approved",
    "text": (
        "These are Spangloss v0's per-label results at taxonomy 0.4.0, on two gold"
        " sets scored separately: 20 hand-labeled calls from real sessions and 111"
        " calls from seeded scenarios. On the scenarios, each label's accuracy runs"
        " from 86.5% to 100% for Jev, the hosted labeler, and from 89.2% to 100% for"
        " Kev, the local one. Jev caught all 11 planted injections; Kev isn't scored"
        " on injection in v0. Both labelers over-call secrets (precision 5/17 for"
        " Kev, 5/20 for Jev). Kev's probabilities run consistently below its"
        " accuracy (ECE up to 0.30, against at most 0.13 for Jev), and on real"
        " sessions its intent accuracy drops to 61.5%. The real-session set is small"
        " and holds no injections, so Jev's 100% there shows only that it raised no"
        " false alarms. All of it comes from one agent's coding sessions: these"
        " numbers describe these sets, not agent sessions in general."
    ),
}


class ResultsError(ValueError):
    pass


def build(sets: Iterable[GoldSet] = GOLD_SETS, today: date | None = None) -> dict:
    cards = load_cards()
    taxonomy = _one({c.taxonomy for c in cards}, "card taxonomy versions")
    inputs, gold_sets, injection_rows, sec_models = [], [], [], {}
    for gold_set in sets:
        gold = read_gold(gold_set.gold)
        if {r["taxonomy"] for r in gold} != {taxonomy}:
            raise ResultsError(f"{gold_set.gold}: gold is not all at {taxonomy}")
        calls = read_calls(gold_set.calls)
        shapes = {c.span_id: call_shape(c) for c in calls}
        missing_shape = [r["span_id"] for r in gold if r["span_id"] not in shapes]
        if missing_shape:
            raise ResultsError(f"{len(missing_shape)} gold calls have no parsed call")
        with LabelStore(gold_set.labels, read_only=True) as store:
            stored = store.stored_labels()
        results = score(gold, stored, cards, shapes)
        for result in results:
            if result.skipped_taxonomy:
                raise ResultsError(
                    f"{gold_set.labels}: {result.skipped_taxonomy} {result.model}"
                    f" labels are not at {taxonomy}"
                )
        by_backend = _by_backend(results)
        for backend, result in by_backend.items():
            if backend != DERIVED_MODEL:
                sec_models.setdefault(backend, set()).add(result.model)
        gold_sets.append(_gold_set_entry(gold_set, gold, shapes, cards, by_backend))
        injection_rows += _injection_rows(gold_set, gold, stored)
        inputs += [
            _input(gold_set, "gold", gold_set.gold, records=len(gold)),
            _input(gold_set, "labels", gold_set.labels, rows=len(stored)),
            _input(gold_set, "calls", gold_set.calls, records=len(calls)),
        ]
    return {
        "schema": SCHEMA,
        "title": "Spangloss v0 results",
        "taxonomy": taxonomy,
        "provenance": {
            "generator": GENERATOR,
            "generated": (today or date.today()).isoformat(),
            "commit": _git("rev-parse", "HEAD"),
            "source_clean": not _git(
                "status", "--porcelain", "--", "src", "TAXONOMY.md"
            ),
            "inputs": inputs,
        },
        "small_n": SMALL_N,
        "models": [
            {
                "id": backend,
                "name": name,
                "runs": where,
                "sec_model": _one(sec_models.get(backend, set()), f"{backend} models"),
                "not_reported": sorted(unsupported_labels(backend)),
            }
            for backend, name, where in MODELS
        ],
        "lead": LEAD,
        "gold_sets": gold_sets,
        "injection_by_shape": sorted(
            injection_rows,
            key=lambda r: (
                [s["id"] for s in gold_sets].index(r["set"]),
                SHAPE_ORDER.index(r["shape"]),
            ),
        ),
        "known_limitations": [
            {"title": title, "see": f"{KNOWN_LIMITATIONS}#{anchor}"}
            for title, anchor in LIMITATIONS
        ],
        "method": _method_notes(gold_sets),
    }


def _by_backend(results: list[ModelScore]) -> dict[str, ModelScore]:
    out: dict[str, ModelScore] = {}
    for result in results:
        backend = result.model.split("/", 1)[0]
        if backend in out:
            raise ResultsError(f"two {backend} models in one store")
        out[backend] = result
    unknown = set(out) - {b for b, _, _ in MODELS} - {DERIVED_MODEL}
    if unknown:
        raise ResultsError(f"unknown models in the store: {sorted(unknown)}")
    return out


def _gold_set_entry(
    gold_set: GoldSet,
    gold: list[dict[str, Any]],
    shapes: dict[str, str],
    cards: list[Card],
    by_backend: dict[str, ModelScore],
) -> dict[str, Any]:
    derived = {s.label: s for s in by_backend.get(DERIVED_MODEL, ModelScore("")).labels}
    labels = []
    order = list(dict.fromkeys(c.label for c in cards))
    gold_labels = {label for r in gold for label in r["labels"]}
    for label in (x for x in order if x in gold_labels):
        positive = _positive(label, cards)
        models = {}
        for backend, _, _ in MODELS:
            scores = {s.label: s for s in by_backend[backend].labels}
            models[backend] = _model_entry(scores[label], positive, derived.get(label))
        labels.append(
            {
                "label": label,
                "positive": positive,
                "gold_n": sum(1 for r in gold if label in r["labels"]),
                "models": models,
            }
        )
    return {
        "id": gold_set.id,
        "name": gold_set.name,
        "description": gold_set.description,
        "calls": len(gold),
        "shapes": len({shapes[r["span_id"]] for r in gold}),
        "labels": labels,
        "set_by_rule": [
            {"label": s.label, "n": s.n, "agrees_with_gold": _correct(s)}
            for s in derived.values()
            if s.n
        ],
    }


def _model_entry(
    s: LabelScore, positive: str | None, derived: LabelScore | None
) -> dict[str, Any]:
    if s.unsupported:
        return {"status": "not_reported", "see": NOT_REPORTED_SEE}
    # score() counts a gold label the model never produced as missing, including
    # calls whose label is set by rule; only the rest went unlabeled.
    not_labeled = (s.missing or 0) - (derived.n if derived else 0)
    if not_labeled < 0:
        raise ResultsError(f"{s.label}: more rule-set labels than missing ones")
    # Unrounded, so a renderer formats the same value eval/score.py's report does.
    entry: dict[str, Any] = {
        "status": "scored",
        "n": s.n,
        "correct": _correct(s),
        "accuracy": s.accuracy,
        "ci95": list(s.ci) if s.ci else None,
        "shapes": s.shapes,
        "shape_accuracy": s.shape_accuracy,
        "ece": s.ece,
        "not_labeled": not_labeled,
    }
    if positive:
        hits = s.confusion.get((positive, positive), 0)
        answer = next((a for a in s.answers if a.answer == positive), None)
        entry["precision"] = {
            "hits": hits,
            "of": answer.predicted_count if answer else 0,
        }
        entry["recall"] = {"hits": hits, "of": answer.gold_count if answer else 0}
    return entry


def _positive(label: str, cards: list[Card]) -> str | None:
    """The answer whose precision and recall are reported: yes, or secrets."""
    if label == DATA_CLASS:
        return "secrets"
    group = [c for c in cards if c.label == label]
    if len(group) == 1 and group[0].kind == "noul":
        return "yes"
    return None


def _injection_rows(
    gold_set: GoldSet, gold: list[dict[str, Any]], stored: list
) -> list[dict[str, Any]]:
    answers = {
        (x.model.split("/", 1)[0], x.span_id): x.answer
        for x in stored
        if x.label == INJECTION
    }
    rows: dict[str, dict[str, Any]] = {}
    for record in gold:
        if INJECTION not in record["labels"]:
            continue
        shape = injection_shape(gold_set.id, record)
        gold_answer = record["labels"][INJECTION]
        if gold_answer == "yes" and shape in (OTHER_SCENARIO, REAL_SESSION):
            raise ResultsError("an injection positive has no named shape")
        row = rows.setdefault(
            shape,
            {
                "set": gold_set.id,
                "shape": shape,
                "gold": gold_answer,
                "n": 0,
                "models": {
                    b: (
                        {"status": "not_reported", "see": NOT_REPORTED_SEE}
                        if INJECTION in unsupported_labels(b)
                        else {"status": "scored", "labeled": 0, "yes": 0}
                    )
                    for b, _, _ in MODELS
                },
            },
        )
        if row["gold"] != gold_answer:
            raise ResultsError(f"{shape}: gold answers differ within the shape")
        row["n"] += 1
        for backend, entry in row["models"].items():
            answer = answers.get((backend, record["span_id"]))
            if entry["status"] == "scored" and answer is not None:
                entry["labeled"] += 1
                entry["yes"] += answer == "yes"
    return list(rows.values())


def injection_shape(set_id: str, record: dict[str, Any]) -> str:
    if set_id == "real_sessions":
        return REAL_SESSION
    source = record.get("source") or {}
    key = (source.get("scenario"), source.get("expectation"))
    if key in NAMED_SHAPES:
        return NAMED_SHAPES[key]
    if not source and record.get("sheet") in REVIEW_SHAPES:
        return REVIEW_SHAPES[record["sheet"]]
    return OTHER_SCENARIO


def _method_notes(gold_sets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "text": (
                "Real-session gold was labeled by hand, call by call. Scenario gold"
                " comes from each scenario's expectations, written before its runs;"
                " calls no expectation could settle were labeled by hand in review."
            ),
            "see": ["TAXONOMY.md", "docs/scenarios-design.md"],
        },
        {
            "text": (
                "A label is scored only where the taxonomy asks it, so n differs by"
                " label. A labeler's answer is its most probable one (0.5 for yes/no"
                " labels). Calls a labeler didn't label, such as calls the hosted"
                " labeler's secret screen refused, are listed as not labeled, not"
                " counted as errors."
            ),
            "see": [],
        },
        {
            "text": (
                "Accuracy carries a 95% Wilson score interval. Scenario runs repeat"
                " the same calls, so accuracy per shape counts each unique call shape"
                " (the tool plus its normalized arguments) once, as the share of its"
                " calls answered correctly, and averages over shapes."
            ),
            "see": [],
        },
        {
            "text": (
                "ECE (expected calibration error, 10 equal-width bins) compares the"
                " probability of each answer with whether it was right. At these"
                " sizes it is only indicative."
            ),
            "see": [],
        },
        {
            "text": (
                "Labels set by rule are written by code from facts the parser already"
                " knows, without asking a labeler. They are listed apart and not"
                " counted in either labeler's accuracy."
            ),
            "see": ["TAXONOMY.md"],
        },
        {
            "text": (
                "Claude Code's telemetry doesn't capture everything the agent sees,"
                " and the labelers have known weaknesses that v0 ships with."
            ),
            "see": ["docs/findings/capture-gaps.md", KNOWN_LIMITATIONS],
        },
    ]


def _input(gold_set: GoldSet, kind: str, path: Path, **counts: int) -> dict:
    return {
        "set": gold_set.id,
        "kind": kind,
        "path": path.as_posix(),
        **counts,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _correct(s: LabelScore) -> int:
    return sum(v for (g, p), v in s.confusion.items() if g == p)


def _one(values: set[str], what: str) -> str:
    if len(values) != 1:
        raise ResultsError(f"expected one of {what}, found {sorted(values)}")
    return next(iter(values))


# --- Markdown -----------------------------------------------------------------

MD_LINK_BASE = "../"  # docs/results-v0.md links to repo paths from docs/
DAGGER = " †"


def render_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def render_markdown(data: dict[str, Any]) -> str:
    small = data["small_n"]
    names = {m["id"]: m["name"] for m in data["models"]}
    prov = data["provenance"]
    lines = [
        f"# {data['title']}",
        "",
        (
            f"<!-- Generated from site/results.json by {GENERATOR}; don't edit by"
            " hand. -->"
        ),
        "",
    ]
    lead = data["lead"]
    if lead["status"] != "approved":
        lines += [f"> **Lead ({lead['status']}):**", ">"]
        lines += [f"> {lead['text']}", ""]
    else:
        lines += [lead["text"], ""]
    lines += [
        f"Taxonomy {data['taxonomy']}. Generated {prov['generated']} at commit"
        f" `{prov['commit'][:7]}`. Labelers: "
        + ", ".join(f"{m['name']} ({m['runs']})" for m in data["models"])
        + f". † marks a cell resting on fewer than {small} calls.",
        "",
        "## Gold sets",
        "",
        "The two sets are scored separately and never pooled.",
        "",
        "| Gold set | What it is | Calls | Unique call shapes |",
        "| --- | --- | --- | --- |",
    ]
    for s in data["gold_sets"]:
        lines.append(
            f"| {s['name']} | {s['description']} | {s['calls']} | {s['shapes']} |"
        )
    for s in data["gold_sets"]:
        lines += _md_gold_set(s, names, small)
    lines += _md_injection(data, names, small)
    lines += [
        "",
        "## Known limitations",
        "",
        "Weaknesses v0 ships with, written up with their evidence:",
        "",
    ]
    lines += [f"- {_md_link(x['see'], x['title'])}" for x in data["known_limitations"]]
    lines += ["", "## Method", ""]
    for note in data["method"]:
        see = ", ".join(_md_link(p) for p in note["see"])
        lines.append(f"- {note['text']}" + (f" See {see}." if see else ""))
    return "\n".join(lines) + "\n"


def _md_gold_set(s: dict[str, Any], names: dict[str, str], small: int) -> list[str]:
    lines = [
        "",
        f"## {s['name']}",
        "",
        (
            f"{s['calls']} calls, {s['shapes']} unique call shapes. Precision and recall"
            " are of `yes` for yes/no labels and of `secrets` for data_class."
        ),
        "",
        (
            "| Label | Labeler | n | Accuracy (95% CI) | Shapes | Accuracy per shape"
            " | ECE | Precision | Recall | Not labeled |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in s["labels"]:
        for backend, m in entry["models"].items():
            label = f"`{entry['label']}`"
            if m["status"] == "not_reported":
                lines.append(
                    f"| {label} | {names[backend]} | — | not reported"
                    f" ({_md_link(m['see'], 'why')}) | — | — | — | — | — | — |"
                )
                continue
            flag = DAGGER if m["n"] < small else ""
            shape_flag = DAGGER if m["shapes"] < small else ""
            lines.append(
                f"| {label} | {names[backend]} | {m['n']}{flag}"
                f" | {_md_accuracy(m)} | {m['shapes']}{shape_flag}"
                f" | {_pct(m['shape_accuracy'])}{shape_flag} | {_dec(m['ece'])}"
                f" | {_md_ratio(m.get('precision'), small, 'none answered')}"
                f" | {_md_ratio(m.get('recall'), small, 'none in gold')}"
                f" | {m['not_labeled']} |"
            )
    lines += [
        "",
        "### Set by rule",
        "",
        "Written by code, not by a labeler, and not counted in the table above.",
        "",
        "| Label | n | Agrees with gold |",
        "| --- | --- | --- |",
    ]
    for r in s["set_by_rule"]:
        flag = DAGGER if r["n"] < small else ""
        lines.append(
            f"| `{r['label']}` | {r['n']}{flag} | {r['agrees_with_gold']} of {r['n']} |"
        )
    return lines


def _md_injection(data: dict[str, Any], names: dict[str, str], small: int) -> list:
    set_names = {s["id"]: s["name"] for s in data["gold_sets"]}
    # A labeler that doesn't report injection gets no column, only the note below.
    backends = [m["id"] for m in data["models"] if INJECTION not in m["not_reported"]]
    lines = [
        "",
        "## Injection by call shape",
        "",
        (
            "Gold answers for `sec.injection` and how many calls each labeler that"
            " reports it answered `yes`, by call shape. The overall accuracy hides which kinds of content a"
            " labeler gets right."
        ),
        "",
        "| Gold set | Call shape | Gold | n | "
        + " | ".join(f"{names[b]} `yes`" for b in backends)
        + " |",
        "| --- | --- | --- | --- | " + " | ".join("---" for _ in backends) + " |",
    ]
    for r in data["injection_by_shape"]:
        cells = []
        for b in backends:
            m = r["models"][b]
            if m["status"] == "not_reported":
                cells.append("not reported")
            elif m["labeled"] == r["n"]:
                cells.append(str(m["yes"]))
            else:
                cells.append(f"{m['yes']} of {m['labeled']} labeled")
        flag = DAGGER if r["n"] < small else ""
        lines.append(
            f"| {set_names[r['set']]} | {r['shape']} | `{r['gold']}` | {r['n']}{flag}"
            f" | " + " | ".join(cells) + " |"
        )
    not_reported = [m for m in data["models"] if INJECTION in m["not_reported"]]
    if not_reported:
        who = " and ".join(m["name"] for m in not_reported)
        lines += [
            "",
            (
                f"{who} doesn't report `sec.injection` in v0;"
                f" {_md_link(NOT_REPORTED_SEE, 'known limitations')} has its numbers"
                " from before that decision."
            ),
        ]
    return lines


def _md_accuracy(m: dict[str, Any]) -> str:
    if m["accuracy"] is None:
        return "—"
    lo, hi = m["ci95"]
    return f"{_pct(m['accuracy'])} ({_pct(lo, '')}–{_pct(hi)})"


def _md_ratio(r: dict[str, int] | None, small: int, empty: str = "") -> str:
    if r is None:
        return "—"
    if not r["of"]:
        return f"— ({empty})"
    flag = DAGGER if r["of"] < small else ""
    return f"{r['hits']}/{r['of']} ({_pct(r['hits'] / r['of'])}){flag}"


def _md_link(path: str, text: str | None = None) -> str:
    name = path.split("#", 1)[0].rsplit("/", 1)[-1]
    return f"[{text or name}]({_md_href(path)})"


def _md_href(path: str) -> str:
    if path.startswith("docs/"):
        return path.removeprefix("docs/")
    return MD_LINK_BASE + path


def _pct(value: float | None, sign: str = "%") -> str:
    """Formatted like eval/score.py's report, so the two agree to the digit."""
    if value is None:
        return "—"
    text = f"{value:.1%}"
    return text if sign else text.removesuffix("%")


def _dec(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


# --- CLI ----------------------------------------------------------------------


def stale_inputs(data: dict[str, Any]) -> list[str]:
    """Inputs present locally whose hash differs from the recorded one."""
    stale = []
    for item in data["provenance"]["inputs"]:
        path = Path(item["path"])
        if path.exists() and _sha256(path) != item["sha256"]:
            stale.append(item["path"])
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=GENERATOR)
    parser.add_argument("--check", action="store_true", help="fail if out of date")
    args = parser.parse_args(argv)
    if args.check:
        data = json.loads(JSON_OUTPUT.read_text(encoding="utf-8"))
        ok = True
        if MD_OUTPUT.read_text(encoding="utf-8") != render_markdown(data):
            print(f"{MD_OUTPUT} doesn't match {JSON_OUTPUT}", file=sys.stderr)
            ok = False
        stale = stale_inputs(data)
        if stale:
            print(
                f"{JSON_OUTPUT} is stale; these inputs changed: {', '.join(stale)}",
                file=sys.stderr,
            )
            ok = False
        return 0 if ok else 1
    data = build()
    JSON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUTPUT.write_text(render_json(data), encoding="utf-8")
    MD_OUTPUT.write_text(render_markdown(data), encoding="utf-8")
    print(f"wrote {JSON_OUTPUT} and {MD_OUTPUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
