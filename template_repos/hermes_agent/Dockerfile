FROM ghcr.io/nesquena/hermes-webui:latest

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/NousResearch/hermes-agent.git \
        /opt/hermes-defaults/hermes-agent

COPY SOUL.md /opt/hermes-defaults/SOUL.md
COPY --chmod=755 entrypoint.sh /entrypoint.sh

# Patch upstream index.html: mark sessionSearch as type="search" with
# autocomplete="off" so Chrome does not autofill it with saved credentials
# from the /login password form (which has no username field).
# You can remove it when they fix the issue at the Hermes level. Or contribute a patch!
RUN for f in /app/static/index.html /apptoo/static/index.html; do \
      [ -f "$f" ] && sed -i \
        's/<input id="sessionSearch"/<input id="sessionSearch" type="search" autocomplete="off" name="session-filter"/' \
        "$f"; \
    done

ENV HERMES_WEBUI_STATE_DIR=/home/hermeswebui/.hermes/webui-mvp
ENV HERMES_WEBUI_SKIP_ONBOARDING=1

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8787/health || exit 1

USER hermeswebui

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/hermeswebui_init.bash"]
