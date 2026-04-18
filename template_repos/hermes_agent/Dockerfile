FROM ghcr.io/nesquena/hermes-webui:latest

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends git patch \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/NousResearch/hermes-agent.git \
        /opt/hermes-defaults/hermes-agent

COPY SOUL.md /opt/hermes-defaults/SOUL.md
COPY config.yaml.template /opt/hermes-defaults/config.yaml.template
COPY patches /opt/hermes-defaults/patches
COPY --chmod=755 entrypoint.sh /entrypoint.sh
COPY --chmod=755 start.sh /start.sh

# Hermes uses /workspace as the default cwd for terminal commands and file tools.
# Symlink it into the EFS-backed home directory so agent work output persists.
RUN rm -rf /workspace && ln -s /home/hermeswebui/.hermes/workspace /workspace

ENV HERMES_WEBUI_STATE_DIR=/home/hermeswebui/.hermes/webui-mvp
ENV HERMES_WEBUI_DEFAULT_WORKSPACE=/home/hermeswebui/.hermes/workspace
ENV HERMES_WEBUI_SKIP_ONBOARDING=1

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8787/health || exit 1

USER hermeswebui

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/start.sh"]
