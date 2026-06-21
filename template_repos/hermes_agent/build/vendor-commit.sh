#!/bin/bash
# Record a HUMR change to a vendored Hermes fork as one atomic, correct unit.
#
# Editing files under vendor/hermes-agent or vendor/hermes-webui is how we change
# Hermes. Committing that edit correctly spans TWO repos, with three easy ways to
# get it wrong:
#   1. Detached HEAD: a fresh `submodule update` leaves the submodule at the
#      pinned commit, NOT on doh/v*. Commits made there are orphans.
#   2. Push-before-pin: you can commit a superproject gitlink pointing at a
#      commit that only exists on your laptop. Local + prod builds (COPY . .)
#      still work, but a fresh clone / re-init can't fetch the SHA. Silent.
#   3. Forgetting to bump the pin at all.
# This helper does the dance in the right order so none of the three can happen.
#
# Usage (run from anywhere in the monorepo):
#   build/vendor-commit.sh <agent|webui> -m "commit message"
set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
VENDOR="$REPO_ROOT/template_repos/hermes_agent/vendor"

which=${1:?usage: vendor-commit.sh <agent|webui> -m \"message\"}; shift
case "$which" in
    agent) sub="$VENDOR/hermes-agent" ;;
    webui) sub="$VENDOR/hermes-webui" ;;
    *) echo "FATAL: first arg must be 'agent' or 'webui'" >&2; exit 2 ;;
esac

# Parse -m message (everything after -m).
msg=""
if [ "${1:-}" = "-m" ]; then shift; msg=${1:?missing message after -m}; shift || true; fi
[ -n "$msg" ] || { echo "FATAL: pass -m \"commit message\"" >&2; exit 2; }

branch=$(git -C "$sub" rev-parse --abbrev-ref HEAD)
if [ "$branch" = "HEAD" ]; then
    echo "FATAL: $sub is in detached HEAD (at the pinned commit), not on a doh/v* branch." >&2
    echo "  Check out the fork branch first, e.g.:" >&2
    echo "    git -C $sub checkout \$(git -C $sub for-each-ref --format='%(refname:short)' 'refs/heads/doh/*' | head -1)" >&2
    exit 1
fi
case "$branch" in doh/*) ;; *)
    echo "FATAL: $sub is on '$branch', expected a doh/* fork branch. Refusing to commit." >&2
    exit 1 ;;
esac

if git -C "$sub" diff --quiet && git -C "$sub" diff --cached --quiet; then
    echo "FATAL: no changes in $sub to commit." >&2
    exit 1
fi

echo "==> committing in submodule ($which, branch $branch)"
git -C "$sub" add -A
git -C "$sub" commit -m "$msg"

echo "==> pushing fork branch to mirror BEFORE bumping the pin (avoids unfetchable SHA)"
git -C "$sub" push doh "$branch" 2>/dev/null || git -C "$sub" push origin "$branch"

echo "==> bumping the superproject gitlink"
git -C "$REPO_ROOT" add "$sub"
git -C "$REPO_ROOT" commit -m "vendor($which): $msg"

echo "Done. Submodule commit pushed and pinned. Superproject pin:"
git -C "$REPO_ROOT" --no-pager log -1 --oneline
