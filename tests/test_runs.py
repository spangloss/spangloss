"""The run log: which model and checkpoint each labeling run used."""

import hashlib
from pathlib import Path
from typing import Any

import duckdb
import pytest
from typesafe_sdk import TypeSafeAPIConnectionError, TypeSafeNotFoundError

from spangloss import cli
from spangloss.backends.base import (
    AskResult,
    BackendUnavailable,
    ModelInfo,
    QuestionSpec,
)
from spangloss.backends.typesafe import TypeSafeBackend
from spangloss.cli import main
from spangloss.parse.record import ParsedToolCall
from spangloss.store import LabelStore

FIXTURES = Path(__file__).parent / "fixtures"
TRACES = FIXTURES / "claude_code_traces.jsonl"
LOGS = FIXTURES / "claude_code_logs.jsonl"
KEV_CARD = {
    "name": "kev-latest",
    "description": "Kev pointer head",
    "release_date": "2026-10-01",
    "run": "jaredpalmer/kev-4b@v1.0",
    "base": "Qwen/Qwen3.5-4B-Base",
}
QUESTION = {"q": QuestionSpec("noul", {"question": "Q?"}, {"yes": "Y", "no": "N"})}


# --- TypeSafe backends -------------------------------------------------------


class _Raw:
    def __init__(self, body: Any) -> None:
        self.body = body

    def json(self) -> Any:
        return self.body


class _Obj:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


class _Client:
    """Answers System One and /v1/models the way a TypeSafe server would."""

    def __init__(
        self,
        cards: list[dict[str, Any]],
        served: str | None = None,
        list_error: type[Exception] | None = None,
    ) -> None:
        self.cards = cards
        self.served = served  # the model name answers report; None echoes the alias
        self.list_error = list_error

    @property
    def models(self) -> "_Client":
        return self

    def list(self) -> Any:
        if self.list_error:
            raise self.list_error.__new__(self.list_error)
        return _Obj(raw_http_response=_Raw({"models": self.cards}))

    def system_one(self, state: Any, questions: dict[str, Any], model: str) -> Any:
        return _Obj(
            model=self.served or model,
            answers={qid: _Obj(noul=0.3) for qid in questions},
            usage=_Obj(input_tokens=10),
        )


def test_kev_names_the_checkpoint_from_its_model_card() -> None:
    other = KEV_CARD | {"name": "jev-latest", "run": "someone/else@v9"}
    client = _Client([other, KEV_CARD])
    kev = TypeSafeBackend("kev", "kev-latest", client)  # type: ignore[arg-type]
    before = kev.describe()
    assert before.checkpoint == "jaredpalmer/kev-4b@v1.0"
    assert before.card == KEV_CARD  # the whole entry, fields the SDK drops included
    assert before.served == ()
    kev.ask({"call": {}}, QUESTION)
    assert kev.describe().served == ("kev-latest",)


@pytest.mark.parametrize(
    ("served", "checkpoint"),
    [("jev-latest", None), ("jev-2026-10-15", "jev-2026-10-15")],
)
def test_jev_version_is_the_served_model_when_it_differs_from_the_alias(
    served: str, checkpoint: str | None
) -> None:
    card = {"name": "jev-latest", "description": "d", "release_date": "2026-09-15"}
    client = _Client([card], served=served)
    jev = TypeSafeBackend("jev", "jev-latest", client)  # type: ignore[arg-type]
    assert jev.describe().checkpoint is None  # nothing answered yet
    jev.ask({"call": {}}, QUESTION)
    after = jev.describe()
    assert after.checkpoint == checkpoint
    assert after.served == (served,)


def test_a_model_the_server_does_not_list_has_no_card() -> None:
    kev = TypeSafeBackend("kev", "kev-latest", _Client([]))  # type: ignore[arg-type]
    assert kev.describe() == ModelInfo(None, None, ())


def test_an_unreachable_model_list_stops_the_run() -> None:
    client = _Client([], list_error=TypeSafeAPIConnectionError)
    kev = TypeSafeBackend("kev", "kev-latest", client, unreachable="Kev is off")  # type: ignore[arg-type]
    with pytest.raises(BackendUnavailable, match="Kev is off"):
        kev.describe()


