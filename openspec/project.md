# Project Context

## Purpose

**DevOps Hero (DOH)** is a platform that makes deploying internal tools to a company's private cloud (VPC) as easy as using Heroku, Render, or Railway, while maintaining enterprise security and compliance.

**The Problem:** AI has made building apps faster than ever, but deploying them internally is still a bottleneck due to IAM permissions, SSO integration, VPC networking, and compliance checks.

**The Solution:** An AI-powered "co-pilot" that automates the deployment process and governance, allowing developers to ship internal apps in minutes rather than weeks.

**Target Users:**
- Full Stack Engineers
- Data Scientists
- Machine Learning Engineers
- Business staff who "vibe-code"

## Tech Stack

### Backend
- **Python 3.14** — Runtime
- **Django 6.0** — Web framework
- **django-htmx** — HTMX integration for dynamic UIs
- **WorkOS** — Authentication (AuthKit, SSO, directory sync)
- **SQLite** — Development database
- **python-dotenv** — Environment variable management

### Frontend
- **HTMX** — Dynamic interactions without JavaScript
- **Tailwind CSS** — Styling (via django-tailwind)
- **Server-side rendering** — Django templates with partials

### Infrastructure (Customer Deployments)
- **AWS CDK (aws-cdk-lib)** — Infrastructure as code
- **boto3** — AWS SDK for Python
- **ECS Fargate** — Container orchestration
- **Aurora Serverless v2** — Managed databases
- **ALB** — Load balancing with HTTPS
- **Route53** — DNS management
- **ACM** — TLS certificates
- **Secrets Manager** — Credential storage

### Infrastructure (DOH Platform)
- **CloudFormation** — DOH's own AWS resources
- **Lambda** — Cross-account callback handler
- **S3** — Public/private asset storage

### Development Tools
- **uv** — Python package manager and runner
- **Jinja2** — Template rendering for CloudFormation

## Project Conventions

### Code Style

#### Function Signatures — No Default Parameters
All parameters must be mandatory. If a value is optional, the caller must explicitly pass `None`.

```python
# Bad
def connect_to_db(url, retries=3, timeout=30):
    ...

# Good
def connect_to_db(url, retries, timeout):
    ...
```

#### Prefer Keyword Arguments
Use named arguments for function calls, especially for literals and multiple parameters.

```python
# Bad
connect_to_db("db://localhost", 3, 30)
create_user("jdoe", True, False)

# Good
connect_to_db(url="db://localhost", retries=3, timeout=30)
create_user(username="jdoe", is_admin=True, send_email=False)
```

#### Module-Qualified Imports for Local Modules
Use `import module` rather than `from module import function`. Call functions with the module prefix.

```python
# Bad
from iam_utils import get_assumed_role_session
session = get_assumed_role_session(...)

# Good
import iam_utils
session = iam_utils.get_assumed_role_session(...)
```

Standard library and well-known packages (e.g., `from pathlib import Path`) are exempt.

### Architecture Patterns

#### HTMX SPA Navigation
The app uses HTMX for SPA-like navigation without client-side JavaScript:

1. **App Shell Pattern**: `app_shell.html` is the outer frame. Content loads into `#main-content` via HTMX.
2. **Out-of-Band Swaps**: Server returns page content AND updated sidebar elements with `hx-swap-oob="true"`.
3. **Server-Owned State**: Active nav states determined by Django template conditionals, not JavaScript.
4. **Browser History**: `hx-push-url="true"` updates URLs for back/forward support.

#### View Pattern
Views handle both HTMX requests (return partial) and full page loads (return app_shell with content_url):

```python
@login_required
def mypage(request):
    context = get_app_shell_context(request=request, current_page="mypage")

    if request.htmx:
        return render(request, "devopshero_app/mypage.html", context=context)

    context["content_url"] = "/mypage/"
    return render(request, "devopshero_app/app_shell.html", context=context)
```

#### Django 6.0 Template Partials
Prefer partials over plain `{% include %}`:

