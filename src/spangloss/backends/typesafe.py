"""Kev (local) and Jev (hosted) through the TypeSafe SDK.

Both speak TypeSafe's System One API. The evidence goes in `state`; the card's
question and notes go in each question's `instructions`, and its answer
definitions in `criteria`. The two never mix.
"""

import logging
from pathlib import Path
from typing import Any

from typesafe_sdk import (
    Choice,
    Noul,
    TypeSafeAPIConnectionError,
    TypeSafeAPITimeoutError,
    TypeSafeBadRequestError,
    TypeSafeClient,
    TypeSafeUnprocessableEntityError,
)
from typesafe_sdk.constants import DEFAULT_BASE_URL

from spangloss.backends.base import (
    AskResult,
    BackendUnavailable,
    Distribution,
    EvidenceTooLong,
    QuestionSpec,
    RequestRejected,
)
from spangloss.backends.safety import screen_for_hosted
from spangloss.parse.record import ParsedToolCall
from spangloss.scenarios.fakes import read_fakes
from spangloss.scenarios.provenance import verified_scenario_spans

KEV_URL = "http://127.0.0.1:8009"
KEV_MODEL = "kev-latest"
JEV_MODEL = "jev-latest"
JEV_KEY = "TYPESAFE_API_KEY"
JEV_KEY_URL = "https://console.typesafe.ai/keys"
TIMEOUT_SECONDS = 120.0  # long evidence on a laptop GPU can take tens of seconds

# Request and response bodies are logged at debug level by the SDK; keep them out.
logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)


class TypeSafeBackend:
    def __init__(
        self, name: str, model: str, client: TypeSafeClient, unreachable: str = ""
    ) -> None:
        self.name = name
        self.model = f"{name}/{model}"
        self._model_name = model
        self._client = client
        # What to tell the user when the server can't be reached at all.
        self._unreachable = unreachable or f"{name} not reachable"

    def screen(self, call: ParsedToolCall) -> str | None:
        return None

    def ask(
        self, evidence: dict[str, Any], questions: dict[str, QuestionSpec]
    ) -> AskResult:
        try:
            response = self._client.system_one(
                state=evidence,
                questions={qid: _sdk_question(q) for qid, q in questions.items()},
                model=self._model_name,
            )
        # Error text is not passed on: it may quote the request.
        except TypeSafeAPITimeoutError:
            raise  # a slow answer, not a missing server
        except TypeSafeAPIConnectionError as e:
            raise BackendUnavailable(self._unreachable) from e
        except TypeSafeUnprocessableEntityError as e:
            # Kev answers 422 for a state over its token limit.
            raise EvidenceTooLong(f"{self.name} rejected the request (422)") from e
        except TypeSafeBadRequestError as e:
            raise RequestRejected(f"{self.name} rejected the request (400)") from e
        answers: dict[str, Distribution] = {}
        for qid, q in questions.items():
            answer = response.answers[qid]
            if q.kind == "noul":
                p = float(answer.noul)  # type: ignore[union-attr]
                answers[qid] = {"yes": p, "no": 1.0 - p}
            else:
                answers[qid] = dict(answer.probabilities)  # type: ignore[union-attr]
        usage = getattr(response, "usage", None)
        return AskResult(answers, getattr(usage, "input_tokens", None))

    def close(self) -> None:
        self._client.close()


class JevBackend(TypeSafeBackend):
    """Hosted: refuses calls that could carry secrets off the machine. Calls from a
    verified scenario run are screened only for secrets outside the fakes list."""

    def __init__(
        self,
        name: str,
        model: str,
        client: TypeSafeClient,
        fakes: frozenset[str] | None = None,
        scenario_spans: frozenset[str] | None = None,
        unreachable: str = "",
    ) -> None:
        super().__init__(name, model, client, unreachable)
        self._fakes = read_fakes() if fakes is None else fakes
        self._scenario_spans = (
            verified_scenario_spans() if scenario_spans is None else scenario_spans
        )

    def screen(self, call: ParsedToolCall) -> str | None:
        return screen_for_hosted(
            call, self._fakes, scenario=call.span_id in self._scenario_spans
        )


def kev_backend(base_url: str = KEV_URL, model: str = KEV_MODEL) -> TypeSafeBackend:
    # An explicit key and URL, so the Jev key and TYPESAFE_* variables never apply here.
    # Kev accepts any key unless KEV_API_KEY is set on the server. It can't read
    # zstd request bodies, which the SDK sends by default on Python 3.14+.
    client = TypeSafeClient(
        api_key="local",  # pragma: allowlist secret
        base_url=base_url,
        model=model,
        timeout=TIMEOUT_SECONDS,
        compression=False,
    )
    return TypeSafeBackend(
        "kev",
        model,
        client,
        unreachable=f"Kev not reachable at {base_url} (start a Kev server; see README)"
        " or use --backend jev",
    )


def jev_backend(env_file: Path = Path(".env"), model: str = JEV_MODEL) -> JevBackend:
    key = read_env_value(env_file, JEV_KEY)
    if not key:
        raise BackendUnavailable(
            f"{JEV_KEY} is not set in {env_file} (add a line {JEV_KEY}=… with a key"
            f" from {JEV_KEY_URL}) or use --backend kev"
        )
    client = TypeSafeClient(
        api_key=key, base_url=DEFAULT_BASE_URL, model=model, timeout=TIMEOUT_SECONDS
    )
    return JevBackend(
        "jev",
        model,
        client,
        unreachable=f"Jev not reachable at {DEFAULT_BASE_URL} (check your network)",
    )


def read_env_value(path: Path, key: str) -> str | None:
    """Read one KEY=value from a .env file without touching os.environ."""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            return value.strip().strip("'\"") or None
    return None


def _sdk_question(q: QuestionSpec) -> Noul | Choice:
    if q.kind == "noul":
        return Noul(
            instructions=q.instructions,
            criteria={"true": q.criteria["yes"], "false": q.criteria["no"]},
        )
    return Choice(instructions=q.instructions, criteria=dict(q.criteria))
