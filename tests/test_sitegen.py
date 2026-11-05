import json
from pathlib import Path

import pytest

from spangloss.parse import ParsedToolCall
from spangloss.parse.record import basis_for
from spangloss.questions import load_cards
from spangloss.questions.derived import DERIVED_LABELS, derived_answers
from spangloss.sitegen import (
    OUTPUT,
    TAXONOMY,
    TaxonomyError,
    generate,
    main,
    render,
)

TEXT = TAXONOMY.read_text(encoding="utf-8")


def call(tool_name: str, args: dict, output: str | None, **extra) -> ParsedToolCall:
    return ParsedToolCall(
        trace_id="t" * 32,
        span_id="s000000000000001",
        parent_tool_span_id=None,
        session_id="session-1",
        start_time="2026-01-01T00:00:00.000000Z",
        end_time="2026-01-01T00:00:01.000000Z",
        tool_name=tool_name,
        args=args,
        args_source="log",
        args_truncated=False,
        output=output,
        has_output=output is not None,
        basis=basis_for(args, output),
        success=True,
        permission_decision="accept",
        permission_source="config",
        **extra,
    )


def tool_search(servers: list[str] | None) -> ParsedToolCall:
    return call("ToolSearch", {"query": "select:x"}, None, loaded_mcp_servers=servers)


def _flat(data: dict) -> dict[str, dict]:
    out = {}
    for entry in data["labels"]:
        for e in entry.get("sub_labels", [entry]):
            out[e["name"]] = e
    return out


def test_site_labels_json_matches_taxonomy() -> None:
    assert OUTPUT.read_text(encoding="utf-8") == render(generate(TEXT)), (
        "site/labels.json is out of date; run uv run python -m spangloss.sitegen"
    )
    assert main(["--check"]) == 0


def test_every_card_label_is_on_the_site_with_the_cards_gating() -> None:
    labels = _flat(generate(TEXT))
    cards = load_cards()
    assert set(labels) == {c.label for c in cards}
    for card in cards:
        assert labels[card.label]["min_basis"] == card.min_basis, card.name


def test_answers_match_the_cards() -> None:
    labels = _flat(generate(TEXT))
    for card in load_cards():
        if card.step == 1:
            continue  # data_class's yes/no step; its answers are step 2's plus none
        expected = list(card.answers)
        if card.step == 2:
            expected = ["none", *expected]
        assert labels[card.label]["answers"] == expected, card.name


def test_derived_labels_lists_exactly_what_code_can_write() -> None:
    mcp = call("mcp__vendor__convert", {"v": 1}, "1")
    env_test = call("Read", {"file_path": "/w/.env.test"}, None)
    spawn = call("Agent", {"prompt": "x"}, None)
    written = set()
    for c in (mcp, env_test, spawn, tool_search(["vendor"]), tool_search(None)):
        written |= set(derived_answers(c, frozenset()))
    assert written == DERIVED_LABELS


def test_source_and_backends() -> None:
    labels = _flat(generate(TEXT))
    assert labels["sec.effect"]["source"] == "model"
    assert labels["sec.data_class"]["source"] == "both"
    assert labels["sec.injection"]["backends"] == {
        "reported_by": ["jev"],
        "not_reported_by": ["kev"],
    }


def test_the_trifecta_summary_is_left_to_the_owner() -> None:
    data = generate(TEXT)
    [trifecta] = [e for e in data["labels"] if e["name"] == "sec.trifecta"]
    assert trifecta["asks_about"] == "[owner to write]"
    assert data["asks_about_status"] == "proposed"


def test_a_new_label_without_a_summary_fails() -> None:
    extra = TEXT.replace(
        "### `sec.injection` — tier 2",
        "### `sec.example` — tier 2\n\n| Answer | Definition |\n| --- | --- |\n"
        "| `a` | A |\n| `b` | B |\n\n### `sec.injection` — tier 2",
    )
    with pytest.raises(TaxonomyError, match="sec.example: not in the gating table"):
        generate(extra)
    gated = extra.replace(
        "| `sec.intent` | Basis is `args` or above |",
        "| `sec.intent` | Basis is `args` or above |\n| `sec.example` | Basis is `args`"
        " or above |",
    )
    with pytest.raises(TaxonomyError, match="sec.example: no asks_about"):
        generate(gated)


def test_check_fails_when_out_of_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spangloss import sitegen

    stale = tmp_path / "labels.json"
    stale.write_text(json.dumps({"taxonomy": "0.0.0"}))
    monkeypatch.setattr(sitegen, "OUTPUT", stale)
    assert main(["--check"]) == 1
    assert main([]) == 0
    assert main(["--check"]) == 0
