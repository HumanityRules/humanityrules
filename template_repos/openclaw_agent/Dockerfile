FROM ghcr.io/openclaw/openclaw:2026.4.10-slim

COPY --chown=node:node openclaw.json /home/node/.openclaw/openclaw.json
COPY --chown=node:node workspace/ /app/workspace/

EXPOSE 18789

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:18789/health || exit 1
