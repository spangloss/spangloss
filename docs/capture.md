# Capturing Claude Code traces

Claude Code sends OpenTelemetry spans to a local Collector. The Collector writes every span to `data/raw/` and forwards a copy to Jaeger for browsing.

```
claude ──OTLP──▶ collector ──▶ data/raw/traces.jsonl
                    └────────▶ Jaeger UI (localhost:16686)
```

Captured spans include tool arguments and tool output, so they can contain secrets and personal data. `data/raw/` is git-ignored; never commit anything from it.

## 1. Start the stack

```
docker compose up -d        # or docker-compose up -d (e.g. Homebrew with Colima)
```

The Collector listens for OTLP on `localhost:4317` (gRPC) and `localhost:4318` (HTTP). All ports are bound to `127.0.0.1` only.

## 2. Start a traced session

In a new terminal, from the repository root:

```
source scripts/telemetry.sh
claude
```

The script enables Claude Code's beta tracing and turns on tool details (arguments, Bash commands) and tool content (output, recorded as the `tool.output` span event). It does not turn on user prompt text. Spans are exported every 5 seconds.

Only sessions started from that shell are traced. Run `telemetry_off` to turn it off again. Claude Code ignores `OTEL_*` variables in a repository's `.claude/settings.json`, so the shell is the place to set them.

## 3. View traces

- **Jaeger:** open http://localhost:16686, pick the `claude-code` service and select **Find Traces**. Each user prompt is a `claude_code.interaction` trace. Tool calls are `claude_code.tool` spans, with `claude_code.tool.blocked_on_user` and `claude_code.tool.execution` as children.
- **Raw file:** `data/raw/traces.jsonl` holds one OTLP JSON export request per line, and each line can contain many spans. The file rotates at 100 MB.

Jaeger keeps traces in memory, so they are gone after `docker compose down`. The raw file stays.

## Stop

```
docker compose down
```
