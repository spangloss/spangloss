"""Load and validate the question cards in questions/cards/.

The cards are the owner's question wording and answer sets; this module only
reads them and checks their shape. TAXONOMY.md is the authority on content.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from spangloss.parse.record import Basis

CARDS_DIR = Path(__file__).parent / "cards"
BASIS_ORDER: dict[str, int] = {"name_only": 0, "args": 1, "content": 2}
EVIDENCE_FIELDS = frozenset({"tool_name", "args", "output"})
REQUIRED = ("label", "taxonomy", "min_basis", "question", "answers", "evidence")


class CardError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Card:
    name: str  # file stem, e.g. "trifecta.sink"; used as the question id, never shown to the model
    label: str  # e.g. "sec.trifecta.sink"
    taxonomy: str
    min_basis: Basis
    question: str
    answers: dict[str, str]
    notes: tuple[str, ...]
    evidence: tuple[str, ...]
    step: int | None = None
    asked_if: str | None = None

    @property
    def kind(self) -> Literal["noul", "choice"]:
        return "noul" if set(self.answers) == {"yes", "no"} else "choice"

    def applies_to(self, basis: Basis) -> bool:
        return BASIS_ORDER[basis] >= BASIS_ORDER[self.min_basis]


def load_cards(directory: Path = CARDS_DIR) -> list[Card]:
    cards = [_load(path) for path in sorted(directory.glob("*.yaml"))]
    if not cards:
        raise CardError(f"no cards in {directory}")
    _check_set(cards)
    return cards


def _load(path: Path) -> Card:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise CardError(f"{path.name}: not valid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise CardError(f"{path.name}: expected a mapping")
    missing = [k for k in REQUIRED if k not in raw]
    if missing:
        raise CardError(f"{path.name}: missing {', '.join(missing)}")

    answers = raw["answers"]
    if not isinstance(answers, dict) or len(answers) < 2:
        raise CardError(f"{path.name}: answers must map at least two answers")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items()):
        raise CardError(f"{path.name}: answers must map strings to strings")
    if raw["min_basis"] not in BASIS_ORDER:
        raise CardError(f"{path.name}: unknown min_basis {raw['min_basis']!r}")
    evidence = raw["evidence"]
    if not isinstance(evidence, list) or not set(evidence) <= EVIDENCE_FIELDS:
        raise CardError(
            f"{path.name}: evidence must be a subset of {sorted(EVIDENCE_FIELDS)}"
        )
    if "tool_name" not in evidence:
        raise CardError(f"{path.name}: evidence must include tool_name")
    notes = raw.get("notes") or []
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        raise CardError(f"{path.name}: notes must be a list of strings")

    return Card(
        name=path.stem,
        label=_str(raw, "label", path),
        taxonomy=str(raw["taxonomy"]),
        min_basis=raw["min_basis"],
        question=_str(raw, "question", path),
        answers=dict(answers),
        notes=tuple(notes),
        evidence=tuple(evidence),
        step=raw.get("step"),
        asked_if=raw.get("asked_if"),
    )


def _str(raw: dict[str, Any], key: str, path: Path) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise CardError(f"{path.name}: {key} must be a non-empty string")
    return value


def _check_set(cards: list[Card]) -> None:
    versions = {c.taxonomy for c in cards}
    if len(versions) != 1:
        raise CardError(f"cards disagree on taxonomy version: {sorted(versions)}")
    names = [c.name for c in cards]
    if len(names) != len(set(names)):
        raise CardError("duplicate card names")
    for label in {c.label for c in cards}:
        group = [c for c in cards if c.label == label]
        if len(group) == 1:
            if group[0].step is not None:
                raise CardError(f"{group[0].name}: step set on a single-card label")
            continue
        # A two-step label: step 1 is yes/no; step 2 is asked only when step 1 is yes.
        steps = sorted(group, key=lambda c: c.step or 0)
        if [c.step for c in steps] != [1, 2]:
            raise CardError(f"{label}: multi-card labels need exactly steps 1 and 2")
        first, second = steps
        if first.kind != "noul":
            raise CardError(f"{first.name}: step 1 must be a yes/no question")
        if second.asked_if != f"{first.name} == yes":
            raise CardError(f"{second.name}: asked_if must be '{first.name} == yes'")
