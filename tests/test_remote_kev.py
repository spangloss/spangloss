"""spangloss label --kev-url: a Kev server elsewhere, labeled under its own name."""

from pathlib import Path
from typing import Any

import pytest

from spangloss import cli
from spangloss.backends.base import BackendUnavailable
from spangloss.backends.support import unsupported_labels
from spangloss.backends.typesafe import (
    JevBackend,
    TypeSafeBackend,
    is_loopback,
    remote_kev_backend,
)
from spangloss.cli import main
from spangloss.store import LabelStore

FIXTURES = Path(__file__).parent / "fixtures"
TRACES = FIXTURES / "claude_code_traces.jsonl"
LOGS = FIXTURES / "claude_code_logs.jsonl"
CARD = {"name": "kev-latest", "run": "jaredpalmer/kev-9b@v1.0"}


class _Obj:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


class _Client:
    """A Kev server: answers every question and lists one model."""

    @property
    def models(self) -> "_Client":
        return self

    def list(self) -> Any:
        return _Obj(raw_http_response=_Obj(json=lambda: {"models": [CARD]}))

    def system_one(self, state: Any, questions: dict[str, Any], model: str) -> Any:
        answers = {
            qid: _Obj(noul=0.3)
            if hasattr(q, "criteria") and set(q.criteria) == {"true", "false"}
            else _Obj(probabilities={o: 1 / len(q.criteria) for o in q.criteria})
            for qid, q in questions.items()
        }
        return _Obj(model=model, answers=answers, usage=_Obj(input_tokens=10))

    def close(self) -> None:
        pass


def _parsed(tmp_path: Path) -> Path:
    out = tmp_path / "tool_calls.jsonl"
    assert main(["parse", str(TRACES), "--logs", str(LOGS), "-o", str(out)]) == 0
    return out


def _label(tmp_path: Path, *extra: str) -> int:
    calls = _parsed(tmp_path)
    args = ["label", "--backend", "kev", "--limit", "3", "--input", str(calls)]
    return main([*args, *extra])


@pytest.mark.parametrize(
    "extra, error",
    [
        (["--kev-url", "http://k"], "--kev-url needs --name"),
        (["--name", "kev-9b"], "--name and --kev-key-var need --kev-url"),
        (["--kev-key-var", "K"], "--name and --kev-key-var need --kev-url"),
        (["--kev-url", "http://k", "--name", "kev"], "--name 'kev': use lowercase"),
        (["--kev-url", "http://k", "--name", "Kev 9B"], "--name 'Kev 9B': use"),
        (["--kev-url", "http://k", "--name", "derived"], "--name 'derived': use"),
        (["--kev-url", "http://k", "--name", "kev-9b"], "--kev-url needs a --db of"),
    ],
)
def test_remote_kev_options_are_checked_before_anything_runs(
    tmp_path: Path, monkeypatch, capsys, extra: list[str], error: str
) -> None:
    def no_backend(*a: Any) -> None:
        raise AssertionError("no backend should be made")

    monkeypatch.setattr(cli, "make_remote_kev", no_backend)
    monkeypatch.setattr(cli, "make_backend", no_backend)
    assert _label(tmp_path, *extra) == 1
    assert (
        capsys.readouterr().err.strip().splitlines()[-1].startswith(f"error: {error}")
    )


def test_default_db_is_refused_even_when_named(tmp_path: Path, capsys) -> None:
    extra = ["--kev-url", "http://k", "--name", "kev-9b", "--db", str(cli.DEFAULT_DB)]
    assert _label(tmp_path, *extra) == 1
    assert "not the default store" in capsys.readouterr().err


def test_remote_kev_is_its_own_model_and_answers_injection() -> None:
    backend = remote_kev_backend("http://127.0.0.1:9000", "kev-9b", None)
    assert backend.model == "kev-9b/kev-latest"
    assert "sec.injection" in unsupported_labels("kev/kev-latest")
    assert unsupported_labels(backend.model) == frozenset()


def test_a_server_off_this_machine_gets_the_hosted_screen() -> None:
    local = remote_kev_backend("http://localhost:9000", "kev-9b", None)
    remote = remote_kev_backend("https://ws--kev-api.modal.run", "kev-9b", "k")
    assert type(local) is TypeSafeBackend
    assert isinstance(remote, JevBackend)
    assert is_loopback("http://127.0.0.1:8009") and is_loopback("http://[::1]:1")
    assert not is_loopback("https://127.example.com")
    assert not is_loopback("https://localhost.example.com")


def test_the_key_comes_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(BackendUnavailable, match="KEV_9B_KEY is not set in .env"):
        cli.make_remote_kev("https://x.modal.run", "kev-9b", "KEV_9B_KEY")
    (tmp_path / ".env").write_text("KEV_9B_KEY=abc\n")
    seen: list[Any] = []
    monkeypatch.setattr(
        "spangloss.backends.typesafe.remote_kev_backend",
        lambda url, name, key: seen.append(key),
    )
    cli.make_remote_kev("https://x.modal.run", "kev-9b", "KEV_9B_KEY")
    assert seen == ["abc"]


def test_label_writes_the_remote_kev_into_its_own_store(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    backend = remote_kev_backend("http://127.0.0.1:9000", "kev-9b", None)
    backend._client.close()
    backend._client = _Client()  # type: ignore[assignment]
    monkeypatch.setattr(cli, "make_remote_kev", lambda url, name, key: backend)
    db = tmp_path / "kev9b.duckdb"
    extra = ["--kev-url", "http://127.0.0.1:9000", "--name", "kev-9b", "--db", str(db)]
    assert _label(tmp_path, *extra) == 0
    err = capsys.readouterr().err
    assert "kev-9b/kev-latest: run " in err
    assert "checkpoint jaredpalmer/kev-9b@v1.0" in err
    assert "unsupported" not in err
    with LabelStore(db, read_only=True) as store:
        assert store.models() == ["kev-9b/kev-latest"]
        [run] = store.runs()
    assert run["checkpoint"] == "jaredpalmer/kev-9b@v1.0"
