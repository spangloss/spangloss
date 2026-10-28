# spangloss

Dr. Pangloss assumed everything was for the best. Spangloss checks.

Spangloss adds security labels to AI agent telemetry. It asks a decision model narrow, typed questions about each OpenTelemetry span — what the call does, what data it touches, whether a tool result contains instructions aimed at the agent — and writes the answers back as `sec.*` attributes with probabilities. Detections and audits can then run over labels instead of raw prompts.

**Status:** early development. The first public release, with a published label taxonomy and per-label evaluation results, is planned for late November 2026.
