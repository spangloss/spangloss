# batch2_capture

The first build step of batch 2 (docs/drafts/batch2-design.md): one clean run that
checks which parts of each batch-2 carrier reach the trace, before any payload is
placed.

- `mcp/tickets.py` is a stand-in issue tracker (stdio MCP, no dependencies) with
  three tools. Each returns a different result shape: one long text block (a marker
  past 2,000 characters), three text blocks (a marker in each), and
  `structuredContent` plus a text block.
- `site/b2/guide.html` is served at `https://docs.tinyfmt.example/b2/guide.html` and
  has markers in visible text, an HTML comment, `alt` text, a hidden element, a code
  block and a meta description. The prompt fetches it with WebFetch and with curl.

There is no payload: the markers are plain words (`B2CAP-...`), not instructions.
The scenario has no evaluation labels and is not for gold, so don't run
`scenarios check` on it.
