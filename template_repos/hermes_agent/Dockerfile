# syntax=docker/dockerfile:1

ARG WEBUI_BASE_VERSION=0.52.76

# ============================================================================
# Erlang/OTP + Elixir builder stage.
#
# Built from the SAME base image as the final stage so the trees we copy out
# link against the same glibc/libssl/libncurses at runtime (ABI match on Debian
# trixie / glibc 2.41 — precompiled OTP from builds.hex.pm targets older Ubuntu
# and is risky here). Build deps (gcc, libssl-dev, …) stay in this throwaway
# stage and never reach the final image or the checkpointed /usr; only the
# finished /opt/humr/{erlang,elixir} trees are COPYed forward. OTP installs at
# its final canonical prefix (/opt/humr/erlang), which the persistent-root
# runner re-syncs to that same path on every boot, so the absolute ROOTDIR
# baked into `erl` stays correct without relocation.
# ============================================================================
FROM ghcr.io/nesquena/hermes-webui:${WEBUI_BASE_VERSION} AS erlang-builder
USER root
# Elixir 1.20.x ships an elixir-otp-29 precompiled build, so OTP 29 + Elixir
# 1.20 are a compatible pair. Bump all three together.
ARG OTP_VERSION=29.0.2
ARG ELIXIR_VERSION=1.20.2
ARG ELIXIR_OTP_RELEASE=29
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential autoconf m4 libncurses-dev libssl-dev zlib1g-dev \
        unzip curl ca-certificates \
    && curl -fsSL -o /tmp/otp.tar.gz \
        "https://github.com/erlang/otp/releases/download/OTP-${OTP_VERSION}/otp_src_${OTP_VERSION}.tar.gz" \
    && mkdir -p /tmp/otp && tar -xzf /tmp/otp.tar.gz -C /tmp/otp --strip-components=1 \
    && cd /tmp/otp \
    && ./configure --prefix=/opt/humr/erlang \
        --without-wx --without-observer --without-debugger --without-et --without-javac \
    && make -j"$(nproc)" \
    && make install \
    && cd / && rm -rf /tmp/otp /tmp/otp.tar.gz \
    && curl -fsSL -o /tmp/elixir.zip \
        "https://github.com/elixir-lang/elixir/releases/download/v${ELIXIR_VERSION}/elixir-otp-${ELIXIR_OTP_RELEASE}.zip" \
    && mkdir -p /opt/humr/elixir \
    && unzip -q /tmp/elixir.zip -d /opt/humr/elixir \
    && rm -f /tmp/elixir.zip \
    && export PATH="/opt/humr/erlang/bin:/opt/humr/elixir/bin:${PATH}" \
    && erl -noshell -eval 'ok = crypto:start(), io:format("OTP ~s crypto OK~n", [erlang:system_info(otp_release)]), halt().' \
    && elixir --version

# ============================================================================
# Final image.
# ============================================================================
FROM ghcr.io/nesquena/hermes-webui:${WEBUI_BASE_VERSION}

# Re-declare so the post-FROM stage can use it: the vendor guard asserts the
# webui fork was rebased onto this same upstream version (see check-vendor.sh).
ARG WEBUI_BASE_VERSION

USER root

ARG NONO_VERSION=0.53.0
ARG GWS_VERSION=0.22.5
ARG CADDY_VERSION=2.11.3
ARG PROCESS_COMPOSE_VERSION=1.110.0
ARG TTYD_VERSION=1.7.7
ARG GH_CLI_VERSION=2.92.0
# Node LTS ("Krypton") for the browser tool's agent-browser CLI. agent-browser
# requires Node >= 24, so this must stay on the 24.x line.
ARG NODE_VERSION=24.17.0
ARG AGENT_BROWSER_VERSION=0.28.0
ARG HERMES_AGENT_EXTRAS=messaging,matrix,cron,cli,dev,tts-premium,slack,pty,honcho,mcp,sms,acp,voice,google,bedrock,web