def test_a_failed_model_list_is_recorded_as_no_card(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _Client([], list_error=TypeSafeNotFoundError)
    kev = TypeSafeBackend("kev", "kev-latest", client)  # type: ignore[arg-type]
    assert kev.describe().card is None
    assert "model list unavailable (TypeSafeNotFoundError)" in caplog.text


# --- spangloss label ---------------------------------------------------------


class _Backend:
    """Answers every question without a model; describe() steps through infos."""

    def __init__(self, *infos: ModelInfo) -> None:
        self.name = "fake"
        self.model = "fake/test"
        self.infos = list(infos)
        self.described = 0

    def screen(self, call: ParsedToolCall) -> str | None:
        return None

    def ask(
        self, evidence: dict[str, Any], questions: dict[str, QuestionSpec]
    ) -> AskResult:
        answers = {
            qid: {"yes": 0.2, "no": 0.8}
            if q.kind == "noul"
            else {o: 1 / len(q.criteria) for o in q.criteria}
            for qid, q in questions.items()
        }
        return AskResult(answers, None)

    def describe(self) -> ModelInfo:
        self.described += 1
        return self.infos.pop(0) if len(self.infos) > 1 else self.infos[0]


def parsed(tmp_path: Path) -> Path:
    out = tmp_path / "tool_calls.jsonl"
    assert main(["parse", str(TRACES), "--logs", str(LOGS), "-o", str(out)]) == 0
    return out


def label(tmp_path: Path, backend: _Backend, monkeypatch, limit: int = 2) -> Path:
    calls = parsed(tmp_path)
    db = tmp_path / "labels.duckdb"
    monkeypatch.setattr(cli, "make_backend", lambda name: backend)
    args = ["label", "--backend", "kev", "--limit", str(limit), "--input", str(calls)]
    assert main([*args, "--db", str(db)]) == 0
    return db


def test_label_logs_the_run_and_tags_its_labels(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    start = ModelInfo("org/ckpt@v1", {"name": "test", "run": "org/ckpt@v1"}, ())
    end = ModelInfo("org/ckpt@v1", {"name": "test"}, ("test",))
    db = label(tmp_path, _Backend(start, end), monkeypatch)
    assert "fake/test: run " in capsys.readouterr().err
    with LabelStore(db) as store:
        [run] = store.runs()
        tagged = store._conn.execute(
            "SELECT DISTINCT run_id FROM labels WHERE model = 'fake/test'"
        ).fetchall()
    assert tagged == [(run["run_id"],)]
    assert run["model"] == "fake/test"
    assert run["checkpoint"] == run["checkpoint_at_end"] == "org/ckpt@v1"
    assert run["card"] == {"name": "test", "run": "org/ckpt@v1"}
    assert run["served"] == ["test"]
    assert run["calls_labeled"] == 2 and run["labels_written"] > 0
    assert run["started_at"] <= run["finished_at"]
    assert run["spangloss"] == cli.__version__


def test_a_run_with_nothing_to_label_is_not_logged(tmp_path: Path, monkeypatch) -> None:
    backend = _Backend(ModelInfo("c", None, ()))
    db = label(tmp_path, backend, monkeypatch, limit=0)
    assert backend.described == 0  # the server wasn't asked either
    with LabelStore(db) as store:
        assert store.runs() == []


def test_a_checkpoint_change_mid_run_is_warned_and_kept(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    backend = _Backend(ModelInfo("org/a@v1", None, ()), ModelInfo("org/b@v1", None, ()))
    db = label(tmp_path, backend, monkeypatch)
    assert (
        "warning: fake/test checkpoint changed during the run, from org/a@v1 to"
        " org/b@v1" in capsys.readouterr().err
    )
    with LabelStore(db) as store:
        [run] = store.runs()
        [p] = store.provenance()
    assert (run["checkpoint"], run["checkpoint_at_end"]) == ("org/a@v1", "org/b@v1")
    assert p.changed > 0 and p.mixed


# --- provenance, as spangloss score shows it ---------------------------------


def test_provenance_counts_rows_per_checkpoint(tmp_path: Path, monkeypatch) -> None:
    db = label(tmp_path, _Backend(ModelInfo("org/a@v1", None, ())), monkeypatch)
    with LabelStore(db) as store:
        [p] = store.provenance()
        rows = store._conn.execute(
            "SELECT count(*) FROM labels WHERE model = 'fake/test'"
        ).fetchone()[0]
    assert p.model == "fake/test"
    assert p.by_checkpoint == {"org/a@v1": rows}
    assert (p.unrecorded, p.changed, p.unfinished, p.mixed) == (0, 0, 0, False)


def test_rows_from_two_checkpoints_are_mixed(tmp_path: Path, monkeypatch) -> None:
    db = label(tmp_path, _Backend(ModelInfo("org/a@v1", None, ())), monkeypatch)
    # A second run, against another checkpoint, labels two more calls.
    monkeypatch.setattr(
        cli, "make_backend", lambda name: _Backend(ModelInfo(None, None, ()))
    )
    calls = tmp_path / "tool_calls.jsonl"
    args = ["label", "--backend", "kev", "--limit", "2", "--input", str(calls)]
    assert main([*args, "--db", str(db)]) == 0
    with LabelStore(db) as store:
        [p] = store.provenance()
    assert set(p.by_checkpoint) == {"org/a@v1", None}
    assert p.mixed


# A store as `label` wrote it before runs were recorded: no runs table, no run_id.
OLD_SCHEMA = """
CREATE TABLE labels (
    span_id VARCHAR NOT NULL, session_id VARCHAR, label VARCHAR NOT NULL,
    answer VARCHAR NOT NULL, p DOUBLE NOT NULL, basis VARCHAR NOT NULL,
    model VARCHAR NOT NULL, taxonomy VARCHAR NOT NULL,
    output_truncated BOOLEAN NOT NULL, input_tokens INTEGER, chunks INTEGER,
    labeled_at TIMESTAMP NOT NULL DEFAULT current_timestamp,
    PRIMARY KEY (span_id, label, model)
);
INSERT INTO labels (span_id, session_id, label, answer, p, basis, model, taxonomy,
    output_truncated)
VALUES ('s1', 'x', 'sec.effect', 'read', 0.9, 'args', 'kev/kev-latest', '0.4.0', false),
       ('s1', 'x', 'sec.reversibility', 'reversible', 1.0, 'args', 'derived', '0.4.0',
        false);
"""


def old_store(tmp_path: Path) -> Path:
    db = tmp_path / "old.duckdb"
    with duckdb.connect(str(db)) as conn:
        conn.execute(OLD_SCHEMA)
    return db


def test_score_reads_an_old_store_without_changing_it(tmp_path: Path, capsys) -> None:
    db = old_store(tmp_path)
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        '{"span_id": "s1", "taxonomy": "0.4.0", "basis": "args", "labels": {"sec.effect": "read"}}\n'
    )
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    args = ["score", "--db", str(db), "--gold", str(gold)]
    assert main([*args, "--input", str(tmp_path / "none.jsonl")]) == 0
    out = capsys.readouterr().out
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert "  kev/kev-latest: label rows: 1 from before runs were recorded\n" in out
    assert "derived" not in out.split("\n\n")[0]  # derived rows have no checkpoint


def test_score_shows_each_models_checkpoint(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    db = label(tmp_path, _Backend(ModelInfo("org/a@v1", None, ())), monkeypatch)
    with LabelStore(db) as store:
        [span_id] = store._conn.execute(
            "SELECT span_id FROM labels WHERE model = 'fake/test' LIMIT 1"
        ).fetchone()
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        f'{{"span_id": "{span_id}", "taxonomy": "0.4.0", "labels": {{}}}}\n'
    )
    capsys.readouterr()
    args = ["score", "--db", str(db), "--gold", str(gold)]
    assert main([*args, "--input", str(tmp_path / "tool_calls.jsonl")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Provenance (from the store's run log):\n")
    assert "  fake/test: label rows: " in out and " from org/a@v1\n" in out
    assert "warning" not in out


def test_score_without_a_store_says_to_label_first(tmp_path: Path, capsys) -> None:
    db = tmp_path / "labels.duckdb"
    gold = tmp_path / "gold.jsonl"
    gold.write_text('{"span_id": "s1", "taxonomy": "0.4.0", "labels": {}}\n')
    assert main(["score", "--db", str(db), "--gold", str(gold)]) == 1
    assert capsys.readouterr().err == (
        f"error: {db} not found; run spangloss label first\n"
    )
    assert not db.exists()
