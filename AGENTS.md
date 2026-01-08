# Project synopsis

**DevOps Hero** is a platform designed to make deploying internal tools to a company's private cloud (VPC) as easy as using Heroku, Render, or Railway, while maintaining enterprise security and compliance.

- **The Problem:** While AI has made building apps faster than ever, deploying them internally is still a bottleneck due to complex requirements like IAM permissions, SSO integration, VPC networking, and compliance checks.
- **The Solution:** An AI-powered "co-pilot" that automates the deployment process and governance, allowing developers to ship internal apps in minutes rather than weeks.
- **Key Features:**
    - **Automated Infrastructure:** Deploys directly to your company’s VPC without requiring manual Terraform or YAML wrestling.
    - **AI-Assisted Security:** Uses an AI wizard to configure IAM permissions and set up approval chains.
    - **Built-in SDK:** Provides out-of-the-box integration for SSO, Role-Based Access Control (RBAC), and standardized logging/metrics.
    - **Governance:** Includes approval flows for sensitive changes to ensure compliance.
- **Target Audience:** It aims to empower Full Stack Engineers, Data Scientists, Machine Learning Engineers, and Business staff to "vibe-code" and ship tools independently, while giving DevOps teams the control and standardization they need.

We abbrebrivate the name of DevOps Hero as DOH.

# What I did to boostrap the project
```
uv init .
uv add django==6.0
uv run django-admin startproject devopshero_site .
uv run manage.py startapp devopshero_app
uv run manage.py migrate
uv run manage.py runserver
```


# Documentation

The main developer documentation lives in the docs/ subdirectory.

**Development Journal:** When trying to understand what, when, and why decisions were made, consult `docs/journal.md`. It contains chronological entries documenting the reasoning behind architectural choices, implementation decisions, and lessons learned.


# Django 6.0 Template Partials

**Prefer partials over plain `{% include %}`** — they're the modern Django 6.0 approach.

- **Same template:** `{% partialdef name %}...{% endpartialdef %}` then `{% partial name %}`
- **External template:** `{% include "path/to/template.html#partial_name" with foo=bar %}`


# HTMX Navigation Paradigm

We use htmx for SPA-like navigation without writing JavaScript. The server owns all UI state.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  app_shell.html (static frame)                              │
│  ┌──────────────┐  ┌─────────────────────────────────────┐  │
│  │ Sidebar      │  │ #main-content                       │  │
│  │ (nav links)  │  │                                     │  │
│  │              │  │  ← htmx swaps content here          │  │
│  │ #sidebar-nav │  │                                     │  │
│  │ -desktop     │  │                                     │  │
│  │ -mobile      │  │                                     │  │
│  └──────────────┘  └─────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Key Concepts

1. **App Shell Pattern**: `app_shell.html` is the outer frame with sidebar and header. Content loads into `#main-content` via htmx.

2. **Out-of-Band Swaps**: When navigating, the server returns both the page content AND updated sidebar elements with `hx-swap-oob="true"`. This keeps the sidebar active state in sync without JavaScript.

3. **Server-Owned State**: Active nav states are determined by Django template conditionals (`{% if item.is_active %}`), not client-side JavaScript.

## Adding a New Top-Level Page

### Step 1: Add to navigation_items in `views.py`

```python
# In get_app_shell_context()
navigation_items = [
    {"name": "Dashboard", "url": "/dashboard/", "icon": "dashboard", "is_active": current_page == "dashboard"},
    # ... existing items ...
    {"name": "NewPage", "url": "/newpage/", "icon": "newpage", "is_active": current_page == "newpage"},
]
```

### Step 2: Create the view in `views.py`

```python
@login_required
def newpage(request):
    if request.htmx:
        context = get_app_shell_context(current_page="newpage")
        return render(request, "devopshero_app/newpage.html", context=context)

    context = get_app_shell_context(current_page="newpage")
    context["content_url"] = "/newpage/"
    return render(request, "devopshero_app/app_shell.html", context=context)
```

