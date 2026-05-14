---
name: ask-codex
description: Query GPT-5.5 for external perspective on current problem
disable-model-invocation: true
---

# Ask Codex

Query GPT-5.5 for external perspective. Codex has NO conversation context.

## Steps

### 1. Synthesize
- Objective and success criteria
From the current conversation (ignore resolved topics):
- Constraints
- What's been tried
- Specific blocker

### 2. Formulate self-contained question
Include relevant code, technical problem, constraints, and what kind of insight would help.

End with: "Structure response as: Summary (bullets), Recommendations (ranked), Risks, Next actions, Questions."

### 3. Execute

```bash
QUESTION="$(cat <<'EOF'
...your multiline question here...
EOF
)"

codex exec --skip-git-repo-check \
  --sandbox workspace-write \
  --model gpt-5.5 \
  -c model_reasoning_effort="xhigh" \
  "$QUESTION"
```

The final answer prints to stdout after a metadata header and a `tokens used` footer — ignore those and use the assistant message.

### 4. Present the insight

- Summarize what Codex suggested
- Evaluate it critically — it's not an authority, just another perspective
- Identify useful ideas that could apply to our problem
- Note any disagreements or alternative approaches you'd recommend
