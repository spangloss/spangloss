# Turn on Claude Code trace export to the local capture stack (docker-compose.yml).
#
#   source scripts/telemetry.sh      # then run `claude` in this same shell
#   telemetry_off                    # undo, without closing the shell
#
# Variable names follow https://code.claude.com/docs/en/monitoring-usage.
# Captured spans include tool arguments and tool output, which can hold secrets
# and personal data. They land in data/raw/, which is git-ignored.

if { [ -n "${ZSH_EVAL_CONTEXT:-}" ] && [ "$ZSH_EVAL_CONTEXT" = toplevel ]; } ||
  { [ -n "${BASH_SOURCE[0]:-}" ] && [ "${BASH_SOURCE[0]}" = "$0" ]; }; then
  echo "telemetry.sh must be sourced, not run: source scripts/telemetry.sh" >&2
  exit 1
fi

export CLAUDE_CODE_ENABLE_TELEMETRY=1
export CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1 # traces are a beta feature

export OTEL_TRACES_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp # tool_result events carry the tool arguments
export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

export OTEL_LOG_TOOL_DETAILS=1 # tool names, Bash commands, tool input
export OTEL_LOG_TOOL_CONTENT=1 # tool output, as the tool.output span event

telemetry_off() {
  unset CLAUDE_CODE_ENABLE_TELEMETRY CLAUDE_CODE_ENHANCED_TELEMETRY_BETA \
    OTEL_TRACES_EXPORTER OTEL_LOGS_EXPORTER OTEL_EXPORTER_OTLP_PROTOCOL OTEL_EXPORTER_OTLP_ENDPOINT \
    OTEL_LOG_TOOL_DETAILS OTEL_LOG_TOOL_CONTENT
  unset -f telemetry_off
  echo "Claude Code telemetry off for this shell."
}

echo "Claude Code traces and logs -> localhost:4317 (tool details and content on). Run 'claude' in this shell."
