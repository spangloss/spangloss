# Agent image for seeded scenarios (docs/scenarios-design.md, section 4).
# Holds no credentials: the API key lives only in the per-run proxy container.
FROM debian:bookworm-slim
ARG CLAUDE_CODE_VERSION
RUN test -n "$CLAUDE_CODE_VERSION"
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq python3 procps \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 -s /bin/bash agent
USER agent
WORKDIR /home/agent
RUN curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_CODE_VERSION"
# Move the binary out of the home folder, which each run mounts fresh, and leave
# home empty so normal mode discovers nothing user-level.
USER root
RUN cp -L /home/agent/.local/bin/claude /usr/local/bin/claude \
    && rm -rf /home/agent/.local /home/agent/.cache /home/agent/.claude /home/agent/.claude.json
USER agent
ENV PATH=/usr/local/bin:/usr/bin:/bin
WORKDIR /workspace
