from importlib.metadata import version

import spangloss


def test_version_matches_package_metadata() -> None:
    assert spangloss.__version__ == version("spangloss")
