# Relays a turn-by-turn conversation between two AI coding agents: Claude Code and OpenAI Codex.
#
# Each side runs as a real resumable CLI session (`claude -p --resume`, `codex exec resume`), so
# both agents experience a genuine multi-turn dialogue with proper user/assistant framing — the
# script only ever passes the newest message across. A conversation lives in its own folder next
# to this script:
#
#   duet/<conversation>/topic.md          you write this; optional frontmatter `first: claude|codex`
#   duet/<conversation>/conversation.md   the transcript, appended turn by turn
#   duet/<conversation>/state.json        session ids and progress; re-running the command resumes
#
# Start or continue a conversation with:
#
#   uv run duet/relay.py duet/<conversation> --rounds 10
#
# Both agents run fully unsandboxed with their cwd set to the conversation folder. Either agent
# can end the conversation by emitting [END] on its own line.

import argparse
import json
import os
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CLAUDE_MODEL = "fable"
CLAUDE_EFFORT = "xhigh"
CODEX_MODEL = "gpt-5.6-sol"
CODEX_REASONING = 'model_reasoning_effort="xhigh"'
REPO_ROOT = Path(__file__).resolve().parent.parent

DISPLAY = {"claude": "Claude", "codex": "Codex", "victor": "Victor"}

CONCLUSIONS_PROMPT = (
    "The conversation is over. Write the closing report for Victor: the conclusions reached, "
    "the key agreements, any disagreements left unresolved, and concrete decisions or artifacts "
    "produced. Claude will not see this message. Start directly with the content, no heading."
)


def parse_topic(conv_dir: Path) -> tuple[str, str]:
    """Return (first_speaker, topic_text) from topic.md; frontmatter is optional."""
    lines = (conv_dir / "topic.md").read_text().splitlines()
    first = "codex"
    body_start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body_start = i + 1
                break
            key, _, value = lines[i].partition(":")
            if key.strip() == "first":
                first = value.strip()
    if first not in ("claude", "codex"):
        sys.exit(f"topic.md: `first` must be claude or codex, got {first!r}")
    topic = "\n".join(lines[body_start:]).strip()
    if not topic:
        sys.exit("topic.md has no topic text")
    return first, topic


def preamble(name: str, counterpart: str, topic: str, conv_dir: Path) -> str:
    return f"""You are {DISPLAY[name]}, an AI agent, in a collaborative conversation with {DISPLAY[counterpart]}, another AI agent. A relay script passes messages between you; the human (Victor) reads the transcript but does not participate. Every prompt you receive from now on is {DISPLAY[counterpart]} speaking to you, verbatim.

The topic:
```
{topic}
```

Ground rules:
- Disagree when you need to disagree. Be adversarial and push back on each other's ideas. Do not converge out of politeness. Do not be sycophantic.
- This is a dialogue, not a report: make your point(s) and hand the turn back.
- Victor may occasionally interject to steer the conversation; his messages arrive inside the incoming prompt, clearly marked as [Victor interjects].
- When you believe the conversation has run its course, end your message with [END] on its own line."""


def scrubbed_env() -> dict[str, str]:
    """Drop this process's Claude-session env so a nested `claude` authenticates on its own."""
    return {k: v for k, v in os.environ.items() if k != "ANTHROPIC_BASE_URL" and not k.startswith("CLAUDE")}


