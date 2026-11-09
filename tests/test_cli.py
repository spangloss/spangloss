"""What the CLI tells a user whose input file or backend is missing."""

from pathlib import Path
from typing import Any

import pytest
from typesafe_sdk import TypeSafeAPIConnectionError, TypeSafeAPITimeoutError

from spangloss import cli
from spangloss.backends.base import BackendUnavailable, QuestionSpec
from spangloss.backends.typesafe import TypeSafeBackend, jev_backend, kev_backend
from spangloss.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
TRACES = FIXTURES / "claude_code_traces.jsonl"
LOGS = FIXTURES / "claude_code_logs.jsonl"


def error_line(capsys: pytest.CaptureFixture[str]) -> str:
    lines = capsys.readouterr().err.strip().splitlines()
    assert len(lines) == 1, lines  # one line, no traceback
    return lines[0]


def parsed(tmp_path: Path) -> Path:
    out = tmp_path / "tool_calls.jsonl"
    assert main(["parse", str(TRACES), "--logs", str(LOGS), "-o", str(out)]) == 0
    return out


def test_parse_missing_traces(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "traces.jsonl"
    assert main(["parse", str(missing)]) == 1
    assert error_line(capsys) == (
        f"error: {missing} not found; capture a session first (docs/capture.md),"
        " or try tests/fixtures/claude_code_traces.jsonl"
    )


def test_parse_missing_logs(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "logs.jsonl"
    assert main(["parse", str(TRACES), "--logs", str(missing)]) == 1
    assert error_line(capsys) == (
        f"error: {missing} not found; pass the logs file captured with the traces"
    )


def test_label_missing_calls(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "tool_calls.jsonl"
    db = tmp_path / "labels.duckdb"
    args = ["label", "--backend", "kev", "--limit", "1", "--input", str(missing)]
    assert main([*args, "--db", str(db)]) == 1
    assert (
        error_line(capsys) == f"error: {missing} not found; run spangloss parse first"
    )


def test_rules_missing_calls(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "tool_calls.jsonl"
    db = tmp_path / "labels.duckdb"
    assert main(["rules", "--input", str(missing), "--db", str(db)]) == 1
    assert (
        error_line(capsys) == f"error: {missing} not found; run spangloss parse first"
    )


def test_rules_before_any_labels(tmp_path: Path, capsys) -> None:
    calls = parsed(tmp_path)
    capsys.readouterr()
    db = tmp_path / "labels.duckdb"
    assert main(["rules", "--input", str(calls), "--db", str(db)]) == 1
    assert error_line(capsys) == (
        f"error: no labels in {db} to run rules over; run spangloss label first"
    )


def test_export_sheet_missing_calls(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "tool_calls.jsonl"
    out = tmp_path / "sheet.csv"
    args = ["export-sheet", "--limit", "1", "--input", str(missing), "--out", str(out)]
    assert main(args) == 1
    assert (
        error_line(capsys) == f"error: {missing} not found; run spangloss parse first"
    )


def test_import_sheet_missing_calls(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "tool_calls.jsonl"
    sheet = tmp_path / "sheet.csv"
    sheet.write_text("span_id\n", encoding="utf-8")
    assert main(["import-sheet", str(sheet), "--input", str(missing)]) == 1
    assert (
        error_line(capsys) == f"error: {missing} not found; run spangloss parse first"
    )


def test_import_sheet_missing_sheet(tmp_path: Path, capsys) -> None:
    calls = parsed(tmp_path)
    capsys.readouterr()
    missing = tmp_path / "sheet.csv"
    args = ["import-sheet", str(missing), "--input", str(calls)]
    assert main([*args, "--gold", str(tmp_path / "gold.jsonl")]) == 1
    assert error_line(capsys) == (
        f"error: {missing} not found; write one with spangloss export-sheet and fill"
        " it in"
    )


class _Unreachable:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def system_one(self, **_: Any) -> Any:
        raise self.error

    @property
    def models(self) -> Any:
        return self

    def list(self) -> Any:
        raise self.error

    def close(self) -> None:
        pass


def test_label_kev_unreachable(tmp_path: Path, capsys, monkeypatch) -> None:
    calls = parsed(tmp_path)
    capsys.readouterr()
    kev = kev_backend()
    kev.close()
    kev._client = _Unreachable(TypeSafeAPIConnectionError("refused"))  # type: ignore[assignment]
    monkeypatch.setattr(cli, "make_backend", lambda name: kev)
    args = ["label", "--backend", "kev", "--limit", "1", "--input", str(calls)]
    assert main([*args, "--db", str(tmp_path / "labels.duckdb")]) == 1
    assert error_line(capsys) == (
        "error: Kev not reachable at http://127.0.0.1:8009 (start a Kev server; see"
        " README) or use --backend jev"
    )


def test_a_timeout_is_not_reported_as_unreachable() -> None:
    client = _Unreachable(TypeSafeAPITimeoutError(1.0))
    backend = TypeSafeBackend("kev", "kev-latest", client)  # type: ignore[arg-type]
    question = {"q": QuestionSpec("noul", {"question": "Q?"}, {"yes": "Y", "no": "N"})}
    with pytest.raises(TypeSafeAPITimeoutError):
        backend.ask({"call": {}}, question)


def test_jev_without_a_key_names_the_file_and_variable(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    with pytest.raises(BackendUnavailable) as e:
        jev_backend(env_file=env)
    assert str(e.value) == (
        f"TYPESAFE_API_KEY is not set in {env} (add a line TYPESAFE_API_KEY=… with a"
        " key from https://console.typesafe.ai/keys) or use --backend kev"
    )


def test_label_jev_without_a_key(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    assert main(["label", "--backend", "jev", "--limit", "1"]) == 1
    line = error_line(capsys)
    assert line.startswith("error: TYPESAFE_API_KEY is not set in .env (")
