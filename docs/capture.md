# Capturing Claude Code traces

Claude Code sends OpenTelemetry spans and log events to a local Collector. The Collector writes both to `data/raw/` and forwards a copy of the spans to Jaeger for browsing.

```
claude ──OTLP──▶ collector ──▶ data/raw/traces.jsonl   (spans)
                    ├────────▶ data/raw/logs.jsonl     (log events)
                    └────────▶ Jaeger UI (localhost:16686)
```

Both are needed. Tool spans carry the tool name, timing, permission decision and output, but full tool arguments arrive only in the `tool_result` log event (`tool_input` and `tool_parameters`). The parser joins the two by `tool_use_id`.

Captured spans and logs include tool arguments and tool output, so they can contain secrets and personal data. `data/raw/` is git-ignored; never commit anything from it.

## What the Collector removes

Claude Code attaches the signed-in account's identity to every span and log event. The Collector deletes these attributes, from both record and resource attributes, before writing to disk or forwarding to Jaeger:

- `user.email`
- `user.account_uuid`
- `user.account_id`
- `organization.id`

They identify a person and an organization, and no label or rule needs them. It keeps `user.id` and `session.id`, which group spans into sessions without naming anyone.

This only removes those attribute keys. Personal data inside tool arguments or output (an email address in `git log`, for example) still reaches `data/raw/` and must be redacted before anything goes into the evaluation set. The Collector cannot reach attributes on span events, so the parser drops the same keys there too.

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

The script enables Claude Code's beta tracing and log events, and turns on tool details (arguments and Bash commands, in the `tool_result` log event) and tool content (output, recorded as the `tool.output` span event). It does not turn on user prompt text. Spans and log events are exported every 5 seconds.

Only sessions started from that shell are traced. Run `telemetry_off` to turn it off again. Claude Code ignores `OTEL_*` variables in a repository's `.claude/settings.json`, so the shell is the place to set them.

## 3. View traces

- **Jaeger:** open http://localhost:16686, pick the `claude-code` service and select **Find Traces**. Each user prompt is a `claude_code.interaction` trace. Tool calls are `claude_code.tool` spans, with `claude_code.tool.blocked_on_user` and `claude_code.tool.execution` as children.
- **Raw files:** `data/raw/traces.jsonl` and `data/raw/logs.jsonl` hold one OTLP JSON export request per line, and each line can contain many spans or log records. Each file rotates at 100 MB.

Jaeger keeps traces in memory, so they are gone after `docker compose down`. The raw files stay.

## Stop

```
docker compose down
```
