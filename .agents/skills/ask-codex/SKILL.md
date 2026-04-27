---
name: ask-codex
description: Query GPT-5.5 for external perspective on current problem
disable-model-invocation: true
---

# Ask Codex

Query GPT-5.4 for external perspective. Codex has NO conversation context.

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

OUT="$(mktemp).txt"

codex exec --skip-git-repo-check \
  --full-auto \
  --model gpt-5.5 \
  -c model_reasoning_effort="xhigh" \
  -o "$OUT" \
  "$QUESTION" < /dev/null

EXIT_CODE=$?
if [ $EXIT_CODE -eq 0 ]; then
  cat "$OUT"
else
  echo "Codex failed (exit=$EXIT_CODE)"
fi
```

The `-o` flag writes the final answer to a file, avoiding TTY buffering issues in non-interactive shells.
- Exit `0`: success (answer in output file)
- Non-zero: failure

### 4. Present the insight

- Summarize what Codex suggested
- Evaluate it critically — it's not an authority, just another perspective
- Identify useful ideas that could apply to our problem
- Note any disagreements or alternative approaches you'd recommend