# Runtime layout: /opt/humr and /opt/hermes are image-owned and refreshed into
# the persistent root on every start. /workspace is user-owned persistent state;
# /run/humr is ephemeral runtime state inside the task.
# Build-time only: no runtime script reads HUMR_ROOT.
ARG HUMR_ROOT=/opt/humr
ENV HUMR_BIN_DIR=/opt/humr/bin
ENV HUMR_RUNTIME_DIR=/opt/humr/runtime
ENV HERMES_WEBUI_DIR=/opt/hermes/webui
ENV HERMES_WEBUI_AGENT_DIR=/opt/hermes/agent
ENV HERMES_WEBUI_PYTHON=/opt/hermes/webui/venv/bin/python
ENV HERMES_HOME=/workspace/.hermes
ENV HERMES_WEBUI_STATE_DIR=/workspace/.hermes/webui-mvp
ENV HERMES_WEBUI_DEFAULT_WORKSPACE=/workspace
ENV HERMES_WEBUI_SKIP_ONBOARDING=1
ENV HERMES_WEBUI_TRUST_FORWARDED_HOST=1
ENV HERMES_PERSISTENT_ROOT=/hermes-persistent-root
ENV HERMES_CHECKPOINT_ROOT=/hermes-checkpoint
ENV HOMEBREW_PREFIX=/home/linuxbrew/.linuxbrew
ENV PATH=${HUMR_BIN_DIR}:${PATH}

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    mkdir -p "$HUMR_BIN_DIR" \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl file git libdbus-1-3 procps rsync unzip zstd \
    && arch="$(dpkg --print-architecture)" \
    && curl -fsSL -o /tmp/nono.deb \
        "https://github.com/always-further/nono/releases/download/v${NONO_VERSION}/nono-cli_${NONO_VERSION}_${arch}.deb" \
    && apt-get install -y --no-install-recommends /tmp/nono.deb \
    && cp /usr/bin/nono "$HUMR_BIN_DIR/nono" \
    && aws_arch="$(uname -m)" \
    && curl -fsSL -o /tmp/awscliv2.zip "https://awscli.amazonaws.com/awscli-exe-linux-${aws_arch}.zip" \
    && unzip -q /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install --bin-dir "$HUMR_BIN_DIR" --install-dir "$HUMR_ROOT/aws-cli" \
    && gws_tar="google-workspace-cli-${aws_arch}-unknown-linux-gnu.tar.gz" \
    && curl -fsSL -o /tmp/gws.tar.gz \
        "https://github.com/googleworkspace/cli/releases/download/v${GWS_VERSION}/${gws_tar}" \
    && tar -xzf /tmp/gws.tar.gz -C "$HUMR_BIN_DIR" \
    && chmod +x "$HUMR_BIN_DIR/gws" \
    && curl -fsSL -o /tmp/caddy.tar.gz \
        "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_${arch}.tar.gz" \
    && tar -xzf /tmp/caddy.tar.gz -C "$HUMR_BIN_DIR" caddy \
    && chmod +x "$HUMR_BIN_DIR/caddy" \
    && curl -fsSL -o /tmp/process-compose.tar.gz \
        "https://github.com/F1bonacc1/process-compose/releases/download/v${PROCESS_COMPOSE_VERSION}/process-compose_linux_${arch}.tar.gz" \
    && tar -xzf /tmp/process-compose.tar.gz -C "$HUMR_BIN_DIR" process-compose \
    && chmod +x "$HUMR_BIN_DIR/process-compose" \
    && curl -fsSL -o "$HUMR_BIN_DIR/ttyd" \
        "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.${aws_arch}" \
    && chmod +x "$HUMR_BIN_DIR/ttyd" \
    && gh_arch="$([ "$arch" = "arm64" ] && echo "arm64" || echo "amd64")" \
    && curl -fsSL -o /tmp/gh.tar.gz \
        "https://github.com/cli/cli/releases/download/v${GH_CLI_VERSION}/gh_${GH_CLI_VERSION}_linux_${gh_arch}.tar.gz" \
    && tar -xzf /tmp/gh.tar.gz -C /tmp \
    && cp "/tmp/gh_${GH_CLI_VERSION}_linux_${gh_arch}/bin/gh" "$HUMR_BIN_DIR/gh" \
    && chmod +x "$HUMR_BIN_DIR/gh" \
    && rm -rf "/tmp/gh.tar.gz" "/tmp/gh_${GH_CLI_VERSION}_linux_${gh_arch}" \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && rm -f /tmp/nono.deb /tmp/gws.tar.gz /tmp/caddy.tar.gz /tmp/process-compose.tar.gz \
    && rm -rf /tmp/aws /tmp/awscliv2.zip

