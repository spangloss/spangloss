"""Restamp gold records to a new taxonomy version, only where no change can affect them.

Gold answers are the owner's. A taxonomy change can make an old answer wrong, or
make a question asked or unasked, so a record is restamped only when every change
between its version and the target provably leaves it alone. Two checks:

1. Changelog checks: for each change in TAXONOMY.md's changelog entry for the
   target version, a rule that says which records it could affect.
2. A gating re-check: the record's answers must still be exactly the questions the
   current cards ask for its parsed call (the same blank rules import-sheet uses).

Restamped records keep their answers and provenance and gain restamped_from and
restamp_reason. Affected records are left on their old version, listed for review,
and skipped by score as a version mismatch until they are relabeled by hand.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spangloss.eval.sheet import check_blanks, read_gold
from spangloss.parse.record import ParsedToolCall
from spangloss.questions.cards import Card
from spangloss.questions.incoming import RETURN_PATH_ARGS
from spangloss.rules.subagent_taint import SPAWN_TOOLS


@dataclass(frozen=True, slots=True)
class Change:
    """One changelog item and which gold records it could affect."""

    summary: str
    affects: Callable[[dict[str, Any]], bool]


# TAXONOMY.md changelog entries, keyed by (from, to). A record more than one
# version behind is checked against every step on the way.
CHANGES: dict[tuple[str, str], list[Change]] = {
    ("0.3.0", "0.3.1"): [
        Change(
            "sec.injection is gated on incoming content: a return-path tool's"
            " content-carrying argument is now asked",
            lambda r: r["tool_name"] in RETURN_PATH_ARGS,
        ),
        Change(
            "sec.injection question reworded to 'content this call brings into the"
            " agent's context'; for other tools that content is still the output",
            lambda r: r["tool_name"] in RETURN_PATH_ARGS,
        ),
        Change(
            "subagent taint anchored on the spawn; changes findings, not labels",
            lambda r: False,
        ),
        Change(
            "capture note on headless handbacks; no label definitions change",
            lambda r: False,
        ),
    ],
    ("0.3.1", "0.3.2"): [
        Change(
            "a subagent spawn is no for all three trifecta questions; an Agent"
            " record is affected only if some trifecta answer isn't already no",
            lambda r: (
                r["tool_name"] in SPAWN_TOOLS
                and any(
                    v != "no"
                    for k, v in r["answers"].items()
                    if k.startswith("trifecta.")
                )
            ),
        ),
        Change(
            "output that only reports instructions it ignored is injection no;"
            " affects injection yes on tools whose output a model writes",
            lambda r: (
                r["answers"].get("injection") == "yes"
                and r["tool_name"] in MODEL_WRITTEN_OUTPUT
            ),
        ),
    ],
}

# Tools whose captured output is written by a model (a summary or a report), so it
# can be a report of ignored instructions. Raw output, such as curl's, can't be.
MODEL_WRITTEN_OUTPUT = frozenset({"WebFetch", "Agent", "SubagentHandback"})


def changes_between(source: str, to: str) -> list[Change] | None:
    """Every change from source to to, following the changelog steps; None if no
    chain of steps connects them."""
    steps = dict(CHANGES)
    changes: list[Change] = []
    version = source
    while version != to:
        nxt = next((b for (a, b) in steps if a == version), None)
        if nxt is None:
            return None
        changes += steps[(version, nxt)]
        version = nxt
    return changes


@dataclass(frozen=True, slots=True)
class Affected:
    span_id: str
    tool_name: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RestampResult:
    restamped: int
    already: int  # records already on the target version
    affected: list[Affected]
    other_versions: int  # records on a version with no restamp rules to the target


def restamp(
    gold: Path, calls: list[ParsedToolCall], cards: list[Card], to: str
) -> RestampResult:
    if cards[0].taxonomy != to:
        raise ValueError(f"cards are at {cards[0].taxonomy}, not {to}")
    by_id = {c.span_id: c for c in calls}
    records = read_gold(gold)
    out: list[dict[str, Any]] = []
    restamped = already = other = 0
    affected: list[Affected] = []
    for record in records:
        source = record["taxonomy"]
        if source == to:
            already += 1
            out.append(record)
            continue
        changes = changes_between(source, to)
        if changes is None:
            other += 1
            out.append(record)
            continue
        reasons = [c.summary for c in changes if c.affects(record)]
        call = by_id.get(record["span_id"])
        if call is None:
            reasons.append("not in the parsed calls, so gating can't be rechecked")
        else:
            reasons += check_blanks(record["span_id"], record["answers"], cards, call)
        if reasons:
            affected.append(
                Affected(record["span_id"], record["tool_name"], tuple(reasons))
            )
            out.append(record)
            continue
        restamped += 1
        out.append(
            record
            | {
                "taxonomy": to,
                "restamped_from": source,
                "restamp_reason": (
                    f"none of the {len(changes)} changelog checks from {source} to"
                    f" {to} applies, and the answers match the questions the {to}"
                    " cards ask for this call"
                ),
            }
        )

    tmp = gold.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for record in out:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp.replace(gold)
    return RestampResult(restamped, already, affected, other)
