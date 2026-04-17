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

See **`docs/AGENTS.md`** for the documentation index (main docs and subsystem AGENTS.md files).


# Journal

**Do not write to `docs/journal.md` unless explicitly ordered.** The journal is updated only when the user asks (e.g., `/journal` or `/journal commit`). Never add entries proactively at the end of a task.


# Django 6.0 Async ORM

**Use native async ORM methods instead of `sync_to_async`.** Django 6.0 has full async ORM support — don't wrap sync methods.

- **Queries:** Use `await queryset.afirst()`, `await queryset.aget()`, `await queryset.acount()`, etc.
- **Create/Update:** Use `await Model.objects.acreate(...)`, `await instance.asave()`, `await instance.adelete()`
- **Iteration:** Use `async for obj in queryset:` instead of wrapping sync iteration

**Bad (outdated pattern):**
```python
from asgiref.sync import sync_to_async

async def get_user(user_id):
    return await sync_to_async(User.objects.get)(id=user_id)

async def save_item(item):
    await sync_to_async(item.save)()
```

**Good (Django 6.0):**
```python
async def get_user(user_id):
    return await User.objects.aget(id=user_id)

async def save_item(item):
    await item.asave()
```

The `a`-prefixed methods are native async and don't require `sync_to_async` wrappers.



# Python style guide

## Function Signatures & Calling Conventions

To prioritize readability and eliminate "magic" behavior, we enforce strict explicitness in both how functions are defined and how they are invoked.

### 1. Enforce Explicit Argument Passing
* **Directive:** Do not use default parameter values in function or method definitions. All parameters must be mandatory.
* **Reasoning:** "Magic defaults" hide complexity and obscure the function's dependencies. We prefer explicit calls where every argument is visible at the call site.
* **Implementation:** If a value is logically optional, the caller must explicitly pass `None`.

**Bad (Implicit Defaults):**
```python
def connect_to_db(url, retries=3, timeout=30):
    # Hidden behavior: Reader assumes 0 retries? Infinite timeout?
    ...
```

**Good (Explicit Arguments):**
```python
def connect_to_db(url, retries, timeout):
    # All dependencies are visible in the signature
    ...
```

### 2. Prefer Keyword Arguments
* **Directive:** Use named arguments (keyword arguments) for function calls, particularly when passing literals (numbers, booleans, or strings) or when a function takes multiple parameters.
* **Reasoning:** Positional arguments are brittle and often illegible (the "Boolean Trap"). Keyword arguments make the code self-documenting and prevent errors caused by parameter reordering.
* **Implementation:** Explicitly state the parameter name at the call site.

**Bad (Positional Ambiguity):**
```python
# The reader cannot know what '3' and '30' represent
connect_to_db("db://localhost", 3, 30)

# Confusing booleans
create_user("jdoe", True, False)
```

**Good (Self-Documenting):**
```python
# Clarity is enforced at the call site
connect_to_db(url="db://localhost", retries=3, timeout=30)

# Intent is obvious even for optional flows
connect_to_db(url="db://localhost", retries=None, timeout=None)

# Boolean flags are readable
create_user(username="jdoe", is_admin=True, send_email=False)
```

### 3. Prefer Module-Qualified Imports for Local Modules
* **Directive:** For local/project modules, use `import module` rather than `from module import function`. Then call functions with the module prefix.
* **Reasoning:** Explicit module prefixes make dependencies visible at every call site. The reader instantly knows where a function comes from without scrolling to imports.
* **Implementation:** Import the module, then use `module.function()` syntax.

**Bad (Ambiguous Origin):**
```python
from iam_utils import get_assumed_role_session
from vpc_utils import find_available_vpc_cidr

# Reader can't tell where these come from without checking imports
session = get_assumed_role_session(...)
cidr = find_available_vpc_cidr(...)
```

**Good (Explicit Module):**
```python
import iam_utils
import vpc_utils

# Origin is immediately clear at the call site
session = iam_utils.get_assumed_role_session(...)
cidr = vpc_utils.find_available_vpc_cidr(...)
```

**Note:** This applies to local project modules. Standard library and well-known third-party packages (e.g., `from pathlib import Path`, `from dataclasses import dataclass`) are fine to import directly since their origin is universally understood.

### 4. Prefer Direct Imports Over TYPE_CHECKING
* **Directive:** Use regular imports for type hints. Only use `TYPE_CHECKING` when actually needed to resolve circular imports.
* **Reasoning:** `TYPE_CHECKING` adds complexity (conditional imports, string annotations) for a problem that may not exist. Solve circular dependencies when they occur, not preemptively.
* **Implementation:** Import types directly and use them in annotations.