# Ensure the sandbox's intended PATH survives login-shell resets. Debian's
# /etc/profile rewrites PATH from scratch for non-root uids; Hermes' terminal
# session snapshot uses `bash -l`, so this drop-in restores the supervisor's
# HUMR_LOGIN_PATH after /etc/profile runs. The fallback (used when a login
# shell isn't descended from supervisor.sh — e.g. ECS Exec sessions debugging
# the container) keeps Hermes CLI, brew, humr-bin, and system bins reachable so
# basic tools still work.
RUN { \
      echo 'export PATH="${HUMR_LOGIN_PATH:-/opt/hermes/webui/venv/bin:/opt/humr/bin:${HOMEBREW_PREFIX}/bin:${HOMEBREW_PREFIX}/sbin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin}"'; \
    } > /etc/profile.d/humr-bin.sh

COPY --chmod=755 build/prune-skills.sh /tmp/prune-skills.sh
COPY --chmod=755 build/check-vendor.sh /tmp/check-vendor.sh
COPY --chmod=755 build/overlay-webui.sh /tmp/overlay-webui.sh

# Vendored Hermes agent fork (git submodule at vendor/hermes-agent). It carries
# HUMR changes as commits on its humr/v* branch, so we copy it straight into place.
# The submodule's .git pointer is kept out of the build context by .dockerignore
# (**/.git). Docker layer caching makes a same-pin rebuild a cache hit.
COPY vendor/hermes-agent ${HERMES_WEBUI_AGENT_DIR}/

# Vendored Hermes webui fork (git submodule at vendor/hermes-webui), staged for
# the /apptoo overlay below.
COPY vendor/hermes-webui /tmp/vendor/hermes-webui

# Fail fast before any expensive work: an unmaterialized submodule COPYs as an
# empty dir (no error), and overlaying a webui fork rebased onto a different
# upstream version than the base image is the riskiest silent failure here.
RUN /tmp/check-vendor.sh "$HERMES_WEBUI_AGENT_DIR" /tmp/vendor/hermes-webui "${WEBUI_BASE_VERSION}" \
    && rm -f /tmp/check-vendor.sh

# Curate the bundled agent skill set. (The submodule's .git pointer is excluded
# from the build context by .dockerignore's **/.git, so it never arrives here.)
# subagent-driven-development ships upstream under optional-skills/ (never
# loaded from there); promote it into the bundled tree first — the kept
# software-development skills (plan, requesting-code-review, ...) reference it,
# and the allowlist keeps it.
RUN cp -R "$HERMES_WEBUI_AGENT_DIR/optional-skills/software-development/subagent-driven-development" \
        "$HERMES_WEBUI_AGENT_DIR/skills/software-development/subagent-driven-development" \
    && /tmp/prune-skills.sh "$HERMES_WEBUI_AGENT_DIR/skills" \
    && rm -f /tmp/prune-skills.sh

# WebUI lives in the base image at /apptoo/. Overlay the vendored fork onto it
# (rsync whole tree, --delete, protecting the build-stamped api/_version.py)
# before it is copied into $HERMES_WEBUI_DIR so the installed venv and
# precompiled bytecode use the overlaid source.
RUN /tmp/overlay-webui.sh /tmp/vendor/hermes-webui /apptoo \
    && rm -f /tmp/overlay-webui.sh \
    && rm -rf /tmp/vendor

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

RUN mkdir -p /uv_cache "$HERMES_WEBUI_DIR" "$HERMES_HOME" "$HERMES_WEBUI_DEFAULT_WORKSPACE" "$HERMES_PERSISTENT_ROOT" "$HERMES_CHECKPOINT_ROOT" \
    && rsync -a /apptoo/ "$HERMES_WEBUI_DIR/" \
    && rm -rf /app /apptoo \
    && chown -R hermeswebui:hermeswebui /uv_cache /opt/hermes "$HERMES_HOME" "$HERMES_WEBUI_DEFAULT_WORKSPACE"

