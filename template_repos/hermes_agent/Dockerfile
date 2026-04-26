FROM ghcr.io/nesquena/hermes-webui:0.50.126

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker-cli git patch \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch v2026.4.16 https://github.com/NousResearch/hermes-agent.git \
        /opt/hermes-defaults/hermes-agent \
    && rm -rf /opt/hermes-defaults/hermes-agent/.git

COPY SOUL.md /opt/hermes-defaults/SOUL.md
COPY config.yaml.template /opt/hermes-defaults/config.yaml.template
COPY patches /opt/hermes-defaults/patches
COPY --chmod=755 entrypoint.sh /entrypoint.sh
COPY --chmod=755 start.sh /start.sh

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