### Step 3: Create the template `templates/devopshero_app/newpage.html`

```html
<div class="max-w-2xl">
    <h1 class="text-2xl font-bold text-gray-900 dark:text-white mb-4">New Page</h1>
    <p class="text-gray-600 dark:text-gray-400">Content here.</p>
</div>

{% include "devopshero_app/partials/_sidebar_oob.html" %}
```

**Important**: Always include `_sidebar_oob.html` at the end — this enables the out-of-band sidebar update.

### Step 4: Add the icon `templates/devopshero_app/partials/icons/newpage.html`

Create an SVG icon file for the sidebar.

### Step 5: Add URL route in `urls.py`

```python
path("newpage/", views.newpage, name="newpage"),
```

## Adding Nested Pages (like Settings subsections)

For pages with their own sub-navigation (tabs):

### Step 1: Create base template with tab nav (e.g., `settings.html`)

```html
<div class="max-w-4xl">
    <h1 class="text-2xl font-bold mb-6">Settings</h1>
    
    <!-- Tab Navigation -->
    <div class="flex h-12 border-b border-gray-200 dark:border-white/10">
        <div class="flex space-x-8">
            <a href="/settings/organization/"
               hx-get="/settings/organization/"
               hx-target="#main-content"
               hx-swap="innerHTML"
               hx-push-url="true"
               class="inline-flex items-center border-b-2 px-1 pt-1 text-sm font-medium
                      {% if active_tab == 'organization' %}border-indigo-600 text-gray-900{% else %}border-transparent text-gray-500 hover:border-gray-300{% endif %}">
                Organization
            </a>
            <!-- More tabs... -->
        </div>
    </div>

    <!-- Content block for subsections -->
    <div class="mt-6">
        {% block settings_content %}{% endblock %}
    </div>
</div>

{% include "devopshero_app/partials/_sidebar_oob.html" %}
```

### Step 2: Create subsection templates that extend the base

```html
{% extends "devopshero_app/settings.html" %}

{% block settings_content %}
<div>
    <h2 class="text-lg font-semibold">Organization Settings</h2>
    <!-- Subsection content -->
</div>
{% endblock %}
```

### Step 3: Create views for each subsection

```python
@login_required
def settings_organization(request):
    context = get_app_shell_context(current_page="settings")
    context["active_tab"] = "organization"
    
    if request.htmx:
        return render(request, "devopshero_app/settings/organization.html", context=context)
    
    context["content_url"] = "/settings/organization/"
    return render(request, "devopshero_app/app_shell.html", context=context)
```

**Key points:**
- `current_page="settings"` keeps the sidebar Settings item active
- `active_tab="organization"` controls which tab is highlighted
- Subsection templates extend the parent, so the whole settings section (nav + content) is returned
- `hx-target="#main-content"` means tab clicks replace the entire settings section, re-rendering the tab nav with correct active states

## File Structure

```
templates/devopshero_app/
├── app_shell.html              # Main layout with sidebar
├── dashboard.html              # Top-level page
├── workspaces.html             # Top-level page
├── settings.html               # Page with sub-navigation
├── settings/                   # Subsection templates
│   ├── organization.html       # Extends settings.html
│   ├── members.html
│   ├── aws_accounts.html
│   └── billing.html
└── partials/
    ├── _sidebar_nav.html       # Sidebar navigation content
    ├── _sidebar_oob.html       # OOB wrapper for htmx updates
    └── icons/                  # SVG icons for nav items
```

## Why This Works

1. **No JavaScript for nav state**: The server renders the correct active states every time.
2. **Browser history works**: `hx-push-url="true"` updates the URL, so back/forward work correctly.
3. **Direct URL access works**: Views handle both htmx requests (return partial) and full page loads (return app_shell with content_url).
4. **DRY**: Subsection templates extend their parent, so tab navigation is defined once.


# Authentication

