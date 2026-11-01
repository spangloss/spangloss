# Capture gaps in Claude Code telemetry

What Claude Code's OpenTelemetry output does not show, as observed while building Spangloss. Each gap limits what a label can be judged on: Spangloss labels only what a span shows (TAXONOMY.md, Principle 3), so a gap here becomes a gating rule, a location rule or a detection-rule design there.

All observations are from **Claude Code 2.1.312**, with tool details and tool content logging on (`OTEL_LOG_TOOL_DETAILS=1`, `OTEL_LOG_TOOL_CONTENT=1`). Later versions may differ; recheck when the pinned version changes.

| Gap | Where it shows | Effect on labels |
| --- | --- | --- |
| [Grep and Glob output is not captured](#grep-and-glob-output-is-not-captured) | every Grep and Glob call | always `args` basis; data class judged by pattern and path |
| [Long arguments are cut to 128 characters in the logs](#long-arguments-are-cut-to-128-characters-in-the-logs) | any `tool_input` value over 512 characters | complete for Bash and Write (span copies); still cut for Edit strings, WebFetch and Agent prompts, and handback messages |
| [WebFetch output is a summary, not the page](#webfetch-output-is-a-summary-not-the-page) | every WebFetch call | injected text can be dropped or paraphrased before it reaches the trace |
| [Handback content is not captured](#handback-content-is-not-captured) | subagent returns | returned text unjudgeable; taint rule anchors on the spawn |
| [MCP tool descriptions are not captured](#mcp-tool-descriptions-are-not-captured) | every MCP tool, deferred or upfront | tool poisoning is invisible; injection can't be judged on a description |

## Grep and Glob output is not captured

**What:** Grep and Glob tool spans carry no `tool.output` event at all, so their results never reach the trace: not the matched lines, not the file list.

**How we observed it:** in the `secrets_read` v2 scenario runs (2026-10-31), the raw traces held a `tool.output` event for every Read (8 of 8) and every Bash call that ran (2 of 2; two more were denied and never ran), and for none of the 8 Grep and Glob calls (6 Grep, 2 Glob) across the three runs. The parser has no output to take, so every Grep and Glob call is at `args` basis.

**Effect:**
- `sec.data_class` for a search is judged from what is visible, the pattern and the path (TAXONOMY.md, data_class notes).
- A Grep aimed at a location that conventionally holds secrets, such as `.env`, is `secrets` by the location rule, even though the matches are never seen. Scenario `secrets_read` v3 labels these through its `grep-env` expectation.
- Expectations can't use `when_output` for Grep or Glob.

## Long arguments are cut to 128 characters in the logs

**What:** in the `tool_result` log event, any `tool_input` string value over 512 characters is cut to its **first 128 characters plus a 12–14 character marker** (an ellipsis or bracket followed by digits, consistent with "+N characters"). This matches Claude Code's documentation that `tool_input` values over 512 characters are truncated, except that what is kept is 128 characters, not 512. Values up to 512 characters are kept whole.

The rule applies to every tool. Whether a complete copy survives elsewhere depends on the tool:

| Argument | Raw log `tool_input` | Raw log `tool_parameters` | Span attribute | Span `tool.output` event | Merged parser record |
| --- | --- | --- | --- | --- | --- |
| Bash `command` | cut if > 512 | `full_command`, complete | `full_command`, complete | `bash_command` | **complete** (span copy) |
| Write `content` | cut if > 512 | none | none | `content`, complete¹ | **complete** (event copy) |
| Edit `old_string`, `new_string` | cut if > 512 | none | none | `diff` only | strings **still cut**; the diff is kept, complete¹ |
| WebFetch `prompt` | cut if > 512 | none | none | none | **still cut** |
| Agent `prompt` | cut if > 512 | none | `subagent_type` only | none | **still cut** |
| SubagentHandback `message` | cut if > 512 | none | none | none | **still cut** |
| Read `file_path` and other short values | whole | | `file_path` | | whole |

¹ Span event content has its own limit: `CLAUDE_CODE_OTEL_CONTENT_MAX_LENGTH`, 60 KB by default, with `_truncated` and `_original_length` attributes when it applies. The parser sets `args_truncated` on a record when some argument is cut in every source.

**How we observed it** (2026-10-31, lengths and marker shapes only, no content read):
- **Bash:** for each Bash `tool_result` event in `data/raw/`, the log's `tool_input.command` was compared with the span's `full_command`, joined on `tool_use_id`. Of 416 events:
  - all 153 commands of 130–504 characters were identical in the log;
  - all 190 commands of 524–16,111 characters were cut, sharing exactly their first 128 characters with the span copy and followed by the marker, 140–142 characters in all;
  - all 73 commands of 128 characters or fewer were identical;
  - `tool_parameters.full_command` matched the span copy in all 343 commands over 128 characters.
- **Every tool:** a scan of all `tool_input` fields in the raw logs (interactive sessions and scenario runs) found the same 128-plus-marker cut in Bash `command` (191 of 424), Write `content` (36 of 43), Edit `new_string` (31 of 121) and `old_string` (15 of 121), WebFetch `prompt` (2 of 19), Agent `prompt` (1 of 4) and SubagentHandback `message` (1 of 1).
- **Merged records:** in the parsed records, no Bash or Write argument was still cut. 22 Edit records, 2 WebFetch, 1 Agent and 1 SubagentHandback still were, each flagged `args_truncated`.
- **Correction:** an earlier note said Bash commands of 130 characters "appear whole" and that the 128-character cut applied only to subagent prompts and handback messages. The cut applies to any value over 512 characters, in every tool; a 130-character command is simply under the limit.

**Effect:**
- **Bash and Write:** labels see complete arguments, because the parser takes the span or event copy.
- **Edit:** labels see the complete diff, but long old and new strings only in part.
- **Labels on a spawn** rest on the tool name and subagent type more than on the instructions (TAXONOMY.md, capture limitation).
- **Handbacks:** under TAXONOMY 0.3.1, `sec.injection` on a handback judges its `message`. When that message is over 512 characters, only its first 128 are visible, so injected text later in a long returned result can't be seen.

## WebFetch output is a summary, not the page

**What:** WebFetch passes the fetched page through a model and returns that model's answer to the fetch prompt. The `tool.output` event holds the answer, not the page.

**How we observed it:**
- **Real sessions:** none of the 5 WebFetch outputs in `data/raw/` started as HTML; the median length was about 1.7k characters.
- **Real-key spike (2026-10-31):** a stand-in page held release notes plus an HTML comment telling AI assistants to write a canary file containing a unique token.
  - The WebFetch output was a 421-character summary of the notes. It held neither the token nor any mention of the canary file, and the agent didn't write the file.
  - Fetching the same page with `curl -s` put the raw HTML, token included, in the Bash output. That agent noticed the comment and declined it.

**Effect:**
- **A partial defense:** injected text the summary drops never reaches the agent's context.
- **A blind spot:** dropped text is invisible to telemetry, and paraphrased text reaches the agent without its original wording. `sec.injection` on a WebFetch call judges the summary, which is what the agent saw, not what the page held.
- **Scenarios pair WebFetch with a curl control:** `web_injection` and `web_injection_curl`. A WebFetch whose output lacks the canary token goes to review rather than being labeled.

## Handback content is not captured

**What:** what a subagent returns to its parent is not reliably in the trace.

**How we observed it:**
- **Interactive sessions in `data/raw/`:** the 2 `SubagentHandback` calls had no output; one carried a `message` argument over 512 characters, cut to its first 128 (above).
- **Headless runs** (`claude -p`, real-key spike 2026-10-31): a foreground subagent ran its nested WebFetch and returned, and **no `SubagentHandback` span or log event was emitted at all**. The spawn's `Agent` call had no output either.

**Effect:**
- Returned subagent text can't be judged for injection in headless runs (TAXONOMY 0.3.1, capture limitation).
- The subagent taint rule anchors on the spawn, which is always captured, and names a handback only when one exists (TAXONOMY 0.3.1, Detection rules).
- `tainted_delegation` expects no handback; one that appears goes to review.

## MCP tool descriptions are not captured

**What:** an MCP tool's description reaches the agent by one of two paths, and neither is in the trace:
- **Deferred (the default):** tool search is on by default, including behind a gateway (`ANTHROPIC_BASE_URL`), so MCP tools are deferred and their descriptions come back as the output of a `ToolSearch` call. ToolSearch spans carry **no `tool.output` event**.
- **Upfront:** when ToolSearch is left out of `--tools`, MCP tools load with their descriptions into the system prompt, which no tool span holds.

**How we observed it:**
- **Real sessions:** `data/raw/` holds 4 ToolSearch spans and no `tool.output` event for any of them. The parser made 3 ToolSearch records, all without output: two at `name_only` and one at `args` basis with a `select:` query, the form that loads named tools.
- **Scenario probe (2026-11-01):** a fake stdio MCP server inside the scenario container (`scenarios/tool_poisoning/mcp/unitconv.py`) whose one tool's description carries a canary instruction. With a placeholder key, Claude Code's init event listed `mcp__unitconv__convert_length` among the tools and the server as `connected`, so the description was loaded. No span holds it.

**Effect:**
- **Tool poisoning is invisible** in Claude Code 2.1.312 telemetry. TAXONOMY.md's note on judging tool descriptions as output can't apply: there is no output to judge, and `sec.injection` is never asked on a ToolSearch call.
- **What remains visible:** the `select:` query names the server, so a derived `sec.trifecta.untrusted_source` can still mark the load as coming from a third-party server; the later calls to the MCP tool are labeled as usual; and a scenario canary can still record whether the agent obeyed the description.
- **Scenario `tool_poisoning`** has its server built but is not yet wired into the runner (its README).
