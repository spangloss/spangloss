"""Agent adapters that turn raw spans into ParsedToolCall records."""

from spangloss.parse.claude_code import parse_files as parse_claude_code
from spangloss.parse.record import ParsedToolCall

__all__ = ["ParsedToolCall", "parse_claude_code"]
