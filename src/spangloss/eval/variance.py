"""Label variance: how often a backend's answers change when the same calls are
labeled again, pass after pass, each pass in its own store.

Per gold set, backend and label, over the (call, label) pairs answered in every
pass (derived labels are excluded; they come from code, not a model):

- answer changed: the stored answer is not the same in all passes;
- across 0.5 (choice labels only): the winning answer's p is above 0.5 in some
  passes and at or below it in others. For a yes/no label this is the same event
  as an answer change, so it isn't reported separately;
- largest p swing: the largest max - min of p across passes, over the pairs. For
  a yes/no label p is p(yes); for a choice label it is the p of each pass's
  winning answer, which may be a different answer in each pass.

Pairs answered in some passes but not all (a skipped call, say) are counted, not
scored.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from spangloss.eval.score import StoredLabel
from spangloss.questions.cards import Card
from spangloss.questions.derived import DERIVED_MODEL

THRESHOLD = 0.5


@dataclass(frozen=True, slots=True)
class LabelVariance:
    label: str
    yes_no: bool
    pairs: int  # (call, label) pairs answered in every pass
    changed: int
    crossed: int | None  # None for yes/no labels
    max_swing: float | None  # None when there are no pairs
    partial: int  # pairs answered in some passes but not all


def yes_no_labels(cards: list[Card]) -> frozenset[str]:
    """Labels answered yes or no. A two-step label (data_class) stores its second
    step's answer, so it counts as a choice label."""
    two_step = {c.label for c in cards if c.step == 2}
    return frozenset(
        c.label for c in cards if c.kind == "noul" and c.label not in two_step
    )


def variance(
    passes: Sequence[Sequence[StoredLabel]],
    model: str,
    span_ids: set[str],
    cards: list[Card],
) -> list[LabelVariance]:
    """One model's variance over the given spans, labels in card order."""
    yes_no = yes_no_labels(cards)
    by_pair: dict[tuple[str, str], list[StoredLabel]] = defaultdict(list)
    for labels in passes:
        for x in labels:
            if x.model == model and x.model != DERIVED_MODEL and x.span_id in span_ids:
                by_pair[(x.span_id, x.label)].append(x)
    order = list(dict.fromkeys(c.label for c in cards))
    results = []
    for label in sorted(
        {lab for _, lab in by_pair},
        key=lambda lab: order.index(lab) if lab in order else len(order),
    ):
        groups = [g for (_, lab), g in by_pair.items() if lab == label]
        full = [g for g in groups if len(g) == len(passes)]
        is_yes_no = label in yes_no
        changed = sum(len({x.answer for x in g}) > 1 for g in full)
        if is_yes_no:
            ps = [[x.p if x.answer == "yes" else 1.0 - x.p for x in g] for g in full]
            crossed = None
        else:
            ps = [[x.p for x in g] for g in full]
            crossed = sum(len({p > THRESHOLD for p in group}) > 1 for group in ps)
        results.append(
            LabelVariance(
                label=label,
                yes_no=is_yes_no,
                pairs=len(full),
                changed=changed,
                crossed=crossed,
                max_swing=max((max(p) - min(p) for p in ps), default=None),
                partial=len(groups) - len(full),
            )
        )
    return results


def format_variance(
    title: str, rows: list[LabelVariance], checkpoints: Sequence[str | None]
) -> str:
    lines = [title]
    named = [c or "not recorded" for c in checkpoints]
    same = len(set(named)) == 1
    lines.append(
        f"  checkpoint: {named[0]} in all {len(named)} passes"
        if same
        else f"  warning: passes used different checkpoints: {', '.join(named)}"
    )
    lines.append(
        f"  {'label':<30} {'pairs':>5}  {'answer changed':>16}  {'across 0.5':>14}"
        f"  {'largest p swing':>15}  {'not in all passes':>17}"
    )
    for r in rows:
        crossed = "= changed" if r.crossed is None else _rate(r.crossed, r.pairs)
        swing = "—" if r.max_swing is None else f"{r.max_swing:.3f}"
        lines.append(
            f"  {r.label:<30} {r.pairs:>5}  {_rate(r.changed, r.pairs):>16}"
            f"  {crossed:>14}  {swing:>15}  {r.partial:>17}"
        )
    return "\n".join(lines)


def _rate(n: int, of: int) -> str:
    return f"{n} ({n / of:.1%})" if of else "—"
