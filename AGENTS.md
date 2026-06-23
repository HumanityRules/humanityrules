# Project synopsis

**Humanity Rules** is an enterprise platform for deploying governed AI assistants inside a company’s own cloud (AWS VPC). Every employee gets a real AI agent with tool execution, persistent memory, and multi-channel access (Slack, web, a personal URL), running isolated and always-on in the company’s own account.

- **The Problem:** AI agents today run client-side, on employees’ laptops, creating a security and governance nightmare. The company can’t see or control what employees do with them, credentials and data leak through unvetted plugins/MCP servers, and the setup locks the company into a single lab’s model and harness. Agents running on third-party accounts instead of the company’s own cloud are not acceptable for regulation and audit.
- **The Solution:** Deploy each employee’s assistant into the company’s VPC with governance built in from the start: curated, security-audited skills; model-provider control (e.g., Bedrock, so nothing leaves the VPC); sandboxed execution; tight ABAC permissions; Okta/SSO auth; approval workflows; and full audit trails.
- **The moat:** Not the model, not the harness — the enterprise delivery platform. We sell trust: a curated, centrally controlled, audited ecosystem of agentic tools with sensible security defaults that are easy to configure.
- **Cloud:** AWS-first (ECS/Fargate, CloudFormation/CDK). Additional cloud providers will follow.
- **Stage:** Pre-beta. The platform architecture (ABAC, approval workflows, Fargate deployment, secrets management) maps directly onto governed AI assistants.

**"Humanity Rules" (abbreviated "HumR") is the product identity, used everywhere.** The code, infrastructure, and environment variables use the `humanityrules` / `humr` / `HUMR_` identifiers throughout (AWS resource names use the short `humr`; Python packages use the full `humanityrules`).

HumR chooses the open-source Hermes agent as its agent harness, and the Hermes WebUI as the UI for users. 

The Hermes agent that HumR deploys is located at template_repos/hermes_agent/. HumR deploys Hermes using the AppTemplate mechanism.

HumR's control plane is called CP.


# Running Python Commands

**Always use `uv run` to execute Python commands.** This project uses uv for dependency management — never run Python or project scripts directly.


# Writing Python Code

**When authoring or modifying any `.py` file in this project, invoke the `python-style` skill.** 


# Multi-tenancy

**Every query on a tenant-owned model must be scoped to the caller's org.** When fetching a row by a client-supplied id, the org filter (direct or transitive) is part of the lookup — never a separate verify-after step.


# Django templates

**`{# #}` comments are single-line only — they leak into the rendered page if wrapped across lines.** Use `{% comment %}...{% endcomment %}` for any multi-line comment.


# Browser Testing

**For local dev login (`/auth/dev-login/` or session-cookie curl), see `docs/local_dev_login.md`.**


# Git

**Commit directly to `main`.** This repo does not use feature branches — do not create a branch before committing unless explicitly asked.


# Documentation

See **`docs/AGENTS.md`** for the documentation index.


# Journal

**Do not write to `docs/journal.md` unless explicitly ordered.** The journal is updated only when the user asks (e.g., `/journal` or `/journal commit`).


# Markdown formatting

**Avoid markdown tables.** They render poorly in terminals and diffs. Use bulleted lists with bold labels instead.


# Writing rules for agents

**Agent-facing rules nudge for what the agent already knows, and convey what it had to discover by acting.** When adding to AGENTS.md or a skill, skip explanations and examples the next agent could write itself from its own memory, which is the same as yours. Keep the codebase-specific facts — for example paths, model and function names, project conventions — that it couldn't otherwise know.

**Freshness misleads.** Whatever occupied your attention while doing the work feels load-bearing in the document you write at the end. To a reader who didn't share that hour — including yourself days later — most of it isn't. The test: would this sentence earn its place if you'd written it cold, with no recent context? If it only makes sense in light of what you just did, drop it.

**Prefer numbered lists in replies.** When presenting options, recommendations, or multi-item analysis to the user, use numbered lists instead of bullet points so they can refer to items by number ("do 2 and 4").
