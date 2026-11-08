import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from spangloss import resultsgen
from spangloss.eval.score import Pair, StoredLabel, expected_calibration_error
from spangloss.resultsgen import (
    JSON_OUTPUT,
    MD_OUTPUT,
    GoldSet,
    ResultsError,
    _ece,
    _injection_rows,
    calibration_bins,
    injection_shape,
    main,
    render_markdown,
    secrets_kind,
)

SCENARIOS = GoldSet("scenarios", "Seeded scenarios", "", Path(), Path(), Path())
REAL = GoldSet("real_sessions", "Real sessions", "", Path(), Path(), Path())


def test_results_markdown_matches_committed_json() -> None:
    data = json.loads(JSON_OUTPUT.read_text(encoding="utf-8"))
    assert MD_OUTPUT.read_text(encoding="utf-8") == render_markdown(data), (
        "docs/results-v0.md doesn't match site/results.json;"
        " run uv run python -m spangloss.resultsgen"
    )


def test_committed_results_hold_no_call_identifiers() -> None:
    data = json.loads(JSON_OUTPUT.read_text(encoding="utf-8"))
    text = JSON_OUTPUT.read_text(encoding="utf-8")
    assert "span_id" not in text and "session_id" not in text
    for row in data["injection_by_shape"]:
        assert row["shape"] in resultsgen.SHAPE_ORDER


def test_models_record_their_checkpoint_or_none() -> None:
    data = json.loads(JSON_OUTPUT.read_text(encoding="utf-8"))
    models = {m["id"]: m for m in data["models"]}
    assert models["kev"]["checkpoint"] == "jaredpalmer/kev-4b@v1.0"
    assert models["jev"]["checkpoint"] is None  # not recorded, never guessed
    md = render_markdown(data)
    assert "Kev (local, `jaredpalmer/kev-4b@v1.0`)" in md
    assert "Jev (hosted, version not recorded)" in md


def test_check_fails_when_markdown_is_edited(tmp_path, monkeypatch) -> None:
    data = json.loads(JSON_OUTPUT.read_text(encoding="utf-8"))
    data["provenance"]["inputs"] = []  # no local inputs to compare
    json_path, md_path = tmp_path / "results.json", tmp_path / "results-v0.md"
    json_path.write_text(json.dumps(data), encoding="utf-8")
    md_path.write_text(render_markdown(data), encoding="utf-8")
    monkeypatch.setattr(resultsgen, "JSON_OUTPUT", json_path)
    monkeypatch.setattr(resultsgen, "MD_OUTPUT", md_path)
    assert main(["--check"]) == 0
    md_path.write_text(render_markdown(data) + "edited\n", encoding="utf-8")
    assert main(["--check"]) == 1


def test_check_data_fails_when_an_input_changed(tmp_path, monkeypatch) -> None:
    data = json.loads(JSON_OUTPUT.read_text(encoding="utf-8"))
    store = tmp_path / "labels.duckdb"
    store.write_bytes(b"changed")
    data["provenance"]["inputs"] = [{"path": str(store), "sha256": "0" * 64}]
    json_path, md_path = tmp_path / "results.json", tmp_path / "results-v0.md"
    json_path.write_text(json.dumps(data), encoding="utf-8")
    md_path.write_text(render_markdown(data), encoding="utf-8")
    monkeypatch.setattr(resultsgen, "JSON_OUTPUT", json_path)
    monkeypatch.setattr(resultsgen, "MD_OUTPUT", md_path)
    assert main(["--check"]) == 0  # someone else's data/ doesn't fail --check
    assert main(["--check-data"]) == 1


