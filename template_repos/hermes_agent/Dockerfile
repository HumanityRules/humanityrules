FROM ghcr.io/nesquena/hermes-webui:0.51.63

USER root

ARG NONO_VERSION=0.53.0
ARG GWS_VERSION=0.22.5
ARG HERMES_AGENT_EXTRAS=modal,daytona,vercel,messaging,matrix,cron,cli,dev,tts-premium,slack,pty,honcho,mcp,homeassistant,sms,acp,voice,dingtalk,feishu,google,bedrock,web

# Runtime layout: /opt/doh and /opt/hermes are image-owned and refreshed into
# the persistent root on every start. /workspace is user-owned persistent state;
# /run/doh is ephemeral runtime state inside the task.
ENV DOH_ROOT=/opt/doh
ENV DOH_BIN_DIR=/opt/doh/bin
ENV DOH_RUNTIME_DIR=/opt/doh/runtime
ENV DOH_RUN_DIR=/run/doh
ENV HERMES_ROOT=/opt/hermes
ENV HERMES_CONFIG_TEMPLATE=/opt/hermes/config.yaml.template
ENV HERMES_WEBUI_DIR=/opt/hermes/webui
ENV HERMES_WEBUI_AGENT_DIR=/opt/hermes/agent
ENV HERMES_WEBUI_PYTHON=/opt/hermes/webui/venv/bin/python
ENV HERMES_HOME=/workspace/.hermes
ENV HERMES_WEBUI_STATE_DIR=/workspace/.hermes/webui-mvp
ENV HERMES_WEBUI_DEFAULT_WORKSPACE=/workspace
ENV HERMES_WEBUI_SKIP_ONBOARDING=1
ENV HERMES_WEBUI_EXTENSION_DIR=/opt/doh/webui-extension
ENV HERMES_PERSISTENT_ROOT=/hermes-persistent-root
ENV HERMES_CHECKPOINT_ROOT=/hermes-checkpoint
ENV PATH=/opt/doh/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin

RUN mkdir -p "$DOH_BIN_DIR" \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git libdbus-1-3 patch rsync sudo unzip \
    && arch="$(dpkg --print-architecture)" \
    && curl -fsSL -o /tmp/nono.deb \
        "https://github.com/always-further/nono/releases/download/v${NONO_VERSION}/nono-cli_${NONO_VERSION}_${arch}.deb" \
    && apt-get install -y --no-install-recommends /tmp/nono.deb \
    && cp /usr/bin/nono "$DOH_BIN_DIR/nono" \
    && aws_arch="$(uname -m)" \
    && curl -fsSL -o /tmp/awscliv2.zip "https://awscli.amazonaws.com/awscli-exe-linux-${aws_arch}.zip" \
    && unzip -q /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install --bin-dir "$DOH_BIN_DIR" --install-dir "$DOH_ROOT/aws-cli" \
    && gws_tar="google-workspace-cli-${aws_arch}-unknown-linux-gnu.tar.gz" \
    && curl -fsSL -o /tmp/gws.tar.gz \
        "https://github.com/googleworkspace/cli/releases/download/v${GWS_VERSION}/${gws_tar}" \
    && tar -xzf /tmp/gws.tar.gz -C "$DOH_BIN_DIR" \
    && chmod +x "$DOH_BIN_DIR/gws" \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && rm -f /tmp/nono.deb /tmp/gws.tar.gz \
    && rm -rf /tmp/aws /tmp/awscliv2.zip \
    && rm -rf /var/lib/apt/lists/*

COPY --chmod=755 build/prune-skills.sh /tmp/prune-skills.sh
RUN git clone --depth 1 --branch v2026.5.7 https://github.com/NousResearch/hermes-agent.git \
        "$HERMES_WEBUI_AGENT_DIR" \
    && /tmp/prune-skills.sh "$HERMES_WEBUI_AGENT_DIR/skills" \
    && rm -f /tmp/prune-skills.sh \
    && rm -rf "$HERMES_WEBUI_AGENT_DIR/.git"

# DOH-owned skills. Dropped into the bundled external skills dir so Hermes can
# read them without copying them into the user-owned $HERMES_HOME.
# Our `google-workspace` replaces the pruned upstream one and uses
# platform-managed auth (see template_repos/hermes_agent/skills/google-workspace/).
COPY skills/google-workspace /opt/hermes/agent/skills/productivity/google-workspace

COPY SOUL.md /opt/hermes/SOUL.md
COPY config.yaml.template /opt/hermes/config.yaml.template
COPY --chmod=755 build/apply-patches.py /tmp/apply-patches.py
COPY doh_runtime /opt/doh/runtime

# DOH-owned WebUI extension bundle. Loaded by Hermes WebUI per docs/EXTENSIONS.md
# when the three HERMES_WEBUI_EXTENSION_* env vars are set (see supervisor.sh).
# The sandbox reads this directory so the WebUI can serve the extension bundle.
COPY webui-extension /opt/doh/webui-extension

# WebUI lives in the base image at /apptoo/. Patch it before copying it into
# $HERMES_WEBUI_DIR so the installed venv and precompiled bytecode use patched source.
COPY build/patches-webui /tmp/patches-webui
RUN set -eu; \
    python3 /tmp/apply-patches.py /tmp/patches-webui /apptoo; \
    python3 -m compileall -q /apptoo || true; \
    rm -rf /tmp/patches-webui

COPY build/patches-agent /tmp/patches-agent

RUN test -f /apptoo/requirements.txt \
    && cat >> /apptoo/requirements.txt <<'EOF'
mcp>=1.2.0,<2
boto3>=1.35.0,<2
cryptography>=42
httpx>=0.27,<1
fastmcp>=3.0,<4
uvicorn>=0.30,<1
rank_bm25>=0.2,<1
EOF

WORKDIR /

RUN mkdir -p "$HERMES_WEBUI_DIR" /uv_cache "$HERMES_HOME" /workspace "$HERMES_PERSISTENT_ROOT" "$HERMES_CHECKPOINT_ROOT" "$DOH_RUN_DIR" \
    && rsync -a /apptoo/ "$HERMES_WEBUI_DIR/" \
    && python3 /tmp/apply-patches.py /tmp/patches-agent "$HERMES_WEBUI_AGENT_DIR" \
    && rm -rf /tmp/patches-agent \
    && rm -rf /app /apptoo \
    && chown -R hermeswebui:hermeswebui "$HERMES_ROOT" /uv_cache "$HERMES_HOME" /workspace "$DOH_ROOT"

USER hermeswebui

RUN cd "$HERMES_WEBUI_DIR" \
    && export UV_CACHE_DIR=/uv_cache \
    && uv venv "$HERMES_WEBUI_DIR/venv" \
    && . "$HERMES_WEBUI_DIR/venv/bin/activate" \
    && uv pip install -r requirements.txt \
    && uv pip install -U pip setuptools \
    && uv pip install "${HERMES_WEBUI_AGENT_DIR}[${HERMES_AGENT_EXTRAS}]" \
    && uv pip install "hindsight-client>=0.4.22" \
    && python -m compileall -q -j 0 "$HERMES_WEBUI_DIR" "$HERMES_WEBUI_AGENT_DIR" \
    && touch "$HERMES_WEBUI_DIR/venv/.deps_installed"

USER root

WORKDIR /workspace

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8787/health || exit 1

ENTRYPOINT ["/opt/doh/runtime/persistent-root-runner.sh"]
CMD ["/opt/doh/runtime/supervisor.sh", "/opt/doh/runtime/webui.sh"]
