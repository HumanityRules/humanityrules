# Project synopsis

**Humanity Rules (HumR) is the enterprise home for AI agents.** It gives employees an always-on agent that remembers context, works across channels, and continues after the laptop closes—without requiring them to install privileged software or operate agent infrastructure.

HumR lets employees build directly with agents, turn successful workflows into apps, and share those apps with their teams in a governed, auditable way.

HumR’s product POV is that agents should be easy enough for non-technical employees to put to work while being owned and operated as company infrastructure—not left unsupervised on employee laptops or rented as a strategic runtime from a model lab. HumR runs agents as isolated workloads in the customer’s cloud, with brokered credentials, scoped permissions, approved tools and models, SSO, ABAC policies, human approvals, and audit trails.

**What makes HumR different is that the customer owns the agent platform:** its runtime, identity, permissions, integrations, operational history, and choice of models.

**Stage:** Pre-beta and pre-product-market fit, with no customers yet.

**Implementation facts:** “Humanity Rules” and “HumR” are the product identity. Code and infrastructure use `humanityrules`, `humr`, and `HUMR_`; AWS resources use `humr`, and Python packages use `humanityrules`. HumR uses the open-source Hermes Agent (HA) as its harness and Hermes WebUI as its primary UI. The deployed Hermes template is under `template_repos/hermes_agent/` and is deployed through the AppTemplate mechanism. HumR’s control plane is called CP.


# Running Python Commands

**Always use `uv run` to execute Python commands.** This project uses uv for dependency management — never run Python or project scripts directly.

# Documentation

See **`docs/AGENTS.md`** for the documentation index.


# Writing Python Code

**When authoring or modifying any `.py` file in this project, invoke the `python-style` skill.** 


# Multi-tenancy

**Every query on a tenant-owned model must be scoped to the caller's org.** When fetching a row by a client-supplied id, the org filter (direct or transitive) is part of the lookup — never a separate verify-after step.


# Browser Testing

**For local dev login (`/auth/dev-login/` or session-cookie curl), see `docs/local_dev_login.md`.**


# Git

**Work directly on the `main` branch.** Do not create a branch unless explicitly asked.


# Writing rules for agents

**Prefer numbered lists in replies.** When presenting options, recommendations, or multi-item analysis to the user, use numbered lists instead of bullet points so they can refer to items by number ("do 2 and 4").

**Avoid markdown tables.** They render poorly in terminals and diffs. Use bulleted lists with bold labels instead.

**Do not write to `docs/journal.md` unless explicitly ordered.** The journal is updated only when the user asks (e.g., `/journal` or `/journal commit`).
