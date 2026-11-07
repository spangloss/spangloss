"""DuckDB label store: one row per tool call per label per model, plus rule findings."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

import duckdb

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
# Stores created before a column existed gain it, empty for old rows.
MIGRATIONS = [
    "ALTER TABLE labels ADD COLUMN IF NOT EXISTS input_tokens INTEGER",
    "ALTER TABLE labels ADD COLUMN IF NOT EXISTS chunks INTEGER",
    "ALTER TABLE findings ADD COLUMN IF NOT EXISTS handback_span_ids VARCHAR[]",
]


@dataclass(frozen=True, slots=True)
class Rederived:
    derived_before: int  # derived rows for the given calls before
    derived_after: int  # derived rows written for them
    model_rows_replaced: int  # model rows removed because the label is now derived
    unsupported_rows_dropped: int  # model rows for labels their backend doesn't answer


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
        for statement in MIGRATIONS:
            self._conn.execute(statement)

    def write(self, labels: Iterable[Label]) -> int:
        """Insert labels, replacing any earlier row for the same call, label and model."""
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
            )
            for x in labels
        ]
        if rows:
            self._conn.executemany(
                "INSERT OR REPLACE INTO labels (span_id, session_id, label, answer, p,"
                " basis, model, taxonomy, output_truncated, input_tokens, chunks)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

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
