FROM public.ecr.aws/aws-observability/aws-sigv4-proxy:1.11.1 AS sigv4proxy

FROM ghcr.io/nesquena/hermes-webui:0.50.259

USER root

ARG NONO_VERSION=0.47.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git haproxy libdbus-1-3 unzip \
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
COPY haproxy-aws.cfg.template /etc/haproxy/aws-endpoints.cfg.template
COPY nono-profile.json /etc/nono/profiles/hermes-nono.json
COPY --chmod=755 supervisor.sh /supervisor.sh
COPY --chmod=755 webui.sh /webui.sh

RUN test -f /apptoo/requirements.txt \
    && cat >> /apptoo/requirements.txt <<'EOF'
mcp>=1.2.0,<2
boto3>=1.35.0,<2
EOF

RUN mkdir -p /app /uv_cache /home/hermeswebui/.hermes \
    && rsync -a --chown=hermeswebui:hermeswebui /apptoo/ /app/ \
    && cp -r /opt/hermes-defaults/hermes-agent /home/hermeswebui/.hermes/hermes-agent \
    && chown -R hermeswebui:hermeswebui /app /uv_cache /home/hermeswebui/.hermes

USER hermeswebui

RUN cd /app \
    && UV_PROJECT_ENVIRONMENT=venv UV_CACHE_DIR=/uv_cache uv venv venv \
    && . /app/venv/bin/activate \
    && UV_CACHE_DIR=/uv_cache uv pip install -r requirements.txt --trusted-host pypi.org --trusted-host files.pythonhosted.org \
    && UV_CACHE_DIR=/uv_cache uv pip install -U pip setuptools --trusted-host pypi.org --trusted-host files.pythonhosted.org \
    && UV_CACHE_DIR=/uv_cache uv pip install "/home/hermeswebui/.hermes/hermes-agent[all]" --trusted-host pypi.org --trusted-host files.pythonhosted.org \
    && UV_CACHE_DIR=/uv_cache uv pip install "hindsight-client>=0.4.22" --trusted-host pypi.org --trusted-host files.pythonhosted.org \
    && touch /app/venv/.deps_installed

USER root

ENV HERMES_WEBUI_STATE_DIR=/home/hermeswebui/.hermes/webui-mvp
ENV HERMES_WEBUI_DEFAULT_WORKSPACE=/workspace
ENV HERMES_WEBUI_SKIP_ONBOARDING=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN mkdir -p /home/hermeswebui/.aws /home/hermeswebui/.hermes /workspace \
    && chown -R hermeswebui:hermeswebui /home/hermeswebui/.aws /home/hermeswebui/.hermes /workspace /opt/hermes-defaults /app /uv_cache

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8787/health || exit 1

USER hermeswebui

ENTRYPOINT ["/supervisor.sh"]
CMD ["/webui.sh"]
