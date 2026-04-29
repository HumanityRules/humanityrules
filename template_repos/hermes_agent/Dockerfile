FROM ghcr.io/nesquena/hermes-webui:0.50.236

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker-cli git patch \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch v2026.4.23 https://github.com/NousResearch/hermes-agent.git \
        /opt/hermes-defaults/hermes-agent \
    && rm -rf /opt/hermes-defaults/hermes-agent/.git

COPY SOUL.md /opt/hermes-defaults/SOUL.md
COPY config.yaml.template /opt/hermes-defaults/config.yaml.template
COPY patches /opt/hermes-defaults/patches
COPY --chmod=755 entrypoint.sh /entrypoint.sh
COPY --chmod=755 start.sh /start.sh

# WebUI lives in ephemeral image layers at /apptoo/, so DOH's WebUI patches
# are applied at build time (not boot, unlike the agent patches on EFS).
# See patches-webui/README.md for per-patch rationale. compileall after the
# patch run overwrites any .pyc the base image shipped so stale bytecode
# can't shadow the patched sources at runtime.
COPY patches-webui /tmp/patches-webui
RUN set -eu; \
    for p in /tmp/patches-webui/*.patch; do \
        [ -f "$p" ] || continue; \
        echo "[patches-webui] applying $(basename "$p")"; \
        patch -p1 -F 0 -d /apptoo -i "$p"; \
    done; \
    python3 -m compileall -q /apptoo || true; \
    rm -rf /tmp/patches-webui

# Append platform dependencies to the webui's requirements.txt so they're
# installed by `hermeswebui_init.bash` before `python server.py` runs — this
# avoids a race where tools.mcp_tool imports `mcp` at module-load time and
# caches _MCP_AVAILABLE=False if the package isn't there yet, and where
# gateway.run imports slack-bolt at startup.
#   mcp          → enables MCP server auto-discovery (hermes-agent[mcp])
#   boto3        → enables the Bedrock LLM provider (hermes-agent[bedrock])
#   slack-{bolt,sdk} → gateway Slack adapter (hermes-agent[slack])
RUN test -f /apptoo/requirements.txt \
    && cat >> /apptoo/requirements.txt <<'EOF'
mcp>=1.2.0,<2
boto3>=1.35.0,<2
slack-bolt>=1.18.0,<2
slack-sdk>=3.27.0,<4
EOF

# /workspace is a real top-level EFS mount, sibling to the agent home —
# same data is mounted into the docker-dind sidecar so tool runs and the
# hermes parent see one consistent filesystem.
ENV HERMES_WEBUI_STATE_DIR=/home/hermeswebui/.hermes/webui-mvp
ENV HERMES_WEBUI_DEFAULT_WORKSPACE=/workspace
ENV HERMES_WEBUI_SKIP_ONBOARDING=1

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8787/health || exit 1

USER hermeswebui

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/start.sh"]
