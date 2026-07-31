---
name: step-by-step
description: Discuss the topics of the previous message one at a time, advancing only on the user's explicit signal.
disable-model-invocation: true
argument-hint: "[optional extra instructions for this discussion]"
---

Additional instructions for this run: $ARGUMENTS

Your previous message covered several topics. The user wants to discuss them one at a time, not all at once.

First, restate the topics as a numbered agenda. If the message wasn't a numbered list, decompose it into one — each distinct topic gets its own entry.

Then open topic 1 and stop there.

Rules for the rest of the discussion:

- Never move to the next topic until the user gives an explicit signal that the current one is settled ("next", "ok", "done", or similar). A reply that merely engages with the topic is not a signal to advance — respond to it and stay on the topic.
- When the user says to advance, name the topic and restate it so they know where we are in the agenda.
