"""Injection-split experiment (docs/STATUS.md).

Asks the two questions in cards/ on every gold call where sec.injection is asked,
through the normal labeler (gating, chunking, the hosted screen). The committed
injection card is not changed. Usage, from the repo root:

    uv run python experiments/injection_split/run.py kev|jev

Output: data/experiments/injection_split_<backend>.json (git-ignored: it holds labels
of session calls).
"""

import json
import sys
from pathlib import Path

from spangloss.cli import make_backend, read_calls
from spangloss.eval.sheet import read_gold
from spangloss.questions import label_call
from spangloss.questions.cards import load_cards

HERE = Path(__file__).parent
OUT = Path("data/experiments")
GOLD_SETS = (
    ("hand", Path("data/eval/gold.jsonl"), Path("data/parsed/tool_calls.jsonl")),
    (
        "scenario",
        Path("data/scenarios/gold.jsonl"),
        Path("data/scenarios/tool_calls.jsonl"),
    ),
)


def main(backend_name: str) -> None:
    cards = load_cards(HERE / "cards")
    backend = make_backend(backend_name)
    rows = []
    for set_name, gold_path, calls_path in GOLD_SETS:
        gold = {
            r["span_id"]: r
            for r in read_gold(gold_path)
            if "sec.injection" in r["labels"]
        }
        for call in read_calls(calls_path):
            if call.span_id not in gold:
                continue
            record = gold[call.span_id]
            outcome = label_call(call, cards, backend)
            row = {
                "set": set_name,
                "span_id": call.span_id,
                "tool": call.tool_name,
                "gold_injection": record["labels"]["sec.injection"],
                "source": (record.get("source") or {}).get("expectation")
                or record.get("sheet"),
                "skipped": outcome.skipped,
            }
            for label in outcome.labels:
                row[label.label] = [label.answer, round(label.p, 3)]
            rows.append(row)
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"injection_split_{backend_name}.json"
    out.write_text(json.dumps(rows, indent=1))
    skipped = sum(1 for r in rows if r["skipped"])
    print(f"{backend_name}: {len(rows)} calls, {skipped} skipped -> {out}")


if __name__ == "__main__":
    main(sys.argv[1])
