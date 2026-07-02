## Effort (main loop)
Effort is set by the user via /model — you can't change it.
Within it: think hard on tricky debugging, multi-file refactors,
and architecture; don't overthink formatting, renames, or
boilerplate.

## Subagents (effort + model)
You choose model and effort per subagent:
- Default: Sonnet 5, medium effort.
- Opus 4.8 / high effort only when a subagent failed twice or
  the task needs deep reasoning (system design, subtle
  correctness).


## Another (better) approach: 
https://x.com/diegocabezas01/status/2072436501263339841
  
```
Use Fable 5 as orchestrator and Opus + Codex to execute (to save fable usage):  

Fable 5 (max reasoning) = orchestrator 
Opus = deep reasoning subagent 
Sonnet = mechanical work subagent 
Codex = peer Sr. engineer, different perspective  

Setup:  
1. Set Fable 5 as your main model  In Claude Code: /model → Fable 5 → reasoning /effort to max

2. Create 2 subagents with /agents In Claude Code:  
deep-reasoner → pinned to opus "Use for reasoning-heavy phases, architecture, debugging complex issues, algorithm design. Think thoroughly, return a concise conclusion the orchestrator can act on."  

fast-worker → pinned to sonnet "Use for mechanical tasks, boilerplate, tests, formatting, simple edits. Execute efficiently."

3. Add OpenAI's official Codex plugin (install codex cli in your computer first), In Claude Code type:
/plugin marketplace add openai/codex-plugin-cc
 /plugin install codex@openai-codex
 /codex:setup

4. Drop this in your CLAUDE.md in your folder: 

## Orchestration workflow  
You (Fable) are the orchestrator. Plan, decompose, synthesize.  
Reasoning-heavy phases → deep-reasoner  
Mechanical work → fast-worker  
Codex (/codex:rescue --background) is a cracked engineer on par with deep-reasoner, from a different perspective. Treat as a peer, not a reviewer.  
High-stakes decisions: task Opus + Codex on the same problem in parallel, synthesize the best of both, without showing either the other's answer. Keep your own context lean.   

5. Then prompt Fable like a tech lead:  "Goal: [what you want] Context: [files, constraints] You're the lead. Delegate reasoning to deep-reasoner, grunt work to fast-worker, fresh-perspective problems to Codex. Show me your plan first, then execute."  

That's it.
```