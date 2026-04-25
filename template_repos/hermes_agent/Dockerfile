FROM ghcr.io/nesquena/hermes-webui:0.50.126

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker-cli git patch \
    && rm -rf /var/lib/apt/lists/*

# ECS-optimized EC2 hosts expose /var/run/docker.sock as root:993 with 0660.
# Keep Hermes running as its required UID/GID while granting Docker socket access.
RUN DOCKER_SOCK_GROUP="$(getent group 993 | cut -d: -f1)" \
    && if [ -z "$DOCKER_SOCK_GROUP" ]; then groupadd --gid 993 dockerhost && DOCKER_SOCK_GROUP=dockerhost; fi \
    && usermod --append --groups "$DOCKER_SOCK_GROUP" hermeswebui

RUN git clone --depth 1 --branch v2026.4.16 https://github.com/NousResearch/hermes-agent.git \
        /opt/hermes-defaults/hermes-agent \
    && rm -rf /opt/hermes-defaults/hermes-agent/.git

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
