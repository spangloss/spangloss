"""Optional user configuration, read from spangloss.toml.

spangloss.toml is git-ignored, since server names can identify the user's own
systems. spangloss.example.toml shows the format.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = Path("spangloss.toml")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Config:
    # MCP servers the user wrote or controls. Their tool descriptions are first-party,
    # like built-in tools; every other server is third-party (TAXONOMY.md, trifecta).
    first_party_mcp_servers: frozenset[str] = field(default_factory=frozenset)


def load_config(path: Path = DEFAULT_CONFIG, required: bool = False) -> Config:
    """The config at path; defaults when it is absent, unless required."""
    if not path.exists():
        if required:
            raise ConfigError(f"{path} does not exist")
        return Config()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: not valid TOML: {e}") from e
    servers = raw.get("mcp", {}).get("first_party_servers", [])
    if not isinstance(servers, list) or not all(isinstance(s, str) for s in servers):
        raise ConfigError(f"{path}: mcp.first_party_servers must be a list of strings")
    return Config(first_party_mcp_servers=frozenset(servers))
