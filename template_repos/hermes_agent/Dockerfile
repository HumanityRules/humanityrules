FROM ghcr.io/nesquena/hermes-webui:0.50.126

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends git patch curl ca-certificates libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*

# nono: credential-injecting reverse proxy + env scrubbing. Hermes runs as
# `nono run …` so API keys (OpenAI, Anthropic, Slack, Tavily, …) never enter
# the Hermes child's environment — they're read once by nono before sandbox
# setup, then injected into outbound requests via loopback proxy routes.
ARG NONO_VERSION=0.40.1
ARG NONO_SHA256_AMD64=5de886cc82bc98c4af95ac6961da5dfcbe382ab877dcbc4ebdd198fccbd5367b
ARG NONO_SHA256_ARM64=af0950d31f58ab9e9fc2a0a5b1b64a998288ae4ceb62a00f46dcb9c6234e91c8
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) target=x86_64-unknown-linux-gnu;  sha=$NONO_SHA256_AMD64 ;; \
        arm64) target=aarch64-unknown-linux-gnu; sha=$NONO_SHA256_ARM64 ;; \
        *) echo "unsupported arch for nono: $arch" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/always-further/nono/releases/download/v${NONO_VERSION}/nono-v${NONO_VERSION}-${target}.tar.gz"; \
    curl -fsSL "$url" -o /tmp/nono.tar.gz; \
    echo "${sha}  /tmp/nono.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/nono.tar.gz -C /usr/local/bin nono; \
    chmod 755 /usr/local/bin/nono; \
    rm /tmp/nono.tar.gz; \
    nono --version

RUN git clone --depth 1 --branch v2026.4.16 https://github.com/NousResearch/hermes-agent.git \
        /opt/hermes-defaults/hermes-agent \
    && rm -rf /opt/hermes-defaults/hermes-agent/.git

COPY SOUL.md /opt/hermes-defaults/SOUL.md
COPY config.yaml.template /opt/hermes-defaults/config.yaml.template
COPY nono-profile.json.template /opt/hermes-defaults/nono-profile.json.template
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