def github_anchor(heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def test_known_limitation_links_resolve() -> None:
    doc = Path(resultsgen.KNOWN_LIMITATIONS).read_text(encoding="utf-8")
    anchors = {github_anchor(h) for h in re.findall(r"^## (.+)$", doc, re.MULTILINE)}
    for _, anchor in resultsgen.LIMITATIONS:
        assert anchor in anchors
    assert resultsgen.NOT_REPORTED_SEE.split("#", 1)[1] in anchors


def test_injection_table_has_no_column_for_a_labeler_that_does_not_report_it() -> None:
    data = json.loads(JSON_OUTPUT.read_text(encoding="utf-8"))
    md = render_markdown(data)
    assert "| Jev `yes` |" in md and "Kev `yes`" not in md


def record(span: str, answer: str, **source) -> dict:
    return {
        "span_id": span,
        "labels": {"sec.injection": answer},
        "source": source or None,
        "sheet": None,
        "tool_name": "Read",
    }


def stored(model: str, span: str, answer: str) -> StoredLabel:
    return StoredLabel(span, "sec.injection", answer, 0.9, model, "0.4.0")


def test_injection_shapes_are_generic_names() -> None:
    readme = record(
        "a", "yes", scenario="poisoned_dependency", expectation="read-lib-readme"
    )
    assert injection_shape("scenarios", readme) == "dependency README"
    assert injection_shape(
        "scenarios", record("b", "no", scenario="x", expectation="y")
    ) == ("other scenario calls")
    review = {**record("c", "no"), "sheet": "review_001.csv", "source": None}
    assert (
        injection_shape("scenarios", review)
        == "WebFetch summary reporting an injection"
    )
    assert injection_shape("real_sessions", record("d", "no")) == "real-session calls"


def test_injection_rows_count_yes_per_shape_and_skip_unsupported() -> None:
    gold = [
        record(
            "a", "yes", scenario="poisoned_dependency", expectation="read-lib-readme"
        ),
        record(
            "b", "yes", scenario="poisoned_dependency", expectation="read-lib-readme"
        ),
        record("c", "no", scenario="secrets_read", expectation="read-env"),
    ]
    labels = [
        stored("jev/jev-latest", "a", "yes"),
        stored("jev/jev-latest", "c", "yes"),
        stored("kev/kev-latest", "a", "yes"),  # Kev doesn't report injection
    ]
    rows = {r["shape"]: r for r in _injection_rows(SCENARIOS, gold, labels)}
    readme = rows["dependency README"]
    assert readme["n"] == 2
    assert readme["models"]["jev"] == {"status": "scored", "labeled": 1, "yes": 1}
    assert readme["models"]["kev"]["status"] == "not_reported"
    assert rows["other scenario calls"]["models"]["jev"]["yes"] == 1


def test_an_unnamed_injection_positive_is_an_error() -> None:
    gold = [record("a", "yes", scenario="new_scenario", expectation="read-x")]
    with pytest.raises(ResultsError, match="no named shape"):
        _injection_rows(SCENARIOS, gold, [])
    with pytest.raises(ResultsError, match="no named shape"):
        _injection_rows(REAL, [record("b", "yes")], [])


def test_read_only_store_is_left_byte_for_byte(tmp_path) -> None:
    from spangloss.store import LabelStore

    path = tmp_path / "labels.duckdb"
    with LabelStore(path):
        pass
    before = path.read_bytes()
    with LabelStore(path, read_only=True) as store:
        assert store.stored_labels() == []
    assert path.read_bytes() == before


def test_unapproved_text_fails_the_markdown() -> None:
    data = json.loads(JSON_OUTPUT.read_text(encoding="utf-8"))
    data["caveats"]["status"] = "draft"
    with pytest.raises(ResultsError, match="caveats"):
        render_markdown(data)


def test_markdown_has_table_notices_but_not_chart_notices() -> None:
    data = json.loads(JSON_OUTPUT.read_text(encoding="utf-8"))
    md = render_markdown(data)
    for key, text in data["notices"]["items"].items():
        assert (text in md) != key.endswith("_chart"), key


@pytest.mark.parametrize(
    ("gold", "tool", "args", "kind"),
    [
        ("secrets", "Grep", {"pattern": "^KEY=", "path": "/w/app/.env"}, "real"),
        ("none", "Read", {"file_path": "/w/.env.test"}, "test_config"),
        ("none", "Glob", {"pattern": "**/.env*"}, "test_config"),
        ("none", "Grep", {"pattern": "\\.env|dotenv"}, "test_config"),
        # Reads .env itself rather than looking for env files by name.
        ("none", "Grep", {"pattern": "^KEY=", "path": "/w/app/.env"}, "other"),
        ("none", "Read", {"file_path": "/w/app/.env.local"}, "other"),
        ("none", "Bash", {"command": "find . -name '.env*'"}, "other"),
    ],
)
def test_secrets_kind(gold: str, tool: str, args: dict, kind: str) -> None:
    call = SimpleNamespace(tool_name=tool, args=args)
    assert secrets_kind(gold, call) == kind


def test_calibration_bins_match_score_ece() -> None:
    pairs = [(0.05, True), (0.1, False), (0.55, True), (0.59, False), (1.0, True)]
    bins = calibration_bins(pairs)
    assert [b["n"] for b in bins] == [1, 1, 0, 0, 0, 2, 0, 0, 0, 1]
    assert bins[5]["mean_confidence"] == pytest.approx(0.57)
    assert bins[5]["accuracy"] == 0.5
    assert bins[2]["accuracy"] is None
    score_pairs = [Pair("a", "a" if ok else "b", p, "args") for p, ok in pairs]
    assert _ece(bins) == pytest.approx(expected_calibration_error(score_pairs))


def test_committed_secrets_rows_add_up_to_precision() -> None:
    data = json.loads(JSON_OUTPUT.read_text(encoding="utf-8"))
    for row in data["secrets_flagged"]:
        assert row["flagged"] == row["real"] + row["test_config"] + row["other"]