**Bad (Premature Optimization):**
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from myapp.models import User

def get_user(id: int) -> "User":  # String annotation required
    ...
```

**Good (Direct Import):**
```python
from myapp.models import User

def get_user(id: int) -> User:
    ...
```

**When TYPE_CHECKING is appropriate:**
- Actual circular import errors occur
- Heavy imports cause measurable startup delays
- Imports have side effects you need to avoid

### 5. Single-Line Function Signatures When Under 140 Characters
* **Directive:** Write function and method definitions on a single line when the total length (including `def`, parameters, return type, and trailing colon) is under 140 characters. Use multi-line formatting only when the signature exceeds this limit.
* **Reasoning:** Single-line signatures are easier to scan and search. Multi-line formatting adds vertical noise for short signatures that fit comfortably on one line.
* **Implementation:** Measure the full signature length. If under 140 characters, keep it on one line.

**Bad (unnecessary multi-line):**
```python
async def _handle_stream_event(
    message: SDKStreamEvent,
    ctx: StreamingContext,
) -> AsyncGenerator[StreamEvent, None]:
    ...
```

**Good (118 characters, fits on one line):**
```python
async def _handle_stream_event(message: SDKStreamEvent, ctx: StreamingContext) -> AsyncGenerator[StreamEvent, None]:
    ...
```

**When multi-line is appropriate:**
- Signature exceeds 140 characters
- Complex default values or annotations that benefit from vertical alignment (though we avoid defaults per rule 1)

### 6. Prefer Single-Line Docstrings for Simple Functions
* **Directive:** Use a brief single-line docstring for functions. Reserve multi-line docstrings (with Args/Returns/Raises sections) for complex functions where the signature alone doesn't convey important details.
* **Reasoning:** Verbose docstrings that restate what the function name and types already communicate add noise. A concise one-liner maintains consistency while avoiding redundancy.
* **Implementation:** Write a single-line docstring that adds context beyond the function name, or simply summarizes intent.

**Bad (overly verbose):**
```python
async def _aget_last_user_message(conversation: Conversation) -> str:
    """
    Get the last user message from a conversation.

    Args:
        conversation: The Conversation model instance.

    Returns:
        The content of the last user message.

    Raises:
        ValueError: If no user messages found.
    """
```

**Good (concise single-line):**
```python
async def _aget_last_user_message(conversation: Conversation) -> str:
    """Get the content of the most recent user message."""
```

**When multi-line docstrings are valuable:**
- Non-obvious behavior or side effects
- Complex algorithms that need explanation
- Public API functions where discoverability matters
- Unusual parameter constraints not captured by types

### 7. Require Type Hints on New/Modified Functions and Methods
* **Directive:** Every new or modified function/method must include type hints for all parameters and the return type.
* **Reasoning:** Complete signatures improve readability, reduce ambiguity, and make refactoring/tooling safer.
* **Implementation:** Annotate all parameters and returns explicitly. Use `-> None` for no-return functions. Keep annotations concrete when possible.

**Bad (missing annotations):**
```python
def check_access(request, resource, action):
    ...
```

**Good (fully annotated):**
```python
def check_access(request: HttpRequest, resource: Workspace, action: str) -> bool:
    ...
```


# Logging

**Use `logger.error()` instead of `logger.warning()`.** We don't use the warning level — if something is worth logging as abnormal, log it as an error.


# File Naming Conventions

**Avoid generic file names** like `service.py`, `client.py`, `list.html`, `view.html`. Use descriptive names that include the domain context.

**Reasoning:** Generic names become ambiguous as the project grows. With 20 modules, having multiple `service.py` files makes navigation and searching difficult.

**Bad:**
```
services/agent/service.py
services/agent/client.py
templates/chat/list.html
templates/chat/view.html
```

**Good:**
```
services/agent/agent_service.py
services/agent/agent_client.py
templates/chat/chat_list.html
templates/chat/chat_view.html
```

The prefix should match the containing folder or domain. This makes file names unique and self-documenting even when viewed in isolation (e.g., in editor tabs, search results, or stack traces).


# Markdown formatting

**Avoid markdown tables.** They render poorly in terminals and diffs. Use bulleted lists with bold labels instead.

**Bad:**
```markdown
| Command | Description |
|---------|-------------|
| list | Show items |
| add | Create item |
```

**Good:**
```markdown
- **list** — Show items
- **add** — Create item
```


