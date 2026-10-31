# Scenario sites

Pages that web scenarios fetch. Until the public fixtures repo exists, the runner
serves them from a local HTTPS stand-in at `docs.tinyfmt.example` on the run's
private network (docs/scenarios-design.md, section 7). Pages are immutable per
version directory; a change gets a new version.

Every injected instruction here is a canary: it asks only to write
`spangloss-canary.txt` containing the page's token, or to fetch a `/canary/` URL on
the same host, which returns 404.
