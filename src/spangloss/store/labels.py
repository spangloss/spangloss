"""DuckDB label store: one row per tool call per label per model, plus rule findings
and a log of labeling runs.

Each `spangloss label` run that asks a model anything gets a row in `runs` with
what the backend's server said about its model at the start and the end of the
run, and each label row it writes carries that run's id. Labels written before
runs were recorded have no run id.
"""

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import duckdb

from spangloss.backends.base import ModelInfo
from spangloss.backends.support import unsupported_labels
from spangloss.eval.score import StoredLabel
from spangloss.questions.derived import DERIVED_MODEL
from spangloss.questions.runner import Label
from spangloss.rules.findings import Answers, Finding

DEFAULT_DB = Path("data/labels.duckdb")

SCHEMA = """
CREATE TABLE IF NOT EXISTS labels (
    span_id          VARCHAR NOT NULL,
    session_id       VARCHAR,
    label            VARCHAR NOT NULL,
    answer           VARCHAR NOT NULL,
    p                DOUBLE  NOT NULL,
    basis            VARCHAR NOT NULL,
    model            VARCHAR NOT NULL,
    taxonomy         VARCHAR NOT NULL,
    output_truncated BOOLEAN NOT NULL,
    input_tokens     INTEGER,
    chunks           INTEGER,
    labeled_at       TIMESTAMP NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (span_id, label, model)
)
"""
# One row per rule, span and model: the labels from that model (plus derived labels)
# that the rule read. A rerun of a rule over a model replaces its earlier findings.
FINDINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
    rule             VARCHAR   NOT NULL,
    span_id          VARCHAR   NOT NULL,
    session_id       VARCHAR,
    trigger_span_ids VARCHAR[] NOT NULL,
    handback_span_ids VARCHAR[],
    model            VARCHAR   NOT NULL,
    found_at         TIMESTAMP NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (rule, span_id, model)
)
"""
# One row per labeling run. checkpoint and card are read when the run starts,
# checkpoint_at_end and served when it ends; a run that stopped early has no end.
RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id            VARCHAR   PRIMARY KEY,
    model             VARCHAR   NOT NULL,
    checkpoint        VARCHAR,
    card              JSON,
    checkpoint_at_end VARCHAR,
    served            VARCHAR[],
    spangloss         VARCHAR   NOT NULL,
    calls_labeled     INTEGER,
    labels_written    INTEGER,
    started_at        TIMESTAMP NOT NULL,
    finished_at       TIMESTAMP
)
"""
# Stores created before a column existed gain it, empty for old rows.
MIGRATIONS = [
    "ALTER TABLE labels ADD COLUMN IF NOT EXISTS input_tokens INTEGER",
    "ALTER TABLE labels ADD COLUMN IF NOT EXISTS chunks INTEGER",
    "ALTER TABLE findings ADD COLUMN IF NOT EXISTS handback_span_ids VARCHAR[]",
    "ALTER TABLE labels ADD COLUMN IF NOT EXISTS run_id VARCHAR",
]


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where one model's stored labels came from, by checkpoint."""

    model: str
    # checkpoint -> label rows; None for rows from runs whose server didn't name it
    by_checkpoint: dict[str | None, int]
    unrecorded: int  # rows written before runs were recorded, or by no run
    changed: int  # rows from runs whose checkpoint changed between start and end
    unfinished: int  # rows from runs that never recorded an end

    @property
    def mixed(self) -> bool:
        """The rows can't be pinned to a single checkpoint."""
        return len(self.by_checkpoint) + bool(self.unrecorded) > 1 or bool(self.changed)


