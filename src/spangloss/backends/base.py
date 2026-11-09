"""The interface every decision-model backend implements."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from spangloss.parse.record import ParsedToolCall

# Probability per answer. A yes/no question answers {"yes": p, "no": 1 - p}.
Distribution = dict[str, float]


@dataclass(frozen=True, slots=True)
class QuestionSpec:
    """One question as sent to a backend. Holds only card text, never evidence."""

    kind: Literal["noul", "choice"]
    instructions: dict[str, Any]  # {"question": ..., "notes": [...]}
    # yes/no: {"yes": ..., "no": ...}; choice: {option: description}
    criteria: dict[str, str]


@dataclass(frozen=True, slots=True)
class AskResult:
    answers: dict[str, Distribution]
    input_tokens: int | None  # as the backend reports it; None if it doesn't


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """What a backend's server says about the model behind its model name, at one
    moment of a labeling run."""

    checkpoint: str | None  # the weights, when the server names them; else None
    card: dict[str, Any] | None  # the server's /v1/models entry, as it sent it
    served: tuple[str, ...]  # model names the answers so far reported, first seen first


class RequestRejected(Exception):
    """The backend refused this request. The call is skipped; the run continues."""


class EvidenceTooLong(RequestRejected):
    """The backend refused the evidence as too long for its context."""


class BackendUnavailable(Exception):
    """The backend can't be used at all: not configured, or not reachable. The run
    stops; the message says what to do."""


class Backend(Protocol):
    name: str
    model: str  # recorded as sec.model

    def screen(self, call: ParsedToolCall) -> str | None:
        """Why this call must not be sent to this backend, or None to send it."""
        ...

    def ask(
        self, evidence: dict[str, Any], questions: dict[str, QuestionSpec]
    ) -> AskResult:
        """Answer all questions about one piece of evidence in a single request."""
        ...

    def describe(self) -> ModelInfo:
        """The model behind this backend right now, for the run log. Raises
        BackendUnavailable when the server can't be reached."""
        ...
