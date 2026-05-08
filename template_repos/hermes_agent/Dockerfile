FROM public.ecr.aws/aws-observability/aws-sigv4-proxy:1.11.1 AS sigv4proxy

FROM ghcr.io/nesquena/hermes-webui:0.50.259

USER root

ARG NONO_VERSION=0.47.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git haproxy libdbus-1-3 patch unzip \
    && arch="$(dpkg --print-architecture)" \
    && curl -fsSL -o /tmp/nono.deb \
        "https://github.com/always-further/nono/releases/download/v${NONO_VERSION}/nono-cli_${NONO_VERSION}_${arch}.deb" \
    && apt-get install -y --no-install-recommends /tmp/nono.deb \
    && aws_arch="$(uname -m)" \
    && curl -fsSL -o /tmp/awscliv2.zip "https://awscli.amazonaws.com/awscli-exe-linux-${aws_arch}.zip" \
    && unzip -q /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install --bin-dir /usr/local/bin --install-dir /usr/local/aws-cli \
    && rm -f /tmp/nono.deb \
    && rm -rf /tmp/aws /tmp/awscliv2.zip \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch v2026.4.30 https://github.com/NousResearch/hermes-agent.git \
        /opt/hermes-defaults/hermes-agent \
    && rm -rf /opt/hermes-defaults/hermes-agent/.git

COPY --from=sigv4proxy /aws-sigv4-proxy /usr/local/bin/aws-sigv4-proxy
COPY SOUL.md /opt/hermes-defaults/SOUL.md
COPY config.yaml.template /opt/hermes-defaults/config.yaml.template
COPY --chmod=755 apply-patches.py /opt/hermes-defaults/apply-patches.py
COPY haproxy.cfg.template /etc/haproxy/haproxy.cfg.template
COPY hermes-nono-profile.json /etc/nono/profiles/hermes-nono-profile.json
COPY --chmod=755 supervisor.sh /supervisor.sh
COPY --chmod=755 webui.sh /webui.sh

# WebUI lives in the base image at /apptoo/. Patch it before copying it into
# /app so the installed venv and precompiled bytecode use patched source.
COPY patches-webui /tmp/patches-webui
RUN set -eu; \
    python3 /opt/hermes-defaults/apply-patches.py /tmp/patches-webui /apptoo; \
    python3 -m compileall -q /apptoo || true; \
    rm -rf /tmp/patches-webui

COPY patches /tmp/patches-agent

RUN test -f /apptoo/requirements.txt \
    && cat >> /apptoo/requirements.txt <<'EOF'
mcp>=1.2.0,<2
boto3>=1.35.0,<2
EOF

ENV HERMES_HOME=/home/hermeswebui/.hermes
ENV HERMES_WEBUI_AGENT_DIR=${HERMES_HOME}/hermes-agent
ENV HERMES_WEBUI_STATE_DIR=${HERMES_HOME}/webui-mvp
ENV HERMES_WEBUI_DEFAULT_WORKSPACE=/workspace
ENV HERMES_WEBUI_SKIP_ONBOARDING=1

RUN mkdir -p /app /uv_cache "$HERMES_HOME" /workspace \
    && rsync -a /apptoo/ /app/ \
    && cp -r /opt/hermes-defaults/hermes-agent "$HERMES_WEBUI_AGENT_DIR" \
    && python3 /opt/hermes-defaults/apply-patches.py /tmp/patches-agent "$HERMES_WEBUI_AGENT_DIR" \
    && rm -rf /tmp/patches-agent \
    && cp /opt/hermes-defaults/SOUL.md "$HERMES_HOME/SOUL.md" \
    && chown -R hermeswebui:hermeswebui /app /uv_cache "$HERMES_HOME" /workspace

USER hermeswebui

RUN cd /app \
    && export UV_CACHE_DIR=/uv_cache \
    && uv venv /app/venv \
    && . /app/venv/bin/activate \
    && uv pip install -r requirements.txt \
    && uv pip install -U pip setuptools \
    && uv pip install "${HERMES_WEBUI_AGENT_DIR}[all]" \
    && uv pip install "hindsight-client>=0.4.22" \
    && python -m compileall -q -j 0 /app "$HERMES_WEBUI_AGENT_DIR" \
    && touch /app/venv/.deps_installed

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8787/health || exit 1

ENTRYPOINT ["/supervisor.sh"]
CMD ["/webui.sh"]
