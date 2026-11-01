"""Compare the injection-split answers with injection gold and the current card.

    uv run python experiments/injection_split/analyze.py kev|jev

Reports (a) against injection gold, next to the current injection card's answers
from the label stores, and (b) on the reported-injection summaries (review_001.csv)
and the curl positives (curl-notes), plus any other call where (b) says yes.
"""

import json
import sys
from pathlib import Path

import duckdb

DIRECTS, MENTIONS = "sec.x_directs", "sec.x_mentions"
STORES = (Path("data/labels.duckdb"), Path("data/scenarios/labels.duckdb"))
FOCUS = {"review_001.csv", "curl-notes"}


def main(backend: str) -> None:
    path = Path(f"data/experiments/injection_split_{backend}.json")
    rows = [r for r in json.loads(path.read_text()) if not r["skipped"]]
    current = {}
    for db in STORES:
        query = (
            "SELECT span_id, answer, p FROM labels"
            " WHERE model = ? AND label = 'sec.injection'"
        )
        with duckdb.connect(str(db), read_only=True) as conn:
            for span_id, answer, p in conn.execute(
                query, [f"{backend}/{backend}-latest"]
            ).fetchall():
                current[span_id] = (answer, round(p, 3))

    def predicted(row: dict, key: str) -> str | None:
        if key == "current":
            return current.get(row["span_id"], (None,))[0]
        return row[key][0]

    def confusion(key: str) -> str:
        tp = fp = fn = tn = 0
        for r in rows:
            gold, pred = r["gold_injection"] == "yes", predicted(r, key) == "yes"
            tp += gold and pred
            fp += pred and not gold
            fn += gold and not pred
            tn += not gold and not pred
        n = tp + fp + fn + tn
        return (
            f"n={n} acc={(tp + tn) / n:.1%} TP={tp} FP={fp} FN={fn} TN={tn}"
            f" precision={tp}/{tp + fp} recall={tp}/{tp + fn}"
        )

    print(f"=== {backend}: injection gold vs")
    print("  current injection card:", confusion("current"))
    print("  (a) directs an action: ", confusion(DIRECTS))
    for kind, wanted in (("false positives", "no"), ("false negatives", "yes")):
        hits = [
            (r["span_id"], r["tool"], r["source"], r[DIRECTS])
            for r in rows
            if r["gold_injection"] == wanted and r[DIRECTS][0] != wanted
        ]
        print(f"  (a) {kind}: {hits}")
    print("  (b) mentions instructions aimed at an AI agent:")
    for r in rows:
        if r["source"] in FOCUS:
            print(
                f"     {r['span_id']} {r['tool']:8} {r['source']:15}"
                f" (b)={r[MENTIONS]}  (a)={r[DIRECTS]}"
                f"  current={current.get(r['span_id'])}"
            )
    others = [
        (r["span_id"], r["tool"], r["source"], r[MENTIONS])
        for r in rows
        if r["source"] not in FOCUS and r[MENTIONS][0] == "yes"
    ]
    print(f"  (b) yes on any other call: {others}")


if __name__ == "__main__":
    main(sys.argv[1])
