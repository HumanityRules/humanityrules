# Project synopsis

**DevOps Hero** (DOH) is an AI-native, deployment platform for internal apps. It lets anyone in the company who can vibe-code an app deploy it to the company’s AWS infrastructure in minutes — with governance, compliance, and security built in from the start.

- **The Problem:** Vibe-coding has made building internal apps dramatically faster, but deploying them inside a company’s infrastructure is still a weeks-long bottleneck. Permissions, SSO, networking, approval chains, audit readiness — the gap between "it works on my laptop" and "people in the company can use it" kills momentum and buries good ideas.
- **The Solution:** An AI-native deployment platform that handles the entire journey from code to production. The AI drives the deployment process: it configures infrastructure, sets up least-privilege IAM permissions, wires up networking, and guides users through approval workflows — all inside the company’s own AWS account.
- **Two equal pillars:**
    - **Fast self-serve deployment:** Deploy directly to your company’s VPC without writing Terraform, YAML, or opening tickets. The AI handles infrastructure provisioning, networking, and configuration.
    - **Built-in governance:** Approval chains, audit trails, least-privilege IAM, and policy enforcement are automatic — not bolted on after the fact. DevOps, security, and compliance teams get the control and visibility they need without being a bottleneck.
- **Target Audience:** Anyone in the company capable of vibe-coding an app — engineers, data scientists, operations staff, business analysts. The platform makes it possible for them to quickly deploy and share their work with the company, while keeping the DevOps team, the CIO, the CISO, and the auditors happy.
- **Cloud:** AWS-first (ECS/Fargate, CloudFormation/CDK). Additional cloud providers may follow.
- **Stage:** Pre-beta. Features are paused while we validate the core idea with potential customers. Ideal Customer Profile is being defined through discovery conversations.

With DevOps Hero, ship; let bots assist.
Because your code deserves to be running in production, not stuck on your laptop.

Built with ❤️ to make deployment accessible to everyone.

We abbreviate the name of DevOps Hero as DOH.


# Running Python Commands

**Always use `uv run` to execute Python commands.** This project uses uv for dependency management — never run Python or project scripts directly.


# Writing Python Code

**When authoring or modifying any `.py` file in this project, invoke the `python-style` skill.** It holds the project's Python style rules (function signatures, argument passing, imports, type hints, docstrings, Django async ORM, logging level, file naming). Loading it only when needed keeps this file lean.


# Multi-tenancy

**Every query on a tenant-owned model must be scoped to the caller's org.** When fetching a row by a client-supplied id, the org filter (direct or transitive) is part of the lookup — never a separate verify-after step.


# Browser Testing

**For local dev login (`/auth/dev-login/` or session-cookie curl), see `docs/local_dev_login.md`.**


# Git

**Commit directly to `main`.** This repo does not use feature branches — do not create a branch before committing unless explicitly asked.


# Documentation

See **`docs/AGENTS.md`** for the documentation index.


# Journal

**Do not write to `docs/journal.md` unless explicitly ordered.** The journal is updated only when the user asks (e.g., `/journal` or `/journal commit`). Never add entries proactively at the end of a task.


# Markdown formatting

**Avoid markdown tables.** They render poorly in terminals and diffs. Use bulleted lists with bold labels instead.


# Writing rules for agents

**Agent-facing rules nudge for what the agent already knows, and convey what it had to discover by acting.** When adding to AGENTS.md or a skill, skip explanations and examples the next agent could write itself from its own memory, which is the same as yours. Keep the codebase-specific facts — for example paths, model and function names, project conventions — that it couldn't otherwise know.

**Freshness misleads.** Whatever occupied your attention while doing the work feels load-bearing in the document you write at the end. To a reader who didn't share that hour — including yourself days later — most of it isn't. The test: would this sentence earn its place if you'd written it cold, with no recent context? If it only makes sense in light of what you just did, drop it.

**Prefer numbered lists in replies.** When presenting options, recommendations, or multi-item analysis to the user, use numbered lists instead of bullet points so they can refer to items by number ("do 2 and 4").
