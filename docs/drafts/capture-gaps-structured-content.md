# Draft addition to docs/findings/capture-gaps.md: MCP structured results

Status: **draft for owner review**. Not yet in the published file. If accepted, it adds
one table row and the section below.

**Table row:**

| Gap | Where it shows | Effect on labels |
| --- | --- | --- |
| [An MCP result's text blocks are dropped when it has structured content](#an-mcp-results-text-blocks-are-dropped-when-it-has-structured-content) | MCP tools that return `structuredContent` | text in the dropped blocks reaches neither the agent nor the trace; nothing to label |

## An MCP result's text blocks are dropped when it has structured content

**What:** an MCP tool result can carry `content` (a list of blocks, such as text)
and `structuredContent` (a JSON object), and the spec lets a server send both. When
both are present, Claude Code passes on only `structuredContent`, serialised as JSON.
The text blocks go nowhere: they're not in the agent's own stream, and they're not in
the trace.

**How we observed it:** in the `batch2_capture` scenario run (2026-11-12),
`mcp__tickets__get_issue_meta` returned one text block with a marker and a
`structuredContent` object with a different marker. The span's output was the JSON
object alone (80 characters) and held only the structured marker. Neither the raw
traces, the raw logs nor the agent's stream-JSON held the text block's marker. Two
other tools on the same server returned text blocks only (one 3,198-character block;
three blocks), and both were captured in full.

**Effect:**
- **Not a gap between the trace and the agent.** The trace shows what the agent saw,
  so labels on these calls judge the right content.
- **Injected text in a dropped block can't act:** the agent never sees it. A scenario
  that places a payload there tests nothing, so batch-2 payloads go in text-only
  results or in a `structuredContent` field.
- **Not observed:** what happens for a server that sends both and whose text block is
  the one a user would read. Also, whether this holds for MCP clients other than
  Claude Code, and for other block types (images, resource links).
- Observed in one run of Claude Code 2.1.312. Recheck when the pinned version changes.
