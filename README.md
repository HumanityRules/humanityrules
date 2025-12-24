# Project synopsis

**Devops Hero** is a platform designed to make deploying internal tools to a company's private cloud (VPC) as easy as using Heroku, Render, or Railway, while maintaining enterprise security and compliance.

- **The Problem:** While AI has made building apps faster than ever, deploying them internally is still a bottleneck due to complex requirements like IAM permissions, SSO integration, VPC networking, and compliance checks.
- **The Solution:** An AI-powered "co-pilot" that automates the deployment process and governance, allowing developers to ship internal apps in minutes rather than weeks.
- **Key Features:**
    - **Automated Infrastructure:** Deploys directly to your company’s VPC without requiring manual Terraform or YAML wrestling.
    - **AI-Assisted Security:** Uses an AI wizard to configure IAM permissions and set up approval chains.
    - **Built-in SDK:** Provides out-of-the-box integration for SSO, Role-Based Access Control (RBAC), and standardized logging/metrics.
    - **Governance:** Includes approval flows for sensitive changes to ensure compliance.
- **Target Audience:** It aims to empower Full Stack Engineers, Data Scientists, Machine Learning Engineers, and Business staff to "vibe-code" and ship tools independently, while giving DevOps teams the control and standardization they need.


# What I did to boostrap the project
```
uv init .
uv add django==6.0
uv run django-admin startproject devopshero_site .
uv run manage.py startapp devopshero_app
uv run manage.py migrate
uv run manage.py runserver
```

# Django 6.0 Template Partials

**Prefer partials over plain `{% include %}`** — they're the modern Django 6.0 approach.

- **Same template:** `{% partialdef name %}...{% endpartialdef %}` then `{% partial name %}`
- **External template:** `{% include "path/to/template.html#partial_name" with foo=bar %}`


# Authentication

Authentication is handled by [WorkOS AuthKit](https://workos.com/docs/user-management). The flow:

1. User visits any page → redirected to `/auth/login/`
2. User clicks "Continue with WorkOS" → redirected to WorkOS hosted auth
3. WorkOS authenticates user → redirects back to `/auth/callback/`
4. Callback exchanges code for user info, creates/updates Django user, logs them in

**Configuration:** Set `WORKOS_CLIENT_ID` and `WORKOS_API_KEY` in `.env`


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


