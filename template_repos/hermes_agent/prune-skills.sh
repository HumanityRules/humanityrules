#!/bin/bash
# Curate the bundled skill set with an allowlist: only skills listed in the
# allowlist survive in the image. Anything else (new upstream skills,
# macOS-only skills, competitor coding agents, consumer/novelty skills,
# policy-risky content) is pruned. This means a Hermes version bump can't
# silently add new skills to Enterprise deployments — additions require an
# allowlist edit.
#
# Usage: prune-skills.sh <bundled-skills-dir> <allowlist-file>
set -euo pipefail

BUNDLED=$1
ALLOW=$2

[ -d "$BUNDLED" ] || { echo "FATAL: bundled skills dir '$BUNDLED' missing" >&2; exit 1; }
[ -f "$ALLOW" ]   || { echo "FATAL: allowlist '$ALLOW' missing" >&2; exit 1; }

# Fail fast if the allowlist references a path that no longer exists upstream
# (rename or removal on version bump) — don't silently drop it.
while read -r line; do
    rel=$(echo "$line" | sed -e 's/#.*//' -e 's/[[:space:]]//g')
    [ -z "$rel" ] && continue
    if [ ! -f "$BUNDLED/$rel/SKILL.md" ]; then
        echo "FATAL: allowlisted skill '$rel' not found in bundled repo" >&2
        exit 1
    fi
done < "$ALLOW"

# Mark each allowlisted skill, delete every other SKILL.md directory,
# then clear the markers.
while read -r line; do
    rel=$(echo "$line" | sed -e 's/#.*//' -e 's/[[:space:]]//g')
    [ -z "$rel" ] && continue
    touch "$BUNDLED/$rel/.doh-keep"
done < "$ALLOW"

find "$BUNDLED" -name SKILL.md -not -path "*/.git/*" -print0 \
    | while IFS= read -r -d '' skill_md; do
        skill_dir=$(dirname "$skill_md")
        if [ ! -f "$skill_dir/.doh-keep" ]; then
            rm -rf "$skill_dir"
        fi
      done

find "$BUNDLED" -name .doh-keep -delete

# Drop now-empty category dirs, and any category dir whose only remaining
# content is a DESCRIPTION.md (would render an empty panel in the WebUI).
find "$BUNDLED" -mindepth 1 -maxdepth 2 -type d -empty -delete
for desc in "$BUNDLED"/*/DESCRIPTION.md; do
    [ -f "$desc" ] || continue
    cat_dir=$(dirname "$desc")
    siblings=$(find "$cat_dir" -mindepth 1 -maxdepth 1 -type d | wc -l)
    [ "$siblings" -eq 0 ] && rm -rf "$cat_dir"
done

echo "[skills-allowlist] kept $(find "$BUNDLED" -name SKILL.md | wc -l) skills"
