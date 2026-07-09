#!/bin/bash
# Curate the bundled Hermes skill set. Only the HUMR-approved skills listed in
# this script survive in the image; every other upstream bundled skill is
# removed at build time.
set -euo pipefail

BUNDLED=${1:?Usage: prune-skills.sh <bundled-skills-dir>}
KEEP_MARKER=.humr-keep

[ -d "$BUNDLED" ] || { echo "FATAL: bundled skills dir '$BUNDLED' missing" >&2; exit 1; }

allowlisted_skills() {
    cat <<'EOF'
creative/architecture-diagram
creative/ascii-art
creative/ascii-video
creative/baoyu-infographic
creative/excalidraw
creative/manim-video
creative/p5js
creative/popular-web-designs
github/codebase-inspection
github/github-code-review
github/github-issues
github/github-pr-workflow
github/github-repo-management
media/gif-search
media/youtube-content
note-taking/obsidian
productivity/maps
productivity/nano-pdf
productivity/ocr-and-documents
productivity/powerpoint
research/arxiv
research/blogwatcher
research/llm-wiki
research/polymarket
research/research-paper-writing
software-development/plan
software-development/requesting-code-review
software-development/systematic-debugging
software-development/test-driven-development
EOF
}

while IFS= read -r rel; do
    if [ ! -f "$BUNDLED/$rel/SKILL.md" ]; then
        echo "FATAL: allowlisted skill '$rel' not found in bundled repo" >&2
        exit 1
    fi
    touch "$BUNDLED/$rel/$KEEP_MARKER"
done < <(allowlisted_skills)

find "$BUNDLED" -name SKILL.md -not -path "*/.git/*" -print0 \
    | while IFS= read -r -d '' skill_md; do
        skill_dir=$(dirname "$skill_md")
        if [ ! -f "$skill_dir/$KEEP_MARKER" ]; then
            rm -rf "$skill_dir"
        fi
    done

find "$BUNDLED" -name "$KEEP_MARKER" -delete

# Prune individual files INSIDE a kept skill that collide with a real skill
# name. Hermes' skill loader (skills_tool.py "Strategy 3") rglobs for flat
# `<name>.md` files anywhere under a search dir and treats each as a skill, so
# a brand template like popular-web-designs/templates/notion.md registers under
# the name "notion" — misleading as a Notion integration skill. We can't fix
# the loader without carrying an upstream patch, so we drop just the colliding
# template files. Tolerant by design: if upstream renames/removes one, the
# collision is already gone, so a missing file is logged, not fatal.
colliding_template_files() {
    cat <<'EOF'
creative/popular-web-designs/templates/notion.md
creative/popular-web-designs/templates/posthog.md
EOF
}

while IFS= read -r rel; do
    [ -z "$rel" ] && continue
    if [ -f "$BUNDLED/$rel" ]; then
        rm -f "$BUNDLED/$rel"
        echo "[skills-allowlist] pruned colliding template file: $rel"
    else
        echo "[skills-allowlist] note: colliding template file already absent: $rel" >&2
    fi
done < <(colliding_template_files)

while :; do
    before=$(find "$BUNDLED" -type d | wc -l)
    while IFS= read -r -d '' desc; do
        cat_dir=$(dirname "$desc")
        siblings=$(find "$cat_dir" -mindepth 1 -maxdepth 1 -type d | wc -l)
        [ "$siblings" -eq 0 ] && rm -rf "$cat_dir"
    done < <(find "$BUNDLED" -name DESCRIPTION.md -print0)
    find "$BUNDLED" -mindepth 1 -type d -empty -delete
    after=$(find "$BUNDLED" -type d | wc -l)
    [ "$before" -eq "$after" ] && break
done

echo "[skills-allowlist] kept $(find "$BUNDLED" -name SKILL.md | wc -l) skills"