def run_or_die(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, cwd=cwd, env=scrubbed_env(), stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"{cmd[0]} failed (exit {proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return proc


def call_claude(prompt: str, session_id: str | None, cwd: Path) -> tuple[str, str]:
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", CLAUDE_MODEL, "--effort", CLAUDE_EFFORT, "--dangerously-skip-permissions"]
    if session_id:
        cmd += ["--resume", session_id]
    proc = run_or_die(cmd=cmd, cwd=cwd)
    data = json.loads(proc.stdout)
    if data.get("is_error"):
        sys.exit(f"claude returned an error: {data.get('result')}")
    return data["result"], data["session_id"]


def call_codex(prompt: str, session_id: str | None, cwd: Path) -> tuple[str, str]:
    fd, out_path = tempfile.mkstemp(prefix="codex_last_")
    os.close(fd)
    cmd = ["codex", "exec"]
    if session_id:
        cmd += ["resume", session_id]
    cmd += [
        "--json", "-o", out_path, "-m", CODEX_MODEL, "-c", CODEX_REASONING,
        "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", prompt,
    ]
    proc = run_or_die(cmd=cmd, cwd=cwd)
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "thread.started":
            session_id = event["thread_id"]
    reply = Path(out_path).read_text().strip()
    os.unlink(out_path)
    if not session_id or not reply:
        sys.exit(f"codex output missing session id or reply:\n{proc.stdout[-2000:]}")
    return reply, session_id


def check_interject() -> str | None:
    """Pick up a message typed while the agents were working: bare Enter opens a prompt, typed text is the message itself."""
    if not sys.stdin.isatty():
        return None
    lines = []
    while select.select([sys.stdin], [], [], 0)[0]:
        line = sys.stdin.readline()
        if line == "":
            return None
        lines.append(line.strip())
    if not lines:
        return None
    message = " ".join(l for l in lines if l).strip()
    if message:
        return message
    try:
        message = input("interject> ").strip()
    except EOFError:
        return None
    return message or None


def register_steer(state: dict, text: str) -> None:
    """Queue Victor's message for both agents: appended for the next speaker, prepended for the one who already spoke."""
    for name in ("claude", "codex"):
        place = "after" if name == state["next"] else "before"
        state["steer_pending"][name] = {"text": text, "place": place}


def apply_steer(name: str, message: str, state: dict) -> str:
    pending = state["steer_pending"][name]
    if not pending:
        return message
    state["steer_pending"][name] = None
    if pending["place"] == "after":
        return f"{message}\n\n[Victor interjects]: {pending['text']}"
    return f"[Victor interjects]: {pending['text']}\n\n{message}"


def call_agent(name: str, prompt: str, state: dict, cwd: Path) -> str:
    print(f"[{DISPLAY[name]} is thinking...]", flush=True)
    started = time.monotonic()
    if name == "claude":
        reply, state["claude_session"] = call_claude(prompt=prompt, session_id=state["claude_session"], cwd=cwd)
    else:
        reply, state["codex_session"] = call_codex(prompt=prompt, session_id=state["codex_session"], cwd=cwd)
    print(f"[{DISPLAY[name]} answered in {time.monotonic() - started:.0f}s]", flush=True)
    return reply


def append_turn(conv_dir: Path, label: str, text: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M")
    block = f"\n## {label} ({stamp})\n\n{text}\n"
    with open(conv_dir / "conversation.md", "a") as f:
        f.write(block)
    print(block, flush=True)


def save_state(conv_dir: Path, state: dict) -> None:
    (conv_dir / "state.json").write_text(json.dumps(state, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Relay a conversation between Claude Code and Codex.")
    parser.add_argument("conversation", help="conversation folder containing topic.md")
    parser.add_argument("--rounds", type=int, default=10, help="max exchange pairs; re-run with a higher value to extend")
    parser.add_argument("--say", help="interject this message before continuing; also revives an [END]-ed conversation")
    args = parser.parse_args()

    conv_dir = Path(args.conversation).resolve()
    first, topic = parse_topic(conv_dir=conv_dir)
    second = "codex" if first == "claude" else "claude"
    state_path = conv_dir / "state.json"

    if state_path.exists():
        state = json.loads(state_path.read_text())
        if args.say:
            register_steer(state=state, text=args.say)
            append_turn(conv_dir=conv_dir, label="Victor", text=args.say)
            state["ended"] = False
            save_state(conv_dir=conv_dir, state=state)
    else:
        if args.say:
            sys.exit("--say needs an existing conversation; write the steer into topic.md instead")
        state = {
            "claude_session": None, "codex_session": None, "turns": 0, "next": first, "last": None,
            "ended": False, "summarized": False, "steer_pending": {"claude": None, "codex": None},
        }
        header = f"# {conv_dir.name}\n\nClaude: {CLAUDE_MODEL} ({CLAUDE_EFFORT}) · Codex: {CODEX_MODEL} (xhigh) · first: {DISPLAY[first]}\n"
        (conv_dir / "conversation.md").write_text(header)

        seed = preamble(name=second, counterpart=first, topic=topic, conv_dir=conv_dir)
        seed += f"\n\n{DISPLAY[first]} speaks first. For now, reply with just: Ready."
        ready = call_agent(name=second, prompt=seed, state=state, cwd=conv_dir)
        print(f"[{DISPLAY[second]}: {ready}]", flush=True)

        seed = preamble(name=first, counterpart=second, topic=topic, conv_dir=conv_dir)
        seed += "\n\nYou speak first. Give your opening turn now."
        opening = call_agent(name=first, prompt=seed, state=state, cwd=conv_dir)
        state.update(turns=1, next=second, last=opening, ended="[END]" in opening)
        append_turn(conv_dir=conv_dir, label=f"{DISPLAY[first]} — turn 1", text=opening)
        save_state(conv_dir=conv_dir, state=state)

    if sys.stdin.isatty():
        print("[press Enter anytime to interject after the current turn]", flush=True)

    while not state["ended"] and state["turns"] < args.rounds * 2:
        steer = check_interject()
        if steer:
            register_steer(state=state, text=steer)
            append_turn(conv_dir=conv_dir, label="Victor", text=steer)
            save_state(conv_dir=conv_dir, state=state)
        speaker = state["next"]
        prompt = apply_steer(name=speaker, message=state["last"], state=state)
        reply = call_agent(name=speaker, prompt=prompt, state=state, cwd=conv_dir)
        state.update(turns=state["turns"] + 1, next="codex" if speaker == "claude" else "claude", last=reply)
        state.update(ended="[END]" in reply, summarized=False)
        append_turn(conv_dir=conv_dir, label=f"{DISPLAY[speaker]} — turn {state['turns']}", text=reply)
        save_state(conv_dir=conv_dir, state=state)

    if state["ended"]:
        ender = "codex" if state["next"] == "claude" else "claude"
        print(f"[conversation ended by {DISPLAY[ender]} via [END]]")
    else:
        print(f"[round cap reached at {state['turns']} turns — re-run with a higher --rounds to continue]")

    if not state["summarized"]:
        prompt = "[Relay script, not Claude]: "
        if state["next"] == "codex" and state["last"]:
            prompt += f"Claude's final message, which was not yet delivered to you:\n\n{state['last']}\n\n"
        prompt += CONCLUSIONS_PROMPT
        report = call_agent(name="codex", prompt=prompt, state=state, cwd=conv_dir)
        state["summarized"] = True
        append_turn(conv_dir=conv_dir, label="Codex — conclusions", text=report)
        save_state(conv_dir=conv_dir, state=state)


if __name__ == "__main__":
    main()
