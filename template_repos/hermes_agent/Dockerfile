FROM ghcr.io/nesquena/hermes-webui:latest

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/NousResearch/hermes-agent.git \
        /home/hermeswebui/.hermes/hermes-agent \
    && chown -R hermeswebui:hermeswebui /home/hermeswebui/.hermes

COPY config.yaml /opt/hermes-defaults/config.yaml
COPY SOUL.md /opt/hermes-defaults/SOUL.md
COPY --chmod=755 entrypoint.sh /entrypoint.sh

ENV HERMES_WEBUI_STATE_DIR=/home/hermeswebui/.hermes/webui-mvp
ENV HERMES_WEBUI_SKIP_ONBOARDING=1

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8787/health || exit 1

USER hermeswebui

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/hermeswebui_init.bash"]
