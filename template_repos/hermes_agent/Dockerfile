# syntax=docker/dockerfile:1

FROM ghcr.io/nesquena/hermes-webui:0.51.293

USER root

ARG NONO_VERSION=0.53.0
ARG GWS_VERSION=0.22.5
ARG CADDY_VERSION=2.11.3
ARG PROCESS_COMPOSE_VERSION=1.110.0
ARG TTYD_VERSION=1.7.7
ARG GH_CLI_VERSION=2.92.0
ARG HERMES_AGENT_REF=v2026.6.5
ARG HERMES_AGENT_EXTRAS=messaging,matrix,cron,cli,dev,tts-premium,slack,pty,honcho,mcp,sms,acp,voice,google,bedrock,web

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
ENV HERMES_WEBUI_TRUST_FORWARDED_HOST=1
ENV HERMES_PERSISTENT_ROOT=/hermes-persistent-root
ENV HERMES_CHECKPOINT_ROOT=/hermes-checkpoint
ENV HOMEBREW_PREFIX=/home/linuxbrew/.linuxbrew
ENV HOMEBREW_NO_ANALYTICS=1
ENV HOMEBREW_NO_AUTO_UPDATE=1
ENV HOMEBREW_INSTALL_FROM_API=1
ENV PATH=${DOH_BIN_DIR}:${PATH}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    mkdir -p "$DOH_BIN_DIR" \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl file git libdbus-1-3 patch procps rsync unzip zstd \
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
    && curl -fsSL -o /tmp/caddy.tar.gz \
        "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_${arch}.tar.gz" \
    && tar -xzf /tmp/caddy.tar.gz -C "$DOH_BIN_DIR" caddy \
    && chmod +x "$DOH_BIN_DIR/caddy" \
    && curl -fsSL -o /tmp/process-compose.tar.gz \
        "https://github.com/F1bonacc1/process-compose/releases/download/v${PROCESS_COMPOSE_VERSION}/process-compose_linux_${arch}.tar.gz" \
    && tar -xzf /tmp/process-compose.tar.gz -C "$DOH_BIN_DIR" process-compose \
    && chmod +x "$DOH_BIN_DIR/process-compose" \
    && curl -fsSL -o "$DOH_BIN_DIR/ttyd" \
        "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.${aws_arch}" \
    && chmod +x "$DOH_BIN_DIR/ttyd" \
    && gh_arch="$([ "$arch" = "arm64" ] && echo "arm64" || echo "amd64")" \
    && curl -fsSL -o /tmp/gh.tar.gz \
        "https://github.com/cli/cli/releases/download/v${GH_CLI_VERSION}/gh_${GH_CLI_VERSION}_linux_${gh_arch}.tar.gz" \
    && tar -xzf /tmp/gh.tar.gz -C /tmp \
    && cp "/tmp/gh_${GH_CLI_VERSION}_linux_${gh_arch}/bin/gh" "$DOH_BIN_DIR/gh" \
    && chmod +x "$DOH_BIN_DIR/gh" \
    && rm -rf "/tmp/gh.tar.gz" "/tmp/gh_${GH_CLI_VERSION}_linux_${gh_arch}" \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && rm -f /tmp/nono.deb /tmp/gws.tar.gz /tmp/caddy.tar.gz /tmp/process-compose.tar.gz \
    && rm -rf /tmp/aws /tmp/awscliv2.zip

# Ensure the sandbox's intended PATH survives login-shell resets. Debian's
# /etc/profile rewrites PATH from scratch for non-root uids; Hermes' terminal
# session snapshot uses `bash -l`, so this drop-in restores the supervisor's
# DOH_LOGIN_PATH after /etc/profile runs. The fallback (used when a login
# shell isn't descended from supervisor.sh — e.g. ECS Exec sessions debugging
# the container) keeps Hermes CLI, brew, doh-bin, and system bins reachable so
# basic tools still work.
RUN { \
      echo 'export PATH="${DOH_LOGIN_PATH:-/opt/hermes/webui/venv/bin:/opt/doh/bin:${HOMEBREW_PREFIX}/bin:${HOMEBREW_PREFIX}/sbin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin}"'; \
    } > /etc/profile.d/doh-bin.sh

COPY --chmod=755 build/prune-skills.sh /tmp/prune-skills.sh
COPY --chmod=755 build/apply-patches.py /tmp/apply-patches.py

# Cache mount id includes HERMES_AGENT_REF so a version bump gets a fresh clone
# instead of rsyncing a stale tree left by the previous ref.
RUN --mount=type=cache,id=hermes-agent-git-${HERMES_AGENT_REF},target=/root/.cache/hermes-agent-git,sharing=locked \
    mkdir -p "$HERMES_WEBUI_AGENT_DIR" \
    && if [ -d /root/.cache/hermes-agent-git/.git ] || [ -f /root/.cache/hermes-agent-git/HEAD ]; then \
        rsync -a --delete /root/.cache/hermes-agent-git/ "$HERMES_WEBUI_AGENT_DIR/"; \
    else \
        git clone --depth 1 --branch ${HERMES_AGENT_REF} https://github.com/NousResearch/hermes-agent.git "$HERMES_WEBUI_AGENT_DIR" \
        && rsync -a "$HERMES_WEBUI_AGENT_DIR/" /root/.cache/hermes-agent-git/; \
    fi \
    && /tmp/prune-skills.sh "$HERMES_WEBUI_AGENT_DIR/skills" \
    && rm -f /tmp/prune-skills.sh \
    && rm -rf "$HERMES_WEBUI_AGENT_DIR/.git"

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
fastapi>=0.110,<1
rank_bm25>=0.2,<1
anthropic==0.87.0
EOF

