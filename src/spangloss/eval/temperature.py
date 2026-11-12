"""Fit a temperature for a served model through the labeling path.

A fine-tune's own temperature is fitted where it was trained (CUDA, fp32 logits).
Local serving (MLX, bf16) gives different probabilities, so a temperature fitted there
can make local calibration worse. This fit runs the calibration partition's calls
through label_call against the live server instead: the evidence, question specs and
output chunks are what labeling sends, and the probabilities are what the serving
path returns.

- Gold comes from the partition's records (System One shape: state plus questions,
  each with its label). A captured request whose evidence equals a record's state is
  scored on that record's questions; requests matching no record are ignored and
  counted. The records already carry the chunk rule's per-chunk injection labels.
- Scaling: p_T(k) is proportional to p(k)^(1/T), the same as dividing the logits by T.
  T minimizes the mean negative log-likelihood of the gold answer, searched on a log
  grid and refined by golden-section search inside TEMPERATURE_RANGE.
- ECE is eval/score.py's: the probability of the chosen answer, ECE_BINS equal-width
  bins, reported at T = 1 and at the fitted T. Accuracy doesn't depend on T.
- Nothing is written to the label store and no temperature is applied anywhere; the
  report is for deciding what to serve with.
"""

import json
import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from spangloss.backends.base import AskResult, Backend, ModelInfo, QuestionSpec
from spangloss.eval.score import ECE_BINS, Pair, expected_calibration_error
from spangloss.parse.record import ParsedToolCall
from spangloss.questions import label_call, load_cards
from spangloss.questions.runner import DEFAULT_MAX_EVIDENCE_CHARS

TEMPERATURE_RANGE = (0.25, 4.0)
GRID_POINTS = 41
EPSILON = 1e-12  # floor for probabilities before taking logs


@dataclass(frozen=True, slots=True)
class Row:
    """One answered question from one request, with its gold answer."""

    question: str
    distribution: dict[str, float]
    gold: str


@dataclass(frozen=True, slots=True)
class FitReport:
    temperature: float
    rows: int
    calls: int
    requests: int
    unmatched_requests: int
    nll: tuple[float, float]  # at T = 1, at the fitted T
    ece: tuple[float, float]
    accuracy: float
    mean_confidence: tuple[float, float]
    per_question: dict[str, int] = field(default_factory=dict)


class _Capturing:
    """A backend that passes each request on and keeps what came back."""

    def __init__(self, inner: Backend) -> None:
        self._inner = inner
        self.name = inner.name
        self.model = inner.model
        self.requests: list[tuple[dict[str, Any], dict[str, dict[str, float]]]] = []

    def screen(self, call: ParsedToolCall) -> str | None:
        return self._inner.screen(call)

    def ask(
        self, evidence: dict[str, Any], questions: dict[str, QuestionSpec]
    ) -> AskResult:
        result = self._inner.ask(evidence, questions)
        self.requests.append((evidence, result.answers))
        return result

    def describe(self) -> ModelInfo:
        return self._inner.describe()


def state_key(state: dict[str, Any]) -> str:
    return json.dumps(state, sort_keys=True, ensure_ascii=False)


def gold_answer(question: dict[str, Any]) -> str:
    """A record question's label as a backend answer key."""
    if question["type"] == "noul":
        return "yes" if question["label"] else "no"
    return str(question["label"])


def capture_rows(
    calls: Iterable[ParsedToolCall],
    records: list[dict[str, Any]],
    backend: Backend,
    max_evidence_chars: int = DEFAULT_MAX_EVIDENCE_CHARS,
    first_party_mcp_servers: frozenset[str] = frozenset(),
) -> tuple[list[Row], Counter[str]]:
    """Label the calls through `backend` and pair each answered question with the
    gold of the record whose state the request carried."""
    gold: dict[str, dict[str, str]] = {}
    for r in records:
        answers = {q: gold_answer(spec) for q, spec in r["questions"].items()}
        gold.setdefault(state_key(r["state"]), {}).update(answers)
    capturing = _Capturing(backend)
    cards = load_cards()
    counts: Counter[str] = Counter()
    for call in calls:
        outcome = label_call(
            call, cards, capturing, max_evidence_chars, first_party_mcp_servers
        )
        counts["skipped" if outcome.skipped else "calls"] += 1
    rows: list[Row] = []
    for evidence, answers in capturing.requests:
        counts["requests"] += 1
        expected = gold.get(state_key(evidence))
        if expected is None:
            counts["unmatched_requests"] += 1
            continue
        for question, distribution in answers.items():
            if question in expected and expected[question] in distribution:
                rows.append(Row(question, dict(distribution), expected[question]))
    return rows, counts


