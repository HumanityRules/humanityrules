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


# Browser Testing (Local Dev Login)

The app uses WorkOS AuthKit for authentication, which requires an external OAuth flow. For local browser testing, use the **dev login endpoint** (available only when `DEBUG=True`):

```
http://127.0.0.1:8000/auth/dev-login/
```

This auto-logs in as the first superuser and redirects to `/dashboard/`. Use the `next` query param to land on a specific page:

```
http://127.0.0.1:8000/auth/dev-login/?next=/deploy/new/default/<repo-id>/
```

For `curl` testing, create a session directly and use it as a cookie:

```bash
uv run manage.py shell -c "
from django.contrib.sessions.backends.db import SessionStore
from devopshero_app.models import User
u = User.objects.get(email='vmendi@gmail.com')
s = SessionStore()
s['_auth_user_id'] = str(u.pk)
s['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
s['_auth_user_hash'] = u.get_session_auth_hash()
s.create()
print(s.session_key)
"
```

Then pass the printed session key: `curl -b "sessionid=<key>" http://127.0.0.1:8000/...`


# Documentation

See **`docs/AGENTS.md`** for the documentation index.


# Journal

**Do not write to `docs/journal.md` unless explicitly ordered.** The journal is updated only when the user asks (e.g., `/journal` or `/journal commit`). Never add entries proactively at the end of a task.


# Markdown formatting

**Avoid markdown tables.** They render poorly in terminals and diffs. Use bulleted lists with bold labels instead.
