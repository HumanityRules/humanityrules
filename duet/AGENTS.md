# Duet

Relays a turn-by-turn conversation between two AI coding agents: Claude Code (`fable`, xhigh effort) and OpenAI Codex (`gpt-5.6-sol`, xhigh reasoning). Each side is a real resumable CLI session; the relay passes only the newest message across, so both agents experience a genuine dialogue. Victor reads the transcript but doesn't participate, aside from occasional interjections.

## Create a conversation

1. Create `duet/<name>/` and write `duet/<name>/topic.md`.
2. Run `uv run duet/relay.py duet/<name>` (default 10 rounds = 20 turns).

`topic.md` is plain text, optionally preceded by frontmatter naming who opens:

```
---
first: claude
---
```

Default is `codex`.

## What happens

- Either agent can end the conversation by putting `[END]` in its reply; otherwise it stops at the round cap — re-run with a higher `--rounds` to continue.
- Whenever the relay stops, Codex appends a closing `## Codex — conclusions` report addressed to Victor — topic.md doesn't need to ask for a summary.
- Ctrl-C is safe anytime; re-running the same command resumes from `state.json`.
- Agents run unsandboxed with cwd = the conversation folder and their full toolset, and the repo root is two directories up — but the preamble doesn't tell them any of this (see "Writing topic.md").

## Steering

- `--say "message"` interjects before continuing an existing conversation, and revives one that ended via `[END]`. It needs an existing `state.json` — for a conversation's first run, put the steer in topic.md instead.
- Victor can also interject live from his terminal. Either way, steers are marked `[Victor interjects]` in what the agents receive and logged as `## Victor` in the transcript.

## Writing topic.md

Each agent gets a preamble (identity + ground rules) before your topic text. It already covers: who each agent is, that they should push back rather than converge politely, that this is a dialogue not a report, the `[END]` convention, and how Victor's interjections are marked. Don't restate any of that — and don't assume it covers anything else.