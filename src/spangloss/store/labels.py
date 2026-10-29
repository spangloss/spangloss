"""DuckDB label store: one row per tool call per label per model."""

from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import Self

import duckdb

from spangloss.questions.runner import Label

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
# Stores created before a column existed gain it, empty for old rows.
MIGRATIONS = [
    "ALTER TABLE labels ADD COLUMN IF NOT EXISTS input_tokens INTEGER",
    "ALTER TABLE labels ADD COLUMN IF NOT EXISTS chunks INTEGER",
]


class LabelStore:
    def __init__(self, path: Path = DEFAULT_DB) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(path))
        self._conn.execute(SCHEMA)
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
