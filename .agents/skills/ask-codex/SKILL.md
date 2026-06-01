---
name: ask-codex
description: Manually query GPT-5.5 via Codex for an external perspective on the current problem. Use only when the user invokes /ask-codex or explicitly asks to ask Codex.
disable-model-invocation: true
---

# Ask Codex

Query GPT-5.5 for external perspective. Codex has NO conversation context.

Do not run Codex in the background. Use the bundled script below and wait for it to finish. The script prints only Codex's final assistant message to stdout; if it times out or fails, report that failure instead of polling an empty output file.

## Steps

### 1. Synthesize
From the current conversation (ignore resolved topics):
- Objective and success criteria
- Constraints
- What's been tried
- Specific blocker

### 2. Formulate self-contained question
Include relevant code, technical problem, constraints, and what kind of insight would help.

End with: "Structure response as: Summary (bullets), Recommendations (ranked), Risks, Next actions, Questions."

### 3. Execute

Write the question to a temporary file and run:

```bash
QUESTION_FILE="$(mktemp -t ask-codex-question.XXXXXX.md)"
cat > "$QUESTION_FILE" <<'EOF'
...your multiline question here...
EOF

.agents/skills/ask-codex/scripts/ask_codex.sh "$QUESTION_FILE"
```

Defaults:
- Model: `gpt-5.5`
- Effort: `xhigh`
- Fast mode: on (`service_tier="fast"`)
- Sandbox: `read-only`
- Timeout: 900 seconds

Use `ASK_CODEX_FAST=0` only if the fast service tier is causing a problem. For normal reviews, keep the prompt focused and avoid pasting huge diffs; include the specific files or hunks that matter.

### 4. Present the insight

- Summarize what Codex suggested
- Evaluate it critically — it's not an authority, just another perspective
- Identify useful ideas that could apply to our problem
- Note any disagreements or alternative approaches you'd recommend