- Same template: `{% partialdef name %}...{% endpartialdef %}` then `{% partial name %}`
- External template: `{% include "path/to/template.html#partial_name" with foo=bar %}`

#### Multi-Tenancy
- Organizations are the top-level tenant
- Users belong to organizations via OrganizationMembership
- User.current_organization tracks active context
- AWS accounts belong to organizations

### Testing Strategy

Testing infrastructure is minimal (noted as technical debt). Priority areas:
- Onboarding flow unit tests
- Auth callback branching
- Transaction atomicity

### Git Workflow

- Main branch: `main`
- Commits should be atomic and focused
- No specific branching strategy documented

## Domain Context

### Core Concepts

| Concept | Description |
|---------|-------------|
| **Organization** | Top-level tenant. Users belong to orgs, orgs own workspaces. |
| **Workspace** | Primary unit of ownership, configuration, governance. Contains apps and datastores. |
| **App** | Compute workload users deploy (web, worker, scheduled job). |
| **Datastore** | Stateful dependency (Aurora MySQL/PostgreSQL). |
| **Environment** | Deployment target (AWS account + region + VPC + ECS cluster). |
| **Deployment** | Binding of an App to an Environment with git source selection. |

### AWS Account Connection Flow
1. User enters account name, clicks "Open AWS Authorization Page"
2. AWSAccount record created with status=PENDING and unique external_id
3. User creates CloudFormation stack in their AWS account
4. Lambda callback notifies DOH backend → status=CONNECTED
5. DOH can now assume role in customer account for deployments

### Deployment Architecture
```
Customer AWS Account
├── VPC (private subnets + NAT Gateway)
├── ECS Cluster (Fargate)
├── Aurora Serverless v2 (if database needed)
├── ALB with HTTPS (ACM cert, Route53)
└── ECR Repository (container images)
```

All infrastructure deployed via AWS CDK from DOH control plane.

## Important Constraints

### Security
- **External ID required**: All cross-account AssumeRole calls must include external_id (confused deputy prevention)
- **Per-app secret isolation**: Each app's task role can only read its own secrets
- **Secrets never in CloudFormation**: Database URLs and credentials injected via Secrets Manager
- **Private subnets**: Fargate tasks run in private subnets behind NAT Gateway

### Technical
- **ARM64 builds**: Docker images built for ARM64 (Graviton) to avoid QEMU emulation bugs with Elixir 1.18 on Apple Silicon
- **Aurora naming**: Cluster identifiers and secrets namespaced by app (`devopshero-{app_name}-aurora`)
- **CDK bootstrap required**: Customer accounts need CDK bootstrap for deployments

### Cost
- NAT Gateway: ~$32/month per VPC (accepted for security posture)
- Aurora Serverless v2: 0.5-2 ACU default (pay for usage)
- Graviton instances: 20% cheaper than x86

## External Dependencies

### APIs
- **WorkOS** — Authentication, SSO, directory sync
- **AWS** — All infrastructure (ECS, RDS, ALB, Route53, ACM, Secrets Manager, CloudFormation, S3, Lambda)
- **Claude API** — AI agent (planned, via Claude Agents SDK)

### DNS
- Test domain: `chsandbox.com` (Route53 hosted zone)

### DOH AWS Resources
- Account: `555553041615`
- Public S3 bucket: `devopshero-public` (CloudFormation templates)
- Private S3 bucket: `devopshero-private` (Lambda code)
- Lambda: `devopshero-install-callback`

## Key Files Reference

| Path | Purpose |
|------|---------|
| `AGENTS.md` | Project synopsis, documentation index, conventions |
| `docs/journal.md` | Development journal (reverse chronological) |
| `docs/TODO.md` | Active task list |
| `docs/agent_spec/agent_spec.md` | AI agent specification |
| `docs/database_config_spec/` | Aurora configuration spec |
| `docs/aws_account.md` | AWS account connection flow |
| `devopshero_app/` | Django application |
| `infra_customer/` | AWS CDK deployment code |
| `infra_devopshero/` | DOH platform infrastructure |
| `deployable_repos/` | Example apps (simple_dashboard, db_portal) |