# /home is preserved by persistent-root-runner.sh's full-rootfs rsync and
# tar checkpoint, so brew installs under $HOMEBREW_PREFIX survive
# redeploys without any extra plumbing.
RUN mkdir -p /home/linuxbrew && chown hermeswebui:hermeswebui /home/linuxbrew

# Every brew invocation — the build-time bootstrap below and the agent's
# in-sandbox installs alike — reads Homebrew's system-wide env file, so brew
# defaults live here instead of as container-wide ENV.
# HOMEBREW_INSTALL_FROM_API skips cloning the multi-GB homebrew-core tap;
# brew fetches formula JSON on demand.
RUN mkdir -p /etc/homebrew \
    && printf '%s\n' \
        'HOMEBREW_NO_ANALYTICS=1' \
        'HOMEBREW_NO_AUTO_UPDATE=1' \
        'HOMEBREW_INSTALL_FROM_API=1' \
        > /etc/homebrew/brew.env

USER hermeswebui

# Build the image-owned WebUI/Hermes venv. Install the local Hermes Agent
# project (${HERMES_WEBUI_AGENT_DIR}/pyproject.toml) with HUMR-selected extras
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
# the persistent root: rsynced into /var/lib/humr/hermes-roots/{slug} on
# first boot and preserved across redeploys, so the agent's installs (pip,
# apt-get) survive container restarts. --seed installs pip/setuptools/wheel
# into the venv's bin/ so `which pip` resolves to the venv's own launcher,
# which auto-targets /workspace/.venv/lib for installs (instead of falling
# through to the root-owned system pip and silently landing in ~/.local).
RUN uv venv --seed --python /usr/local/bin/python3 "${HERMES_WEBUI_DEFAULT_WORKSPACE}/.venv"

# Linuxbrew bootstrap. Bottles are RPATH-locked to HOMEBREW_PREFIX, so we
# install at the canonical prefix and let the persistent-root rsync preserve
# the tree across redeploys (including the agent's `brew install`s).
RUN mkdir -p "${HOMEBREW_PREFIX}/bin" \
    && git clone --depth=1 https://github.com/Homebrew/brew "${HOMEBREW_PREFIX}/Homebrew" \
    && ln -s ../Homebrew/bin/brew "${HOMEBREW_PREFIX}/bin/brew" \
    && "${HOMEBREW_PREFIX}/bin/brew" --version

USER root

# Node LTS ("Krypton") under /opt/humr. Image-owned: re-synced from the image on
# every boot and excluded from the EFS checkpoint (persistent-root-runner.sh
# lists /opt/humr in IMAGE_OWNED_DIRS). node/npm/npx are symlinked into
# $HUMR_BIN_DIR, already on the agent's PATH. Kept in its own layer, separate
# from agent-browser below, so either can be dropped independently.
RUN arch="$(dpkg --print-architecture)" \
    && node_arch="$([ "$arch" = "amd64" ] && echo "x64" || echo "arm64")" \
    && curl -fsSL -o /tmp/node.tar.gz \
        "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.gz" \
    && mkdir -p "$HUMR_ROOT/node" \
    && tar -xzf /tmp/node.tar.gz -C "$HUMR_ROOT/node" --strip-components=1 \
    && ln -sf "$HUMR_ROOT/node/bin/node" "$HUMR_BIN_DIR/node" \
    && ln -sf "$HUMR_ROOT/node/bin/npm" "$HUMR_BIN_DIR/npm" \
    && ln -sf "$HUMR_ROOT/node/bin/npx" "$HUMR_BIN_DIR/npx" \
    && node --version && npm --version \
    && rm -f /tmp/node.tar.gz

