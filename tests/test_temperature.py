import math
import random

from test_labeler import FakeBackend, call

from spangloss.eval.temperature import (
    Row,
    capture_rows,
    fit_temperature,
    mean_nll,
    report,
    scaled,
)


def test_scaling_at_one_is_identity_and_higher_t_softens() -> None:
    d = {"yes": 0.9, "no": 0.1}
    assert all(math.isclose(scaled(d, 1.0)[k], d[k]) for k in d)
    assert scaled(d, 2.0)["yes"] < 0.9
    assert scaled(d, 0.5)["yes"] > 0.9
    assert math.isclose(sum(scaled({"a": 0.5, "b": 0.3, "c": 0.2}, 1.7).values()), 1)


def test_fit_recovers_the_temperature_that_made_a_model_overconfident() -> None:
    # True confidence q; the model reports q sharpened by T = 2 (logits doubled).
    rng = random.Random(0)
    rows = []
    for _ in range(4000):
        q = rng.uniform(0.55, 0.95)
        right = rng.random() < q
        reported = scaled({"yes": q, "no": 1 - q}, 0.5)
        rows.append(Row("q", reported, "yes" if right else "no"))
    t = fit_temperature(rows)
    assert 1.8 < t < 2.2
    assert mean_nll(rows, t) < mean_nll(rows, 1.0)


def test_capture_scores_only_requests_matching_a_record_state() -> None:
    backend = FakeBackend()
    c = call()
    rows, counts = capture_rows([c], [], backend)
    assert rows == [] and counts["requests"] >= 1
    assert counts["unmatched_requests"] == counts["requests"]

    evidence, questions = backend.requests[0]
    noul = next(q for q, spec in questions.items() if spec.kind == "noul")
    record = {
        "state": evidence,
        "questions": {noul: {"type": "noul", "label": False}},
        "meta": {"span_id": c.span_id},
    }
    rows, counts = capture_rows([c], [record], FakeBackend())
    assert [(r.question, r.gold) for r in rows] == [(noul, "no")]
    assert rows[0].distribution == {"yes": 0.2, "no": 0.8}  # FakeBackend's answer

    fit = report(rows * 10, counts)
    assert fit.accuracy == 1.0 and fit.temperature < 1.0  # right and underconfident
