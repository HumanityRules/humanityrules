FROM ghcr.io/nesquena/hermes-webui:0.50.259

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch v2026.4.30 https://github.com/NousResearch/hermes-agent.git \
        /opt/hermes-defaults/hermes-agent \
    && rm -rf /opt/hermes-defaults/hermes-agent/.git

COPY SOUL.md /opt/hermes-defaults/SOUL.md
COPY config.yaml.template /opt/hermes-defaults/config.yaml.template
COPY --chmod=755 entrypoint.sh /entrypoint.sh
COPY --chmod=755 start.sh /start.sh

RUN test -f /apptoo/requirements.txt \
    && cat >> /apptoo/requirements.txt <<'EOF'
mcp>=1.2.0,<2
boto3>=1.35.0,<2
EOF

ENV HERMES_WEBUI_STATE_DIR=/home/hermeswebui/.hermes/webui-mvp
ENV HERMES_WEBUI_DEFAULT_WORKSPACE=/workspace
ENV HERMES_WEBUI_SKIP_ONBOARDING=1

RUN mkdir -p /home/hermeswebui/.hermes /workspace \
    && chown -R hermeswebui:hermeswebui /home/hermeswebui/.hermes /workspace /opt/hermes-defaults

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8787/health || exit 1

USER hermeswebui

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/start.sh"]