Authentication is handled by [WorkOS AuthKit](https://workos.com/docs/user-management). The flow:

1. User visits any page → redirected to `/auth/login/`
2. User clicks "Continue with WorkOS" → redirected to WorkOS hosted auth
3. WorkOS authenticates user → redirects back to `/auth/callback/`
4. Callback exchanges code for user info, creates/updates Django user, logs them in

**Configuration:** Set `WORKOS_CLIENT_ID` and `WORKOS_API_KEY` in `.env`


## Browser Debugging Login for LLMs

When using browser tools to debug, log in via Django admin (`/admin/`) instead of the main login flow. WorkOS auth requires external redirects that don't work well with automated browser testing. Once authenticated through admin, click "View site" to access the app with an active session.


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



# Specification

### Naming and core concepts
* **Workspace:**
  * Primary unit of ownership, configuration, and governance.
  * Each Workspace has a primary Git repo (additional repos can be added later in v2).
* **Apps:**
  * Compute workloads users deploy.
* **Datastores:**
  * Stateful dependencies the platform provisions and manages (Aurora, DynamoDB in v1).
* **Environments:**
  * Deployment targets that are independent of any app.
  * Many apps can deploy into the same environment.
  * Branch/tag/commit selection occurs when creating a Deployment (binding an App to an Environment), along with deploy triggers.
  * v1 Environments are workspace-scoped (default and only scope in v1). There are no “shared environments across workspaces” (membership model) yet.
  * v1 supports two modes for Environment networking:
    * Managed networking: the platform creates a new VPC for the environment.
    * Existing VPC attachment.


### Compute substrate
* v1 supports ECS/Fargate only.
* Therefore, users do not choose substrate at deployment time in v1.
* ECS/Fargate is treated as an implicit environment capacity.


### High-level mental model
* Workspace contains definitions (Apps, Datastores) and owns Environments.
* Environment is where things run (AWS account + region + VPC + ECS baseline).
* Deployment binds an App to an Environment and selects the git source and triggers.
* Datastore provisioning creates datastore instances into an Environment (or into that environment’s network context).
* Binding explicitly attaches Apps to Datastore instances with specific permissions.
  

## Object model
### Organization
* A user can belong to many organizations.
* An organization can have many AWS accounts
  
### AWS Account
* Associated to a particular organization through a wizard flow.
* Cloudformation template that the customer has to execute on their account.


### Workspace
* workspace_id
* primary_repo
* RBAC policies (workspace admins, developers, viewers)
* Collections
  * Apps
  * Datastores
  * Environments

### Network configuration
A first-class concept representing the network a given Environment uses, implemented with a reusable NetworkProfile object.
* AWS Account reference
* Region
* VPC
* Subnet
  * private subnets for Fargate tasks
  * datastore subnet group inputs (Aurora needs subnets)
  
### Environment
An Environment defines the runtime fabric (ECS/Fargate) and capabilities.
* environment_id
* workspace_id
* name (dev/staging/prod, or any user-chosen label)
* network configuration reference (created VPC or existing VPC + selected subnets)
* ECS cluster (one per environment in v1 for clarity)
* baseline security groups and defaults
* logging/metrics plumbing configuration
* ingress baseline (ALB conventions if applicable)

### App
* app_id
* workspace_id
* name
* build strategy: Dockerfile-based OR Nixpacks (v1)
* runtime metadata ports, health check path, command, env var schema
* workload type: web/worker/job

### Deployment (binding app and environment)
* deployment_id
* app_id
* environment_id
* source selector: branch / tag / commit SHA
* deploy trigger: manual / on push / (future) PR-based previews
* runtime overrides: desired count/scale
* environment variables and secrets references

### Datastore
* datastore_id
* workspace_id
* type: Aurora / DynamoDB (v1)
* template with defaults

### Datastore Instance
* datastore_instance_id
* datastore_id
* environment_id
* outputs: endpoint identifiers, ARNs, connection metadata references


