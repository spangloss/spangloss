"""Score stored labels against hand-labeled gold.

For each model in the label store (derived labels count as their own model), per
label: accuracy, precision and recall for each answer, a confusion table, and
expected calibration error. Results are also broken down by basis.

- Labels are compared at the sec.* level; for data_class that is the final answer.
- A label whose taxonomy differs from its gold record's is skipped and counted.
- Gold labels a model never produced are counted as missing, not scored. Derived
  labels cover only what the parser decides, so they have no missing count.
- A label the model's backend does not answer (backends/support.py) is reported
  as unsupported: any stored rows for it are ignored and nothing counts as missing.
- p is the probability of the chosen answer (TAXONOMY.md Labeling rule 6), so
  calibration compares it with whether that answer was right. ECE uses ECE_BINS
  equal-width bins; with few calls it is only indicative.
- Accuracy is also reported per unique call shape (eval/shapes.py): each shape
  counts once, with the share of its records answered correctly, so a call repeated
  across scenario runs doesn't outweigh the rest. Calls with no known shape count
  as their own shape.
- Accuracy carries a 95% Wilson score interval, which stays inside [0, 1] and is
  sensible at small n and at 0% or 100%, unlike the normal approximation.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from math import sqrt
from typing import Any

from spangloss.backends.support import unsupported_labels
from spangloss.questions.cards import Card
from spangloss.questions.derived import DERIVED_MODEL

ECE_BINS = 10
WILSON_Z = 1.96  # 95%


@dataclass(frozen=True, slots=True)
class StoredLabel:
    span_id: str
    label: str
    answer: str
    p: float
    model: str
    taxonomy: str


@dataclass(frozen=True, slots=True)
class Pair:
    gold: str
    predicted: str
    p: float
    basis: str
    shape: str = ""  # call shape; "" means unknown, scored as its own shape


@dataclass(frozen=True, slots=True)
class AnswerScore:
    answer: str
    precision: float | None  # None when the model never gave this answer
    recall: float | None  # None when gold never has this answer
    gold_count: int
    predicted_count: int


@dataclass(frozen=True, slots=True)
class LabelScore:
    label: str
    n: int
    accuracy: float | None  # None when nothing was scored
    ci: tuple[float, float] | None  # 95% Wilson interval for accuracy
    shapes: int  # unique call shapes among the scored records
    shape_accuracy: float | None  # mean over shapes of each shape's accuracy
    answers: list[AnswerScore]
    confusion: dict[tuple[str, str], int]  # (gold, predicted) -> count
    ece: float | None
    by_basis: dict[str, tuple[int, int]]  # basis -> (n, correct)
    missing: int | None  # gold labels with no model label; None for derived
    unsupported: bool = False  # the backend does not answer this label


@dataclass(slots=True)
class ModelScore:
    model: str
    labels: list[LabelScore] = field(default_factory=list)
    skipped_taxonomy: int = 0


def score(
    gold: list[dict[str, Any]],
    stored: Iterable[StoredLabel],
    cards: list[Card],
    shapes: dict[str, str] | None = None,
) -> list[ModelScore]:
    """shapes maps span_id to call shape; spans without one are their own shape."""
    shapes = shapes or {}
    gold_by_span = {r["span_id"]: r for r in gold}
    by_model: dict[str, list[StoredLabel]] = defaultdict(list)
    for x in stored:
        by_model[x.model].append(x)

    results = []
    for model in sorted(by_model, key=lambda m: (m == DERIVED_MODEL, m)):
        result = ModelScore(model)
        unsupported = unsupported_labels(model)
        pairs: dict[str, list[Pair]] = defaultdict(list)
        seen: set[tuple[str, str]] = set()  # scored or skipped
        for x in by_model[model]:
            if x.label in unsupported:
                continue
            record = gold_by_span.get(x.span_id)
            if record is None or x.label not in record["labels"]:
                continue
            seen.add((x.span_id, x.label))
            if x.taxonomy != record["taxonomy"]:
                result.skipped_taxonomy += 1
                continue
            pairs[x.label].append(
                Pair(
                    record["labels"][x.label],
                    x.answer,
                    x.p,
                    record["basis"],
                    shapes.get(x.span_id) or f"span:{x.span_id}",
                )
            )
        # A real model is scored on every gold label; derived labels only on theirs.
        wanted = set(pairs)
        if model != DERIVED_MODEL:
            wanted |= {label for r in gold for label in r["labels"]}
        for label in _label_order(cards, wanted):
            if label in unsupported:
                result.labels.append(
                    replace(_score_label(label, [], [], None), unsupported=True)
                )
                continue
            missing = None
            if model != DERIVED_MODEL:
                missing = sum(
                    1
                    for r in gold
                    if label in r["labels"] and (r["span_id"], label) not in seen
                )
            result.labels.append(
                _score_label(label, pairs[label], _answer_order(cards, label), missing)
            )
        results.append(result)
    return results


def expected_calibration_error(pairs: list[Pair], bins: int = ECE_BINS) -> float:
    buckets: dict[int, list[Pair]] = defaultdict(list)
    for pair in pairs:
        buckets[min(int(pair.p * bins), bins - 1)].append(pair)
    total = len(pairs)
    ece = 0.0
    for bucket in buckets.values():
        accuracy = sum(p.gold == p.predicted for p in bucket) / len(bucket)
        confidence = sum(p.p for p in bucket) / len(bucket)
        ece += abs(accuracy - confidence) * len(bucket) / total
    return ece


def wilson_interval(
    correct: int, n: int, z: float = WILSON_Z
) -> tuple[float, float] | None:
    if n == 0:
        return None
    phat = correct / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def format_report(results: list[ModelScore], gold_calls: int) -> str:
    lines = [
        (
            f"Gold: {gold_calls} calls. ECE uses {ECE_BINS} equal-width bins; with"
            " this few calls it is only indicative. Accuracy intervals are 95% Wilson."
            " acc/shape counts each unique call shape (tool + normalized arguments)"
            " once."
        ),
    ]
    for result in results:
        lines += ["", f"=== {result.model} ==="]
        if result.skipped_taxonomy:
            lines.append(
                f"skipped {result.skipped_taxonomy} label(s): taxonomy differs from gold"
            )
        lines.append(
            f"{'label':<30} {'n':>3} {'acc':>6} {'95% CI':>15} {'shapes':>6}"
            f" {'acc/shape':>9} {'ECE':>6} {'missing':>8}"
        )
        for s in result.labels:
            if s.unsupported:
                lines.append(f"{s.label:<30} unsupported by this backend")
                continue
            missing = "-" if s.missing is None else str(s.missing)
            ece = f"{'-':>6}" if s.ece is None else f"{s.ece:>6.3f}"
            lines.append(
                f"{s.label:<30} {s.n:>3} {_pct(s.accuracy)} {_ci(s.ci):>15}"
                f" {s.shapes:>6} {_pct(s.shape_accuracy):>9} {ece} {missing:>8}"
            )
        bases = sorted({b for s in result.labels for b in s.by_basis})
        if bases:
            lines += ["", "accuracy by basis (correct/n)"]
            lines.append(f"{'label':<30} " + " ".join(f"{b:>10}" for b in bases))
            for s in (s for s in result.labels if not s.unsupported):
                cells = []
                for b in bases:
                    n, correct = s.by_basis.get(b, (0, 0))
                    cells.append(f"{f'{correct}/{n}' if n else '-':>10}")
                lines.append(f"{s.label:<30} " + " ".join(cells))
        for s in result.labels:
            if not s.n:
                continue
            lines += [
                "",
                (
                    f"{s.label}: n={s.n}, accuracy {_pct(s.accuracy).strip()}"
                    f" (95% CI {_ci(s.ci)})"
                ),
            ]
            lines.append(
                f"  {'answer':<16} {'gold':>5} {'pred':>5} {'prec':>6} {'recall':>6}"
            )
            for a in s.answers:
                lines.append(
                    f"  {a.answer:<16} {a.gold_count:>5} {a.predicted_count:>5}"
                    f" {_pct(a.precision)} {_pct(a.recall)}"
                )
            names = [a.answer for a in s.answers]
            width = max(8, *(len(n) for n in names)) + 1
            lines.append(
                f"  {'gold \\ pred':<{width}}" + "".join(f"{n:>{width}}" for n in names)
            )
            for g in names:
                lines.append(
                    f"  {g:<{width}}"
                    + "".join(
                        f"{s.confusion.get((g, p), 0) or '.':>{width}}" for p in names
                    )
                )
    return "\n".join(lines)


def _score_label(
    label: str, pairs: list[Pair], order: list[str], missing: int | None
) -> LabelScore:
    confusion = Counter((p.gold, p.predicted) for p in pairs)
    gold_counts = Counter(p.gold for p in pairs)
    predicted_counts = Counter(p.predicted for p in pairs)
    seen = set(gold_counts) | set(predicted_counts)
    names = [a for a in order if a in seen] + sorted(seen - set(order))
    answers = [
        AnswerScore(
            answer=a,
            precision=confusion[(a, a)] / predicted_counts[a]
            if predicted_counts[a]
            else None,
            recall=confusion[(a, a)] / gold_counts[a] if gold_counts[a] else None,
            gold_count=gold_counts[a],
            predicted_count=predicted_counts[a],
        )
        for a in names
    ]
    by_basis: dict[str, tuple[int, int]] = {}
    for basis in sorted({p.basis for p in pairs}):
        subset = [p for p in pairs if p.basis == basis]
        by_basis[basis] = (len(subset), sum(p.gold == p.predicted for p in subset))
    correct = sum(p.gold == p.predicted for p in pairs)
    by_shape: dict[str, list[bool]] = defaultdict(list)
    for p in pairs:
        by_shape[p.shape].append(p.gold == p.predicted)
    return LabelScore(
        label=label,
        n=len(pairs),
        accuracy=correct / len(pairs) if pairs else None,
        ci=wilson_interval(correct, len(pairs)),
        shapes=len(by_shape),
        shape_accuracy=(
            sum(sum(v) / len(v) for v in by_shape.values()) / len(by_shape)
            if by_shape
            else None
        ),
        answers=answers,
        confusion=dict(confusion),
        ece=expected_calibration_error(pairs) if pairs else None,
        by_basis=by_basis,
        missing=missing,
    )


def _label_order(cards: list[Card], labels: set[str]) -> list[str]:
    order = list(dict.fromkeys(c.label for c in cards))
    return [label for label in order if label in labels] + sorted(labels - set(order))


def _answer_order(cards: list[Card], label: str) -> list[str]:
    """The card's answers in card order; a two-step label starts with none."""
    group = [c for c in cards if c.label == label]
    if len(group) == 1:
        return list(group[0].answers)
    step2 = next(c for c in group if c.step == 2)
    return ["none", *step2.answers]


def _ci(ci: tuple[float, float] | None) -> str:
    return "-" if ci is None else f"{ci[0]:.1%}–{ci[1]:.1%}"


def _pct(value: float | None) -> str:
    return f"{'-':>6}" if value is None else f"{value:>6.1%}"
