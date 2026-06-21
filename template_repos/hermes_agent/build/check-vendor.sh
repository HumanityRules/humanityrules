#!/bin/bash
# Fail-fast guard for the vendored Hermes submodules.
#
# The vendored agent/webui trees arrive as git submodules under vendor/. The
# build context has no .git, so they must be materialized on the builder's
# checkout (`git submodule update --init`) before `docker build`. An
# unmaterialized submodule COPYs into the image as an EMPTY directory -- no
# error -- and the breakage would otherwise surface minutes later as a cryptic
# pip/import failure. This guard turns that into an immediate, legible failure.
#
# It also asserts the webui fork was rebased onto the SAME upstream version as
# the base image (FROM ...hermes-webui:<WEBUI_BASE_VERSION>). Overlaying fork
# source from version X onto a base image of version Y is the single riskiest
# silent failure in the whole vendoring scheme; this closes it.
set -euo pipefail

AGENT_DIR=${1:?usage: check-vendor.sh <agent-dir> <webui-src-dir> <expected-webui-version>}
WEBUI_SRC=${2:?missing webui src dir}
EXPECTED_VERSION=${3:?missing expected webui version}

die() {
    echo "FATAL: $*" >&2
    echo >&2
    echo "  The vendored Hermes submodules are not materialized. Run:" >&2
    echo "    git submodule update --init template_repos/hermes_agent/vendor/hermes-agent" >&2
    echo "    git submodule update --init template_repos/hermes_agent/vendor/hermes-webui" >&2
    echo "  then rebuild." >&2
    exit 1
}

[ -f "$AGENT_DIR/pyproject.toml" ] || die "agent submodule missing ($AGENT_DIR/pyproject.toml not found)"
[ -f "$WEBUI_SRC/server.py" ]      || die "webui submodule missing ($WEBUI_SRC/server.py not found)"
[ -f "$WEBUI_SRC/requirements.txt" ] || die "webui submodule incomplete ($WEBUI_SRC/requirements.txt not found)"

actual_version=$(cat "$WEBUI_SRC/.doh-upstream-version" 2>/dev/null || echo "<missing>")
if [ "$actual_version" != "$EXPECTED_VERSION" ]; then
    echo "FATAL: webui fork/base version mismatch." >&2
    echo "  Base image (FROM): $EXPECTED_VERSION" >&2
    echo "  Fork pinned to:     $actual_version (vendor/hermes-webui/.doh-upstream-version)" >&2
    echo "  Rebase the humr/v* branch onto the matching upstream tag and update" >&2
    echo "  .doh-upstream-version, or set --build-arg WEBUI_BASE_VERSION to match." >&2
    exit 1
fi

echo "[check-vendor] OK: agent + webui materialized; webui fork matches base $EXPECTED_VERSION"
