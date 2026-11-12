"""v0 is frozen: its published files and the inputs results.json records must not change.

The site files are pinned in tests/v0.sha256 (``cd site && shasum -a 256 -c ../tests/v0.sha256``).
The data inputs are checked against the digests results.json publishes; they are
git-ignored, so each is skipped where it is absent (a fresh clone).
"""

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
INPUTS = json.loads((SITE / "results.json").read_text())["provenance"]["inputs"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pinned_site_files() -> list[tuple[str, str]]:
    lines = (Path(__file__).parent / "v0.sha256").read_text().splitlines()
    return [(name, digest) for digest, name in (line.split() for line in lines)]


@pytest.mark.parametrize(("name", "digest"), pinned_site_files())
def test_published_site_file_is_unchanged(name: str, digest: str) -> None:
    assert sha256(SITE / name) == digest, f"site/{name} differs from the published v0"


def test_results_json_records_the_frozen_inputs() -> None:
    assert {i["path"] for i in INPUTS} >= {
        "data/eval/gold.jsonl",
        "data/scenarios/gold.jsonl",
        "data/parsed/tool_calls.jsonl",
        "data/scenarios/tool_calls.jsonl",
        "data/labels.duckdb",
        "data/scenarios/labels.duckdb",
    }


@pytest.mark.parametrize("entry", INPUTS, ids=[i["path"] for i in INPUTS])
def test_frozen_input_matches_its_published_digest(entry: dict) -> None:
    path = ROOT / entry["path"]
    if not path.exists():
        pytest.skip(f"{entry['path']} is local only")
    assert sha256(path) == entry["sha256"], (
        f"{entry['path']} differs from the digest in site/results.json; v0 inputs are"
        " frozen (batch-1 data belongs under data/scenarios/batch1/)"
    )