WORKDIR /

RUN mkdir -p /uv_cache "$HERMES_WEBUI_DIR" "$HERMES_HOME" "$HERMES_WEBUI_DEFAULT_WORKSPACE" "$HERMES_PERSISTENT_ROOT" "$HERMES_CHECKPOINT_ROOT" "$DOH_RUN_DIR" \
    && rsync -a /apptoo/ "$HERMES_WEBUI_DIR/" \
    && python3 /tmp/apply-patches.py /tmp/patches-agent "$HERMES_WEBUI_AGENT_DIR" \
    && rm -rf /tmp/patches-agent \
    && rm -rf /app /apptoo \
    && chown -R hermeswebui:hermeswebui /uv_cache "$HERMES_ROOT" "$HERMES_HOME" "$HERMES_WEBUI_DEFAULT_WORKSPACE"

# /home is preserved by persistent-root-runner.sh's full-rootfs rsync and
# tar checkpoint, so brew installs under $HOMEBREW_PREFIX survive
# redeploys without any extra plumbing.
RUN mkdir -p /home/linuxbrew && chown hermeswebui:hermeswebui /home/linuxbrew

USER hermeswebui

# Build the image-owned WebUI/Hermes venv. Install the local Hermes Agent
# project (${HERMES_WEBUI_AGENT_DIR}/pyproject.toml) with DOH-selected extras
# (${HERMES_AGENT_EXTRAS}) into this venv.
RUN --mount=type=cache,target=/uv_cache,uid=1024,gid=1024 \
    cd "$HERMES_WEBUI_DIR" \
    && export UV_CACHE_DIR=/uv_cache \
    && uv venv "$HERMES_WEBUI_DIR/venv" \
    && . "$HERMES_WEBUI_DIR/venv/bin/activate" \
    && uv pip install -r requirements.txt \
    && uv pip install -U pip setuptools \
    && uv pip install "${HERMES_WEBUI_AGENT_DIR}[${HERMES_AGENT_EXTRAS}]" \
    && uv pip install "hindsight-client>=0.4.22" \
    && python -m compileall -q -j 0 "$HERMES_WEBUI_DIR" "$HERMES_WEBUI_AGENT_DIR" \
    && touch "$HERMES_WEBUI_DIR/venv/.deps_installed"

# Empty user venv shared by terminal and execute_code's project mode. Lives on
# the persistent root: rsynced into /var/lib/devopshero/hermes-roots/{slug} on
# first boot and preserved across redeploys, so the agent's installs (pip,
# apt-get) survive container restarts. --seed installs pip/setuptools/wheel
# into the venv's bin/ so `which pip` resolves to the venv's own launcher,
# which auto-targets /workspace/.venv/lib for installs (instead of falling
# through to the root-owned system pip and silently landing in ~/.local).
RUN uv venv --seed --python /usr/local/bin/python3 "${HERMES_WEBUI_DEFAULT_WORKSPACE}/.venv"

# Linuxbrew bootstrap. Bottles are RPATH-locked to HOMEBREW_PREFIX, so we
# install at the canonical prefix and let the persistent-root rsync preserve
# the tree across redeploys (including the agent's `brew install`s).
# HOMEBREW_INSTALL_FROM_API skips cloning the multi-GB homebrew-core tap;
# brew fetches formula JSON on demand.
RUN mkdir -p "${HOMEBREW_PREFIX}/bin" \
    && git clone --depth=1 https://github.com/Homebrew/brew "${HOMEBREW_PREFIX}/Homebrew" \
    && ln -s ../Homebrew/bin/brew "${HOMEBREW_PREFIX}/bin/brew" \
    && "${HOMEBREW_PREFIX}/bin/brew" --version

USER root

# DOH-owned source below this line. Keep these COPY layers last so edits to
# doh_runtime/, webui-extension/, and skills/ reuse the cached dependency
# layers above instead of re-running uv pip install and Linuxbrew bootstrap.
COPY skills/ /opt/hermes/agent/skills/
COPY SOUL.md /opt/hermes/SOUL.md
COPY config.yaml.template /opt/hermes/config.yaml.template
COPY doh_runtime /opt/doh/runtime

# Expose the webapps CLI on PATH. Source-of-truth lives in doh_runtime/ so the
# whole runtime tree COPYs together; the symlink lets the agent type `webapps`.
RUN chmod +x /opt/doh/runtime/webapps \
    && ln -sf /opt/doh/runtime/webapps /opt/doh/bin/webapps

# DOH-owned WebUI extension bundle. Loaded by Hermes WebUI per docs/EXTENSIONS.md
# when the HERMES_WEBUI_EXTENSION_* env vars are set (see webui.sh).
# The sandbox reads this directory so the WebUI can serve the extension bundle.
COPY webui-extension /opt/doh/webui-extension

WORKDIR ${HERMES_WEBUI_DEFAULT_WORKSPACE}

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8787/health || exit 1

ENTRYPOINT ["/opt/doh/runtime/persistent-root-runner.sh"]
CMD ["/opt/doh/runtime/supervisor.sh", "/opt/doh/runtime/webui.sh"]