# agent-browser CLI for the browser tool (drives both local and cloud/Browser Use
# backends). Depends on the Node layer above (requires Node >= 24, and npm to
# install). npm resolves its global prefix to /opt/humr/node via node's realpath,
# so the package + bin land under /opt/humr; the bin is symlinked into
# $HUMR_BIN_DIR. agent-browser bundles prebuilt CLIs for ~7 platforms (~11MB
# each) — keep only the glibc binary for the build arch plus the small JS
# launcher and drop the rest. Clear npm's download cache (~/.npm) so it neither
# bloats the image nor lands in the EFS checkpoint. Drop this whole layer to
# remove agent-browser while leaving Node in place.
RUN arch="$(dpkg --print-architecture)" \
    && node_arch="$([ "$arch" = "amd64" ] && echo "x64" || echo "arm64")" \
    && npm install -g "agent-browser@${AGENT_BROWSER_VERSION}" \
    && ln -sf "$HUMR_ROOT/node/bin/agent-browser" "$HUMR_BIN_DIR/agent-browser" \
    && find "$HUMR_ROOT/node/lib/node_modules/agent-browser/bin" -maxdepth 1 -type f \
        -name "agent-browser-*" ! -name "agent-browser-linux-${node_arch}" -delete \
    && agent-browser --version \
    && npm cache clean --force \
    && rm -rf /root/.npm

# Erlang/OTP + Elixir under /opt/humr (built in the erlang-builder stage above).
# Image-owned: re-synced from the image on every boot and excluded from the EFS
# checkpoint (persistent-root-runner.sh lists /opt/humr in IMAGE_OWNED_DIRS), so
# this multi-hundred-MB runtime never lands in the shutdown tarball — the same
# treatment as Node. A runtime `brew install elixir` (the old webapps Elixir
# skill) would instead install under /home, which IS checkpointed, and would
# inflate every backup by the full Erlang+Elixir footprint. erl/elixir/mix/iex
# are symlinked into $HUMR_BIN_DIR, already on the agent's PATH. The base image
# already ships the runtime libs OTP links against (libssl3, libncurses6,
# libtinfo6). Kept above the HUMR-owned source COPYs so source edits don't
# rebuild it.
COPY --from=erlang-builder /opt/humr/erlang /opt/humr/erlang
COPY --from=erlang-builder /opt/humr/elixir /opt/humr/elixir
RUN ln -sf /opt/humr/erlang/bin/erl     "$HUMR_BIN_DIR/erl" \
    && ln -sf /opt/humr/erlang/bin/erlc    "$HUMR_BIN_DIR/erlc" \
    && ln -sf /opt/humr/erlang/bin/escript "$HUMR_BIN_DIR/escript" \
    && ln -sf /opt/humr/elixir/bin/elixir  "$HUMR_BIN_DIR/elixir" \
    && ln -sf /opt/humr/elixir/bin/elixirc "$HUMR_BIN_DIR/elixirc" \
    && ln -sf /opt/humr/elixir/bin/mix     "$HUMR_BIN_DIR/mix" \
    && ln -sf /opt/humr/elixir/bin/iex     "$HUMR_BIN_DIR/iex" \
    && erl -noshell -eval 'ok = crypto:start(), halt().' \
    && elixir --version

# HUMR-owned source below this line. Keep these COPY layers last so edits to
# humr_runtime/, webui-extension/, and skills/ reuse the cached dependency
# layers above instead of re-running uv pip install and Linuxbrew bootstrap.
COPY skills/ /opt/hermes/agent/skills/
COPY SOUL.md /opt/hermes/SOUL.md
COPY ENVIRONMENT.md /opt/hermes/ENVIRONMENT.md
COPY config.yaml.template /opt/hermes/config.yaml.template
COPY humr_runtime /opt/humr/runtime

# Expose the Web Apps and Widgets CLIs on PATH. Source-of-truth lives in
# humr_runtime/ so the whole runtime tree COPYs together.
RUN chmod +x /opt/humr/runtime/webapps/webapps \
        /opt/humr/runtime/widgets/widgets \
    && ln -sf /opt/humr/runtime/webapps/webapps /opt/humr/bin/webapps \
    && ln -sf /opt/humr/runtime/widgets/widgets /opt/humr/bin/widgets

# Image-owned source for the HUMR WebUI extension bundle. sandbox_seed.py syncs
# it into the WebUI-managed extension root on every boot.
COPY webui-extension /opt/humr/webui-extension

WORKDIR ${HERMES_WEBUI_DEFAULT_WORKSPACE}

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8787/health || exit 1

ENTRYPOINT ["/opt/humr/runtime/persistent-root-runner.sh"]
CMD ["/opt/humr/runtime/supervisor.sh", "/opt/humr/runtime/webui.sh"]
