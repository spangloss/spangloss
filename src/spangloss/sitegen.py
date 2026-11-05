"""Generate site/labels.json from TAXONOMY.md.

    uv run python -m spangloss.sitegen          # write site/labels.json
    uv run python -m spangloss.sitegen --check  # fail if it is out of date

Everything except `asks_about` comes from TAXONOMY.md or from code:

- name, answers: each label's section under "## Labels" (its answer table; the
  trifecta table for the trifecta sub-labels, which are yes/no).
- min_basis, asked_when: the gating table under "Input basis and question gating".
- source: "model" when only the labeler answers, "code" when only code does, "both"
  when code decides some calls (questions/derived.py) and the labeler the rest.
- backends: which backends report the label (backends/support.py).

`asks_about` is a one-line summary that TAXONOMY.md doesn't hold. The proposals
below are not the owner's wording until approved; the JSON marks their status.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from spangloss.backends.support import BACKENDS, unsupported_labels
from spangloss.questions.derived import DERIVED_LABELS

TAXONOMY = Path("TAXONOMY.md")
OUTPUT = Path("site/labels.json")
TRIFECTA = "sec.trifecta"
OWNER_TO_WRITE = "[owner to write]"
MAX_SUMMARY_WORDS = 12

# Proposed, not approved (status "proposed" in the JSON).
ASKS_ABOUT: dict[str, str] = {
    "sec.effect": "What the call does to the systems it touches",
    "sec.reversibility": "Whether the call's effects could be undone if it was a mistake",
    "sec.data_class": "Whether the call touches sensitive data, and which kind",
    TRIFECTA: OWNER_TO_WRITE,
    "sec.trifecta.private_source": "Whether the call accesses data that isn't public",
    "sec.trifecta.untrusted_source": (
        "Whether the call brings in content from outside the user's control"
    ),
    "sec.trifecta.sink": (
        "Whether the call sends data off the machine, to an unconfirmed destination"
    ),
    "sec.intent": "What the call is for, as a category",
    "sec.injection": (
        "Whether incoming content tries to steer the agent beyond its purpose"
    ),
}
ASKS_ABOUT_STATUS = "proposed"

BASES = ("name_only", "args", "content")


class TaxonomyError(ValueError):
    pass


def generate(text: str) -> dict[str, Any]:
    version = _version(text)
    gating = _gating(text)
    labels = []
    for name, body in _label_sections(text):
        if name == f"{TRIFECTA}.*":
            labels.append(_trifecta(body, gating))
        else:
            if name not in gating:
                raise TaxonomyError(f"{name}: not in the gating table")
            labels.append(_entry(name, _answers(name, body), gating[name]))
    return {
        "taxonomy": version,
        "generated_from": "TAXONOMY.md",
        "generator": "python -m spangloss.sitegen",
        "asks_about_status": ASKS_ABOUT_STATUS,
        "labels": labels,
    }


def render(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _entry(name: str, answers: list[str], gate: tuple[str, str]) -> dict[str, Any]:
    min_basis, asked_when = gate
    return {
        "name": name,
        "asks_about": _summary(name),
        "answers": answers,
        "min_basis": min_basis,
        "asked_when": asked_when,
        "source": "both" if name in DERIVED_LABELS else "model",
        "backends": {
            "reported_by": [b for b in BACKENDS if name not in unsupported_labels(b)],
            "not_reported_by": [b for b in BACKENDS if name in unsupported_labels(b)],
        },
    }


def _trifecta(body: str, gating: dict[str, tuple[str, str]]) -> dict[str, Any]:
    if "yes/no questions" not in body:
        raise TaxonomyError("trifecta: expected independent yes/no questions")
    gate = gating[f"{TRIFECTA}.*"]
    names = re.findall(r"^\| `(sec\.trifecta\.[a-z_]+)` \|", body, re.MULTILINE)
    if not names:
        raise TaxonomyError("trifecta: no sub-labels in its table")
    min_basis, asked_when = gate
    return {
        "name": TRIFECTA,
        "asks_about": _summary(TRIFECTA),
        "min_basis": min_basis,
        "asked_when": asked_when,
        "sub_labels": [_entry(n, ["yes", "no"], gate) for n in names],
    }


def _summary(name: str) -> str:
    try:
        summary = ASKS_ABOUT[name]
    except KeyError:
        raise TaxonomyError(f"{name}: no asks_about summary") from None
    if summary != OWNER_TO_WRITE and len(summary.split()) > MAX_SUMMARY_WORDS:
        raise TaxonomyError(f"{name}: asks_about is over {MAX_SUMMARY_WORDS} words")
    return summary


def _version(text: str) -> str:
    m = re.search(r"^\*\*Version:\*\* (\d+\.\d+\.\d+)", text, re.MULTILINE)
    if not m:
        raise TaxonomyError("no **Version:** line")
    return m.group(1)


def _gating(text: str) -> dict[str, tuple[str, str]]:
    """Label -> (min_basis, the "Asked when" text) from the gating table."""
    section = _between(text, "| Label | Asked when |", "\n\n")
    rows = re.findall(r"^\| `(sec\.[a-z_.*]+)` \| (.+?) \|$", section, re.MULTILINE)
    if not rows:
        raise TaxonomyError("no gating table")
    gating = {}
    for label, asked_when in rows:
        named = [b for b in BASES if f"`{b}`" in asked_when]
        if named:
            min_basis = named[0]
        elif "at any basis" in asked_when:
            min_basis = BASES[0]
        else:
            raise TaxonomyError(f"{label}: can't read a basis from {asked_when!r}")
        gating[label] = (min_basis, asked_when.replace("`", ""))
    return gating


def _label_sections(text: str) -> list[tuple[str, str]]:
    section = _between(text, "## Labels", "\n## ")
    parts = re.split(
        r"^### `(sec\.[a-z_.*]+)` — tier \d+\n", section, flags=re.MULTILINE
    )
    return list(zip(parts[1::2], parts[2::2], strict=True))


def _answers(name: str, body: str) -> list[str]:
    table = body.split("| Answer |", 1)
    if len(table) != 2:
        raise TaxonomyError(f"{name}: no answer table")
    answers = []
    for line in table[1].splitlines()[2:]:
        m = re.match(r"^\| `([a-z_]+)` \|", line)
        if not m:
            break
        answers.append(m.group(1))
    if not answers:
        raise TaxonomyError(f"{name}: empty answer table")
    return answers


def _between(text: str, start: str, end: str) -> str:
    i = text.index(start) + len(start)
    j = text.find(end, i)
    return text[i : j if j != -1 else len(text)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m spangloss.sitegen")
    parser.add_argument("--check", action="store_true", help="fail if out of date")
    args = parser.parse_args(argv)
    expected = render(generate(TAXONOMY.read_text(encoding="utf-8")))
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
            print(
                f"{OUTPUT} is out of date; run python -m spangloss.sitegen",
                file=sys.stderr,
            )
            return 1
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(expected, encoding="utf-8")
    print(f"wrote {OUTPUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