def scaled(distribution: dict[str, float], temperature: float) -> dict[str, float]:
    logs = {k: math.log(max(p, EPSILON)) / temperature for k, p in distribution.items()}
    top = max(logs.values())
    weights = {k: math.exp(v - top) for k, v in logs.items()}
    total = sum(weights.values())
    return {k: w / total for k, w in weights.items()}


def mean_nll(rows: list[Row], temperature: float) -> float:
    return sum(
        -math.log(max(scaled(r.distribution, temperature)[r.gold], EPSILON))
        for r in rows
    ) / len(rows)


def fit_temperature(
    rows: list[Row], bounds: tuple[float, float] = TEMPERATURE_RANGE
) -> float:
    lo, hi = math.log(bounds[0]), math.log(bounds[1])
    grid = [lo + (hi - lo) * i / (GRID_POINTS - 1) for i in range(GRID_POINTS)]
    best = min(range(GRID_POINTS), key=lambda i: mean_nll(rows, math.exp(grid[i])))
    a, b = grid[max(best - 1, 0)], grid[min(best + 1, GRID_POINTS - 1)]
    ratio = (math.sqrt(5) - 1) / 2
    for _ in range(40):
        c, d = b - ratio * (b - a), a + ratio * (b - a)
        if mean_nll(rows, math.exp(c)) <= mean_nll(rows, math.exp(d)):
            b = d
        else:
            a = c
    return math.exp((a + b) / 2)


def _pairs(rows: list[Row], temperature: float) -> list[Pair]:
    pairs = []
    for r in rows:
        p = scaled(r.distribution, temperature)
        chosen = max(p, key=lambda k: p[k])
        pairs.append(Pair(gold=r.gold, predicted=chosen, p=p[chosen], basis=""))
    return pairs


def report(rows: list[Row], counts: Counter[str]) -> FitReport:
    if not rows:
        raise ValueError("no request matched a calibration record")
    t = fit_temperature(rows)
    before, after = _pairs(rows, 1.0), _pairs(rows, t)
    return FitReport(
        temperature=t,
        rows=len(rows),
        calls=counts["calls"],
        requests=counts["requests"],
        unmatched_requests=counts["unmatched_requests"],
        nll=(mean_nll(rows, 1.0), mean_nll(rows, t)),
        ece=(expected_calibration_error(before), expected_calibration_error(after)),
        accuracy=sum(p.gold == p.predicted for p in before) / len(before),
        mean_confidence=(
            sum(p.p for p in before) / len(before),
            sum(p.p for p in after) / len(after),
        ),
        per_question=dict(Counter(r.question for r in rows)),
    )


def format_fit(model: str, fit: FitReport) -> str:
    questions = ", ".join(f"{q} {n}" for q, n in sorted(fit.per_question.items()))
    lo, hi = TEMPERATURE_RANGE
    lines = [
        f"{model}: temperature fitted through the labeling path",
        f"  calls {fit.calls}, requests {fit.requests}"
        + f" ({fit.unmatched_requests} matched no record),"
        + f" questions scored {fit.rows}: {questions}",
        f"  fitted T {fit.temperature:.2f}  (searched {lo}-{hi}, min mean NLL)",
        f"  accuracy {fit.accuracy:.3f}",
        f"  mean confidence  T=1 {fit.mean_confidence[0]:.3f}"
        + f"  fitted {fit.mean_confidence[1]:.3f}",
        f"  NLL              T=1 {fit.nll[0]:.4f}  fitted {fit.nll[1]:.4f}",
        f"  ECE ({ECE_BINS} bins)    T=1 {fit.ece[0]:.3f}  fitted {fit.ece[1]:.3f}",
        "  measured on the partition it was fitted on: check held-out ECE"
        + " before serving with it",
    ]
    return "\n".join(lines)
