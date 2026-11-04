"""One committed list of fake secrets (docs/scenarios-design.md, section 8).

scenarios/FAKE_SECRETS.txt lists every fake secret value used in scenario fixtures,
one per line. verify_fakes scans paths with detect-secrets and reports any hit
whose value is not on the list, so a real secret can't hide among the fakes. The
standard detect-secrets hook skips scenario fixtures; this check replaces it there.

scenarios/TEST_VALUES.txt lists test-mode values (labeled data_class none) in the
same format. verify-fakes accepts them too; nothing else treats them as fakes.
"""

from dataclasses import dataclass
from pathlib import Path

from detect_secrets import SecretsCollection
from detect_secrets.settings import default_settings

FAKES_FILE = Path("scenarios/FAKE_SECRETS.txt")
TEST_VALUES_FILE = Path("scenarios/TEST_VALUES.txt")


@dataclass(frozen=True, slots=True)
class Unlisted:
    path: str
    line: int
    kind: str


def read_fakes(path: Path = FAKES_FILE) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    return frozenset(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def read_test_values(path: Path = TEST_VALUES_FILE) -> frozenset[str]:
    return read_fakes(path)


def verify_fakes(paths: list[Path], fakes: frozenset[str]) -> list[Unlisted]:
    """Every detect-secrets hit in paths whose value is not a listed fake."""
    files = [
        f
        for p in paths
        for f in ([p] if p.is_file() else sorted(p.rglob("*")))
        if f.is_file()
    ]
    found: list[Unlisted] = []
    with default_settings():
        collection = SecretsCollection()
        for f in files:
            collection.scan_file(str(f))
        for filename, secret in collection:
            if secret.secret_value not in fakes:
                found.append(Unlisted(filename, secret.line_number, secret.type))
    return sorted(found, key=lambda u: (u.path, u.line))