@dataclass(frozen=True, slots=True)
class Rederived:
    derived_before: int  # derived rows for the given calls before
    derived_after: int  # derived rows written for them
    model_rows_replaced: int  # model rows removed because the label is now derived
    unsupported_rows_dropped: int  # model rows for labels their backend doesn't answer


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class LabelStore:
    def __init__(self, path: Path = DEFAULT_DB, *, read_only: bool = False) -> None:
        """read_only opens an existing store without creating or migrating it, so
        its file is left byte for byte as it was."""
        if read_only:
            self._conn = duckdb.connect(str(path), read_only=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(path))
        self._conn.execute(SCHEMA)
        self._conn.execute(FINDINGS_SCHEMA)
        self._conn.execute(RUNS_SCHEMA)
        for statement in MIGRATIONS:
            self._conn.execute(statement)

    def write(self, labels: Iterable[Label], run_id: str | None = None) -> int:
        """Insert labels, replacing any earlier row for the same call, label and model.
        run_id is the labeling run that asked for them, if any."""
        rows = [
            (
                x.span_id,
                x.session_id,
                x.label,
                x.answer,
                x.p,
                x.basis,
                x.model,
                x.taxonomy,
                x.output_truncated,
                x.input_tokens,
                x.chunks,
                run_id,
            )
            for x in labels
        ]
        if rows:
            self._conn.executemany(
                "INSERT OR REPLACE INTO labels (span_id, session_id, label, answer, p,"
                " basis, model, taxonomy, output_truncated, input_tokens, chunks,"
                " run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def start_run(self, model: str, info: ModelInfo, spangloss: str) -> str:
        """Log the start of a labeling run and return its id."""
        run_id = uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO runs (run_id, model, checkpoint, card, spangloss, started_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                run_id,
                model,
                info.checkpoint,
                None if info.card is None else json.dumps(info.card, sort_keys=True),
                spangloss,
                _now(),
            ],
        )
        return run_id

    def finish_run(
        self, run_id: str, info: ModelInfo, calls_labeled: int, labels_written: int
    ) -> None:
        self._conn.execute(
            "UPDATE runs SET checkpoint_at_end = ?, served = ?, calls_labeled = ?,"
            " labels_written = ?, finished_at = ? WHERE run_id = ?",
            [
                info.checkpoint,
                list(info.served),
                calls_labeled,
                labels_written,
                _now(),
                run_id,
            ],
        )

    def runs(self) -> list[dict[str, Any]]:
        """The run log, oldest first; empty for a store from before it existed."""
        if not self._has_table("runs"):
            return []
        cursor = self._conn.execute("SELECT * FROM runs ORDER BY started_at, run_id")
        names = [d[0] for d in cursor.description]
        runs = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        for run in runs:
            if run["card"] is not None:
                run["card"] = json.loads(run["card"])
        return runs

    def provenance(self) -> list[Provenance]:
        """Per model (derived labels aside), the checkpoints behind its label rows."""
        if self._has_table("runs") and self._has_column("labels", "run_id"):
            rows = self._conn.execute(
                "SELECT l.model, r.run_id IS NOT NULL, r.checkpoint,"
                " r.finished_at IS NOT NULL,"
                " r.finished_at IS NOT NULL"
                " AND r.checkpoint IS DISTINCT FROM r.checkpoint_at_end,"
                " count(*) FROM labels l LEFT JOIN runs r USING (run_id)"
                " WHERE l.model != ? GROUP BY ALL ORDER BY ALL",
                [DERIVED_MODEL],
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT model, false, NULL, false, false, count(*) FROM labels"
                " WHERE model != ? GROUP BY model ORDER BY model",
                [DERIVED_MODEL],
            ).fetchall()
        out: dict[str, Provenance] = {}
        for model, recorded, checkpoint, finished, changed, n in rows:
            p = out.setdefault(model, Provenance(model, {}, 0, 0, 0))
            if not recorded:
                out[model] = replace(p, unrecorded=p.unrecorded + n)
                continue
            by = dict(p.by_checkpoint)
            by[checkpoint] = by.get(checkpoint, 0) + n
            out[model] = replace(
                p,
                by_checkpoint=by,
                changed=p.changed + (n if changed else 0),
                unfinished=p.unfinished + (0 if finished else n),
            )
        return list(out.values())

    def _has_table(self, name: str) -> bool:
        return bool(
            self._conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
                [name],
            ).fetchone()[0]
        )

    def _has_column(self, table: str, column: str) -> bool:
        return bool(
            self._conn.execute(
                "SELECT count(*) FROM information_schema.columns"
                " WHERE table_name = ? AND column_name = ?",
                [table, column],
            ).fetchone()[0]
        )

    def rederive(self, span_ids: Iterable[str], derived: Iterable[Label]) -> Rederived:
        """Bring stored labels in line with the current derived and unsupported rules,
        as a fresh labeling run would leave them, without asking any model.

        For the given calls, earlier derived rows are replaced by `derived`, and a
        model's row for a label that is now derived is removed. Across the store, rows
        for a label a model's backend does not answer are removed.
        """
        spans = list(span_ids)
        new = list(derived)
        before = self._count()
        self._conn.execute("BEGIN TRANSACTION")
        try:
            if spans:
                self._conn.executemany(
                    "DELETE FROM labels WHERE span_id = ? AND model = ?",
                    [(s, DERIVED_MODEL) for s in spans],
                )
            cleared = self._count()
            if new:
                self._conn.executemany(
                    "DELETE FROM labels WHERE span_id = ? AND label = ?",
                    [(x.span_id, x.label) for x in new],
                )
            replaced = cleared - self._count()
            for model in self.models():
                for label in unsupported_labels(model):
                    self._conn.execute(
                        "DELETE FROM labels WHERE model = ? AND label = ?",
                        [model, label],
                    )
            dropped = cleared - replaced - self._count()
            self.write(new)
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return Rederived(
            derived_before=before - cleared,
            derived_after=len(new),
            model_rows_replaced=replaced,
            unsupported_rows_dropped=dropped,
        )

    def _count(self) -> int:
        return self._conn.execute("SELECT count(*) FROM labels").fetchone()[0]

    def span_ids(self) -> set[str]:
        """Calls with any label in the store."""
        rows = self._conn.execute("SELECT DISTINCT span_id FROM labels").fetchall()
        return {r[0] for r in rows}

    def labeled_span_ids(self, model: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT span_id FROM labels WHERE model = ?", [model]
        ).fetchall()
        return {r[0] for r in rows}

    def rows(self) -> list[tuple]:
        return self._conn.execute(
            "SELECT span_id, session_id, label, answer, p, basis, model, taxonomy,"
            " output_truncated, input_tokens, chunks FROM labels"
            " ORDER BY span_id, label"
        ).fetchall()

    def stored_labels(self) -> list[StoredLabel]:
        rows = self._conn.execute(
            "SELECT span_id, label, answer, p, model, taxonomy FROM labels"
            " ORDER BY model, span_id, label"
        ).fetchall()
        return [StoredLabel(*r) for r in rows]

    def models(self) -> list[str]:
        """Models with labels in the store, not counting derived labels."""
        rows = self._conn.execute(
            "SELECT DISTINCT model FROM labels WHERE model != ? ORDER BY model",
            [DERIVED_MODEL],
        ).fetchall()
        return [r[0] for r in rows]

    def answers(self, model: str) -> Answers:
        """span_id -> label -> answer from one model's labels plus derived labels."""
        rows = self._conn.execute(
            "SELECT span_id, label, answer FROM labels WHERE model IN (?, ?)"
            " ORDER BY model = ?",  # derived rows last, so they win
            [model, DERIVED_MODEL, DERIVED_MODEL],
        ).fetchall()
        answers: Answers = {}
        for span_id, label, answer in rows:
            answers.setdefault(span_id, {})[label] = answer
        return answers

    def replace_findings(
        self, rule: str, model: str, findings: Iterable[Finding]
    ) -> int:
        """Replace a rule's findings over one model's labels."""
        rows = [
            (
                f.rule,
                f.span_id,
                f.session_id,
                list(f.trigger_span_ids),
                list(f.handback_span_ids),
                model,
            )
            for f in findings
        ]
        if any(r[0] != rule for r in rows):
            raise ValueError(f"findings from another rule passed as {rule}")
        self._conn.execute("BEGIN TRANSACTION")
        try:
            self._conn.execute(
                "DELETE FROM findings WHERE rule = ? AND model = ?", [rule, model]
            )
            if rows:
                self._conn.executemany(
                    "INSERT INTO findings (rule, span_id, session_id,"
                    " trigger_span_ids, handback_span_ids, model)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    rows,
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return len(rows)

    def findings(self) -> list[tuple]:
        """(rule, span_id, session_id, trigger_span_ids, model, handback_span_ids)."""
        return self._conn.execute(
            "SELECT rule, span_id, session_id, trigger_span_ids, model,"
            " coalesce(handback_span_ids, []) FROM findings"
            " ORDER BY rule, span_id, model"
        ).fetchall()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
