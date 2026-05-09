#!/bin/bash
# Curate the bundled Hermes skill set. Only the DOH-approved skills listed in
# this script survive in the image; every other upstream bundled skill is
# removed at build time.
set -euo pipefail

BUNDLED=${1:?Usage: prune-skills.sh <bundled-skills-dir>}
KEEP_MARKER=.doh-keep

[ -d "$BUNDLED" ] || { echo "FATAL: bundled skills dir '$BUNDLED' missing" >&2; exit 1; }

allowlisted_skills() {
    cat <<'EOF'
autonomous-ai-agents/hermes-agent
creative/architecture-diagram
creative/ascii-art
creative/ascii-video
creative/baoyu-comic
creative/baoyu-infographic
creative/creative-ideation
creative/excalidraw
creative/manim-video
creative/p5js
creative/pixel-art
creative/popular-web-designs
data-science/jupyter-live-kernel
devops/webhook-subscriptions
email/himalaya
github/codebase-inspection
github/github-auth
github/github-code-review
github/github-issues
github/github-pr-workflow
github/github-repo-management
mcp/native-mcp
media/gif-search
media/youtube-content
note-taking/obsidian
productivity/linear
productivity/maps
productivity/nano-pdf
productivity/notion
productivity/ocr-and-documents
productivity/powerpoint
research/arxiv
research/blogwatcher
research/llm-wiki
research/polymarket
research/research-paper-writing
social-media/xurl
software-development/plan
software-development/requesting-code-review
software-development/subagent-driven-development
software-development/systematic-debugging
software-development/test-driven-development
software-development/writing-plans
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
