"""Labels a backend does not answer.

A backend's labels are only as good as its evaluation. Where a backend's answers
on a label are not usable, the label is reported as unsupported for it: its card
is not sent, no row is written, and scoring shows the label as unsupported
instead of counting it missing. An absent label never means "no" (TAXONOMY.md,
Principle 2), so detection rules must treat it like a label that wasn't asked.

- Kev, sec.injection (v0): recall 3/11 on the scenario gold, all of it the curl
  control, with no threshold that separates its answers
  (docs/findings/known-limitations.md).

Keyed by backend name, the part of sec.model before the "/". A Kev server labeled
under its own name (spangloss label --kev-url URL --name NAME) has no entry here,
so it is asked every label; the CLI keeps its labels out of the default store.
"""

BACKENDS = ("kev", "jev")  # the backends `spangloss label --backend` offers

UNSUPPORTED_LABELS: dict[str, frozenset[str]] = {
    "kev": frozenset({"sec.injection"}),
}


def unsupported_labels(model: str) -> frozenset[str]:
    """The labels the backend behind this sec.model value does not answer."""
    return UNSUPPORTED_LABELS.get(model.split("/", 1)[0], frozenset())
