# DevOps Hero — Screen-by-Screen Website Report

This report documents every screen in the DevOps Hero web application as of April 2026.
It is intended to help an LLM understand what can be shown in a demo video and build a narrative script/storyboard.

---

## Global UI Elements

**Design language:** Dark theme (navy/gray-900 backgrounds), indigo/purple accent colors, Tailwind CSS. Modern, enterprise-grade SaaS aesthetic.

**App shell (all authenticated pages):**
- **Left sidebar (fixed, 288px):** DevOps Hero logo at top, organization selector dropdown ("Humanity Rules" in demo data), then vertical nav links: Dashboard, Workspaces, Environments, Security, Settings. Each has an icon. Active page is highlighted.
- **Top header bar (sticky):** Search input (left), notifications bell icon (center-right), profile avatar + name dropdown ("Victor") far right. The profile menu has logout and org-switching options.
- **Content area:** Right of sidebar, below header. Content loads asynchronously via HTMX for snappy SPA-like navigation without full page reloads.

---

## Screen 1: Landing Page

**URL:** `/`
**Purpose:** Public marketing page. Communicates the product value proposition to potential customers and captures waitlist signups.
**No authentication required.**

### Hero Section (above the fold)
- **Badge:** "Everyone's vibe-coding. Now everyone can vibe-deploy." with sparkle icon.
- **Headline:** "Deploy internal tools inside your AWS account **in minutes**" — large gradient text.
- **Sub-headline:** "SSO + RBAC, least-privilege IAM, and approval workflows — built in"
- **Description paragraph:** "DevOps Hero is an AI deployment co-pilot that provisions infrastructure and generates least-privilege IAM requests in your AWS account. Build or vibe-code dashboards, APIs, Slack bots, admin portals, and more — then ship them without opening infra tickets."
- **Email signup form:** Single email input + "Notify me" button. Submits to waitlist. Subtext: "We'll email you when DevOps Hero is ready. No spam."
- **Three feature badges (grid):**
  - "Private networking in your VPC" (clock icon)
  - "Approval workflows & audit trails" (shield icon)
  - "AI-Driven self-serve deployments" (sparkles icon)
- **Visual effects:** Animated background blobs (purple/cyan glow), grid pattern, radial gradient.
- **Nav bar at top:** DevOps Hero logo, links to Problem, Solution, Features, Who It's For, "Go to Dashboard" link.

### Problem Section
- **Headline:** "AI sped up building internal apps"
- **Sub-headline:** "Getting them deployed inside your company is still the bottleneck"
- **Description:** "You've written the perfect internal tool, but now you're stuck wiring IAM policies, approval flows, SSO, logging and metrics..."
- **Four pain-point cards:**
  - **IAM Policy Maze** — "Complex permission configurations that require deep AWS expertise"
  - **Terraform Wrangling** — "Endless YAML and HCL files that need constant maintenance"
  - **SSO Integration** — "Days spent configuring authentication and authorization flows"
  - **Infra Ticket Queues** — "Waiting days for platform teams to provision resources"
- **Stat callout:** "Average deployment time: 2 days to 2 weeks for a simple internal tool"

### Solution Section
- **Headline:** "The Heroku experience inside your enterprise"
- **Description:** "Our AI-powered co-pilot automates infrastructure provisioning directly into your company's AWS account..."
- **Three solution cards:**
  - **AI Co-Pilot** — "Intelligent automation that understands your infrastructure needs"
  - **Enterprise Security** — "Least-privilege IAM policies configured through a simple wizard"
  - **Built-in SDK** — "SSO, RBAC, logging, and metrics included by default"

### Features Section
- **Headline:** "Everything you need to ship with confidence"
- **Six feature cards (2x3 grid):**
  - **Heroku-like Deployment Experience** — Deploy via web UI or CLI; rollbacks and destruction are trivial. No infrastructure expertise required.
  - **AI-Assisted IAM & Approval Chains** — Know what permissions are missing, request them automatically, track approvals through an intelligent wizard.
  - **Governance & Audit Trails** — Require approvals for sensitive actions, complete audit log for compliance and security reviews.
  - **Built-in Observability** — Logs and metrics flow into CloudWatch, Datadog or your preferred stack automatically. Zero config.
  - **SDK with SSO & RBAC** — Inject standardized authentication and authorization. AI helps scaffold integration code.
  - **Instant Rollbacks** — One-click rollbacks to any previous version.

### Who It's For Section
- **Headline:** "Built for every builder"
- **Sub:** "From infrastructure engineers to business analysts"
- **Four persona cards:**
  - **Adam** (DevOps/Platform Engineer) — Challenge: Tired of cobbling together scripts and permissions. With DOH: opinionated golden path reduces toil.
  - **Rachel** (Software Developer) — Challenge: Wants to deliver value, not wrestle pipelines. With DOH: Ship features directly, zero infra overhead.
  - **Troy** (Data Scientist) — Challenge: Has ideas beyond notebooks. With DOH: Deploy dashboards, ML tooling, data products without learning Kubernetes.
  - **Crystal** (Citizen Developer / Vibe-coder) — Challenge: Loves vibe-coding internal apps. With DOH: Build operational tools securely and compliantly.

### CTA Section (bottom)
- **Headline:** "Ready to ship internal tools faster than ever?"
- **Description:** "Join the early adopters who are transforming how their companies build and deploy internal software."
- **Second email signup form** (same as hero).

### Footer
- **Four columns:** Product (Features, Pricing, Changelog), Company (About, Blog, Careers, Contact), Resources (Documentation, API Reference, Status, Support), Legal (Privacy, Terms, Security).
- **Bottom line:** "2026 DevOps Hero. All rights reserved. Made with precision for enterprise teams."
- **Social links:** Twitter, GitHub, LinkedIn.

---

## Screen 2: Onboarding

**URL:** `/onboarding/`
**Purpose:** First-time user flow after authentication. Creates the user's organization. Shown once, after first login.

### UI Description
- Standalone page (no sidebar or app shell — uses its own minimal layout).
- Dark background matching the app theme.
- DevOps Hero logo centered at top.
- **Headline:** "Welcome to DevOps Hero!"
- **Subtext:** "Signed in as [user email]"
- **Form:** Single input field for "Organization name" with placeholder "Acme Corp".
- **Helper text:** "This is usually your company or team name."
- **Submit button:** "Create Organization" (indigo, full width).

### Functionality
- After submitting, the organization is created and the user is redirected to the dashboard.
- This is the gateway step — no other features are accessible until the org is created.

---

## Screen 3: Dashboard

**URL:** `/dashboard/`
**Purpose:** Overview of all apps and datastores across the organization. The "home" screen after login.

### UI Description
- **Page title:** "Dashboard"
- **Apps section:** Grid of app cards (up to 3 per row). Each card shows:
  - **App name** (bold header, e.g., "db-portal", "simple-dashboard", "AI Detector and Humanizer")
  - **Type:** Web Service
  - **Repository:** GitHub owner/repo name
  - **Branch:** main/master
  - **Workspace:** Link to workspace (e.g., "Default" in purple)
  - **Last deployed:** Relative time (e.g., "1 day, 22 hours ago") or "Never"
  - **URL:** Link to deployed app URL or "—" if not deployed
  - **Status:** Color-coded pill — green "Succeeded", red "Failed", or "—"
- **Datastores section:** Grid of datastore cards. Each shows:
  - **Name** (e.g., "db-portal-mysql")
  - **Engine** (e.g., "Aurora MySQL")
  - **Status pill** (e.g., yellow "Pending")
  - **Workspace link**

### Functionality
- Cards are clickable — navigate to app detail or workspace detail.
- Status pills auto-refresh (HTMX polling) for apps with in-progress deployments.
- Provides at-a-glance operational awareness across the entire organization.

---

## Screen 4: Workspaces List

**URL:** `/workspaces/`
**Purpose:** Browse and manage logical groupings of apps and datastores. Workspaces are the organizational units for projects or teams.

### UI Description
- **Page title:** "Workspaces"
- **Subtitle:** "Organize your apps and datastores into workspaces."
- **Grid of workspace cards** (2 per row). Each card shows:
  - **Workspace name** (e.g., "Default")
  - **Description** (if set, e.g., "Your starting workspace for apps and datastores")
  - **Nested app list** inside the card: each app name with its latest deployment status pill
- **"+ New Workspace" card:** Dashed-border card with plus icon. Opens a modal to create a new workspace.

### Functionality
- Cards are clickable — navigate to workspace detail.
- Status pills show latest deployment state for each app within the workspace.
- The workspace create modal asks for name and optional description.

---

## Screen 5: Workspace Detail

**URL:** `/workspaces/<slug>/`
**Purpose:** View all details, apps, datastores, and deployments for a single workspace.

### UI Description
- **Breadcrumb:** Workspaces > [workspace name]
- **Two-column header row:**
  - **Left card — Workspace info:** Description, Slug (monospace), Created date, Apps count.
  - **Right card — Workspace Tags:** "Workspace Tags" heading with "Edit" button. Shows inherited tags (from organization) and direct tags (assigned to this workspace). Tags are key=value pills (e.g., `project=One`, `team=Mine`).
- **Apps section:** Grid of app cards (2 per row), same format as Dashboard cards. Plus a dashed-border "+ New App" button that opens a repository picker modal.
- **Datastores section:** Grid of datastore cards with name, engine, status pill. Shows "No datastores configured" if empty.
- **Recent Deployments section:** Table with columns: App name (with environment and ref info), Status pill, Time ago. Each row links to the app. Shows "No deployments in this workspace yet" if empty.

### Functionality
- The "New App" button opens a **repository picker modal** that lists GitHub repositories connected to the organization. User selects a repo and is redirected to the Deployment Editor to configure and deploy it.
- Tags are editable inline (key-value pairs). Tags are used by the ABAC security system.
- Provides workspace-level operational overview.

---

## Screen 6: App Detail

**URL:** `/apps/<slug>/`
**Purpose:** Full detail view for a single application — its configuration, deployed environments, and deployment history.

### UI Description
- **Breadcrumb:** Workspaces > [workspace] > [app name]
- **Action button (top right):** "Deployment" — links to the Deployment Editor for this app.
- **Two-column header row:**
  - **Left card — App info:** App Type (Web Service), Created date, Created By (email).
  - **Right card — App Tags:** Shows "Inherited" tags (from workspace/org) and "Direct" tags on this app. Editable with "Edit" button.
- **Source card:** Repository name, Branch, Subpath.
- **Build card:** Strategy (Dockerfile), Dockerfile path.
- **Container card:** Port (e.g., 8501), Health Check Path (e.g., `/_stcore/health`), Health Check Command.
- **"Deployed to Environments" section:** Table showing each environment where this app is live:
  - Environment name (e.g., "Production") with link
  - AWS Account info (e.g., "Humanity Rules Sandbox"), Region (us-east-1), Git ref
  - Live URL (clickable, e.g., https://simple-dashboard.chsandbox.com)
  - Status pill (Succeeded/Failed)
  - Expandable "Config" section showing env var count and secrets count
  - **Action buttons:** Redeploy, Permissions, Tear Down
- **"Recent Deployments" section:** Historical deployment log table. Each row:
  - Environment name, AWS Account, Region, Git ref, URL
  - Status pill
  - Relative timestamp

### Functionality
- "Deployment" button navigates to the AI-assisted Deployment Editor.
- "Redeploy" triggers a new deployment of the same app to the same environment.
- "Permissions" navigates to the IAM Permissions Editor for that app/environment combination.
- "Tear Down" shows a confirmation modal, then destroys the deployed infrastructure (ECS service, ALB rules, CloudFormation stack).
- Config section is expandable to see env vars and secrets (values hidden).
- Auto-refreshes via HTMX when deployments are in progress.

---

## Screen 7: Deployment Editor (AI-Assisted)

**URL:** `/deploy/<app-slug>/` or `/deploy/new/<workspace-slug>/<repo-id>/`
**Purpose:** The core AI-driven deployment experience. A split-panel interface where the AI agent configures the app and its deployment blueprint through a conversational chat.

### UI Description
- **Breadcrumb:** Workspaces > [workspace] > [app name or "New App"]
- **Description:** "Define the app and its deployment blueprint. The blueprint is the environment-specific deployable state for this app."
- **"Reset Conversation" button** (top right).
- **Two-panel layout (40/60 split):**

  **Left panel — Configuration state:**
  - **APPLICATION section** with status badge (Defined/Pending):
    - Name, Type (Web Service), Build Strategy (Dockerfile), Dockerfile path, Port, Health check path, Repository
  - **DEPLOYMENT BLUEPRINT section** with status badge (Defined/Pending):
    - "The agent will configure the blueprint after defining the app and selecting an environment."
    - When configured: shows environment, env vars, secrets, resource settings

  **Right panel — AI Chat conversation:**
  - **Conversation header:** Title (e.g., "New Conversation"), context metadata (Workspace, Repository), Fork button, status badge (Active).
  - **Message stream:** Alternating messages between the AI agent and the user:
    - **Agent tool calls** displayed as collapsible rows: e.g., "Bash: List root directory contents" (85ms), "Read: src/Dockerfile" (12ms), "Read: src/requirements.txt" (16ms), "Analyze Repository" (4905ms). Each shows a green checkmark icon and execution time.
    - **Agent text messages** rendered as markdown with formatting (bold, lists, code blocks, links).
    - **Interactive choice buttons:** Agent asks questions and presents clickable option buttons (e.g., "Re-deploy to Production" / "Deploy to dev").
    - **Streaming indicator:** Shows "Thinking..." during agent processing.
  - **Message input:** Text area at bottom with "Send" button. Supports Enter to send, Shift+Enter for newlines, Up arrow for message history.

### Functionality
- The AI agent automatically analyzes the repository (reads Dockerfile, requirements.txt, app code, etc.), determines the app type, port, health check, and build strategy.
- The agent proposes configuration and asks the user to confirm or adjust.
- Agent presents interactive questions as clickable buttons (not just text) — e.g., choosing which environment to deploy to.
- The left panel updates in real-time as the agent configures values (via SSE + HTMX).
- Tool executions are logged inline in the chat (Bash commands, file reads, analysis steps) with timing.
- "Reset Conversation" starts a new conversation and clears the draft blueprint.
- "Fork" creates a copy of the conversation for debugging/experimentation.
- The agent can trigger actual deployments (CloudFormation stack creation, Docker build, ECS service setup).

---

## Screen 8: Environments List

**URL:** `/environments/`
**Purpose:** View and manage all deployment target environments (each an AWS VPC + ECS cluster combination).

### UI Description
- **Page title:** "Environments"
- **Subtitle:** "Deployment targets within your connected AWS accounts. Each environment has its own VPC and ECS cluster."
- **Grid of environment cards** (3 per row). Each card shows:
  - **Environment name** (e.g., "Production", "dev")
  - **Status pill:** green "Ready"
  - **Account:** AWS account friendly name (e.g., "Humanity Rules Sandbox")
  - **Account ID:** AWS account number (e.g., 266117665083)
  - **Region:** AWS region (e.g., us-east-1)
- **"+ New Environment" card:** Dashed-border card with plus icon. Opens the "Select AWS Account" modal, then navigates to the Environment Editor.

### Functionality
- Cards are clickable — navigate to environment detail.
- "New Environment" opens a modal showing connected AWS accounts. User picks an account, then enters the AI-assisted Environment Editor.
- Environments are the deployment targets — apps are deployed *into* environments.

---

## Screen 9: Environment Detail

**URL:** `/environments/<id>/`
**Purpose:** View detailed information about a single environment and its deployments.

### UI Description
- **Breadcrumb:** Environments > [environment name]
- **"Resume Setup" button** (shown only for draft/pending/provisioning/error states).
- **Two-column header row:**
  - **Left card — Environment info:** AWS Account name, Account ID, Region, VPC ID, Domain (e.g., chsandbox.com or "HTTP only"), Status pill.
  - **Right card — Environment Tags:** Key-value tag pills, editable. Tags used by ABAC security policies.
- **Recent Deployments section:** Table of apps deployed to this environment. Each row shows app name, environment, ref, URL, status, timestamp.

### Functionality
- "Resume Setup" re-enters the AI Environment Editor to continue provisioning.
- Tags control which users/groups have access (via ABAC policies).

---

## Screen 10: Environment Editor (AI-Assisted)

**URL:** `/environments/<id>/setup/` or `/environments/new/`
**Purpose:** AI-guided setup of a new environment (VPC, ECS cluster, domain, ALB). Split-panel layout similar to the Deployment Editor.

### UI Description
- **Breadcrumb:** Environments > [environment name or "New Environment"]
- **Description:** "Define the environment draft, review it with the agent, and provision it when you're ready."
- **"Reset Conversation" button** (top right).
- **Two-panel layout (40/60 split):**

  **Left panel — Environment configuration:**
  - AWS Account section (selected account)
  - Environment setup section (name, region, domain, VPC settings — populated by the agent)

  **Right panel — AI Chat conversation:**
  - Same chat interface as the Deployment Editor.
  - Agent asks about Route53 domains, region preferences, naming.
  - Presents options as clickable buttons (e.g., "chsandbox.com" / "None (HTTP-only via Load Balancer)").
  - Executes tool calls (e.g., "Save Environment: default" — 26ms).
  - Summarizes the draft and asks: "Provision now" or "Keep editing."
  - Can trigger actual VPC/ECS cluster provisioning via CloudFormation.

### Functionality
- Agent discovers available Route53 domains in the AWS account and presents them as options.
- Agent creates the environment draft, user confirms, agent provisions the infrastructure.
- Real-time left panel updates as agent configures values.

---

## Screen 11: Security Hub

**URL:** `/security/hub/`
**Purpose:** Central security dashboard. Shows runtime permission issues and pending permission requests.

### UI Description
- **Page title:** "Security"
- **Tab bar:** Hub | People | Groups | Policies (Hub is active)
- **"Recent issues" section:**
  - Sub-header: "RUNTIME PERMISSION ERRORS"
  - Shows count of recent issues. When no issues: "No issues."
- **"Permission requests" section:**
  - Lists pending IAM permission change requests. Each row shows:
    - App name / Environment (e.g., "simple-dashboard / Production")
    - Statement count, relative time, requester email
    - Status badge (e.g., "Draft")
  - Rows are clickable — navigate to the Permissions Editor.

### Functionality
- Runtime permission errors surface when a deployed app tries to access an AWS resource it doesn't have permission for.
- Permission requests track the lifecycle of IAM policy changes (Draft -> Submitted -> Approved/Rejected).

---

## Screen 12: Security — People

**URL:** `/security/people/`
**Purpose:** Manage identity attributes for organization members. Part of the Attribute-Based Access Control (ABAC) system.

### UI Description
- **Tab bar:** Hub | People | Groups | Policies (People is active)
- **Section title:** "People"
- **Subtitle:** "Manage identity attributes for organization members."
- **Default org-role selector:** Dropdown ("member") + "Save" button. Sets the default role automatically assigned when a new identity joins the organization.
- **User list:** Each row shows:
  - **Email** (e.g., amy.king@humanityrules.io)
  - **Full name** (e.g., Amy King)
  - **Attribute tags:** Color-coded pills showing all attributes (both direct and inherited from groups). Examples: `region=us-west`, `tier=premium`, `clearance=internal`, `department=data-science`, `cost_center=CC-101`, `clearance=admin`.
- Users are clickable — navigate to individual user detail for attribute management.

### Functionality
- Attributes are key-value pairs that define what a user can access.
- Attributes flow from groups (inherited) and can also be assigned directly to users.
- The ABAC system evaluates user attributes against resource tags and policy rules to determine access.

---

## Screen 13: Security — Groups

**URL:** `/security/groups/`
**Purpose:** Manage groups as attribute containers. Group members inherit all group attributes.

### UI Description
- **Tab bar:** Hub | People | Groups | Policies (Groups is active)
- **Section title:** "Groups"
- **Subtitle:** "Groups are attribute containers. Members inherit all group attributes."
- **Create form (inline):** Text inputs for "Group name" and "Description (optional)" + "Create Group" button (indigo).
- **Grid of group cards** (3 per row). Each card shows:
  - **Group name** (e.g., "Admins", "Auditors", "Contractors", "Data Science", "DevOps", "Engineering", "Finance", "Product", "QA", "Security")
  - **Description** (e.g., "System administrators", "Read-only audit access", "External contractors")
  - **Member count** (e.g., "26 members", "19 members")
  - **Attribute pills:** Color-coded key=value tags (e.g., `clearance=admin`, `region=any`, `cost_center=CC-201`)

### Functionality
- Groups are clickable — navigate to group detail page where you can:
  - Add/remove members
  - Add/remove attributes
  - Delete the group
- When a user is added to a group, they inherit all group attributes.
- This creates scalable access management: instead of managing 100 individual users, manage 10 groups.

---

## Screen 14: Security — Policies

**URL:** `/security/policies/`
**Purpose:** Define ABAC policies that map identity attributes + resource tags to allowed actions.

### UI Description
- **Tab bar:** Hub | People | Groups | Policies (Policies is active)
- **Section title:** "Policies"
- **Subtitle:** "ABAC policies map identity attributes + resource tags to allowed actions."
- **"New Policy" button** (indigo, top right).
- **Policy list:** Each row shows:
  - **Policy name** (e.g., "Default: simple-dashboard open access", "Org admins: full environment access")
  - **Badge:** "System" (yellow) for auto-generated policies
  - **Rule preview in plain English:** e.g., `IF identity * AND app app-name=simple-dashboard THEN app:use`
  - **Resource type badge** (right): "App", "Environment", or "Workspace"
- **Types of policies shown:**
  - **Default app access policies** (one per app) — e.g., `IF identity * AND app app-name=X THEN app:use`
  - **Org role-based policies:**
    - `Org admins: app usage` — IF identity org-role=admin AND app * THEN app:use
    - `Org admins: full environment access` — IF identity org-role=admin AND environment * THEN environment:admin
    - `Org admins: full workspace access` — IF identity org-role=admin AND workspace * THEN workspace:admin
    - `Org members: app usage` — IF identity org-role=member AND app * THEN app:use
    - `Org members: environment access` — view + deploy
    - `Org members: workspace access` — view + edit
    - `Org viewers: app usage`, `Org viewers: environment access`

### Functionality
- Policies are the rules engine of the ABAC system.
- Admins can create custom policies to restrict access based on attribute matching.
- Policies are clickable — navigate to policy detail for editing conditions and actions.
- System-generated policies provide sensible defaults.

---

## Screen 15: Permissions Editor (AI-Assisted)

**URL:** `/security/permissions/editor/?app=<slug>&environment=<id>`
**Purpose:** Edit IAM task-role policies (AWS permissions) for a specific app+environment deployment. Split-panel with AI chat assistance.

### UI Description
- **Breadcrumb:** Security > Permissions — [app name] / [environment name]
- **Status badge** for the permission request (Draft, Submitted, Approved, Rejected).
- **Description:** "Edit IAM task-role policies for this deployment."
- **Two-panel layout (50/50 split):**

  **Left panel — Policy Editor:**
  - **"Policy statements" heading** with action buttons:
    - "Refresh resources" — re-fetches available AWS resources
    - "+ Add service" — opens a searchable dropdown of AWS services (S3, DynamoDB, SQS, SES, etc.) with "Common" section and "All services" section
  - **Statements list:** Each statement is a collapsible service group (e.g., S3, DynamoDB) with:
    - Permission level selector
    - Resource selector (specific ARNs or wildcards)
    - Remove button
  - **Description text area:** "Describe why these permission changes are needed..."
  - **Action buttons:** "Cancel" and "Submit Request"

  **Right panel — AI Chat conversation:**
  - Same chat interface as other editors.
  - Agent can suggest permission statements, explain what permissions are needed, and modify the policy.
  - Real-time sync: when the agent changes permissions, the left panel updates automatically via SSE.

### Functionality
- The AI agent can analyze the app code and determine what AWS permissions it needs (e.g., S3 read for data files, DynamoDB access for state).
- Users can manually add/remove service permissions or let the agent handle it.
- "Submit Request" sends the permission changes for approval (governance workflow).
- Dual editing: both human and AI can modify the same permission set simultaneously.

---

## Screen 16: Settings — Personal Settings

**URL:** `/settings/` or `/settings/personal/`
**Purpose:** View user profile information.

### UI Description
- **Page title:** "Settings"
- **Tab bar:** Personal Settings | Organization Settings | AWS Accounts | Git Integrations | Billing
- **Profile section:**
  - "Your name and email address as registered with DevOps Hero."
  - Table: Full name, Email, Username.
- **Current Organization section:**
  - "The organization you are currently working in."
  - Table: Organization name (e.g., "Humanity Rules").

---

## Screen 17: Settings — Organization Settings

**URL:** `/settings/organization/`
**Purpose:** View organization information.

### UI Description
- **Tab: Organization Settings** active.
- **Organization section:**
  - "General information about your organization."
  - Table: Name ("Humanity Rules"), Slug ("humr").

---

## Screen 18: Settings — AWS Accounts

**URL:** `/settings/aws-accounts/`
**Purpose:** Manage connected AWS accounts where DevOps Hero will deploy applications.

### UI Description
- **Tab: AWS Accounts** active.
- **Section title:** "AWS Accounts"
- **Subtitle:** "Manage the AWS accounts where DevOpsHero will deploy your applications."
- **"Add AWS Account" button** (indigo, top right).
- **Table:** Name, AWS Account ID, Status, Created, Edit link.
  - Example row: "Humanity Rules Sandbox" | 266117665083 | green "Connected" | Jan 23, 2026 | Edit

### Functionality
- "Add AWS Account" starts a CloudFormation-based installation flow: user deploys a CloudFormation stack in their AWS account that creates the IAM trust role.
- Connected accounts show their status (Connected = ready to deploy).
- This is a prerequisite step: at least one AWS account must be connected before environments can be created.

---

## Screen 19: Settings — Git Integrations

**URL:** `/settings/git-integrations/`
**Purpose:** Connect GitHub organization to import repositories for deployment.

### UI Description
- **Tab: Git Integrations** active.
- **Section title:** "Git Integrations"
- **Subtitle:** "Connect your GitHub organization to import repositories for deployment."
- **GitHub card:** Shows GitHub logo, "Connected" status with green dot, repository count (e.g., "4 repositories"). Buttons: "Re-sync" and "Reconnect".
- **Synced Repositories table:** Columns: Repository (owner/name), Default Branch (pill, e.g., `main` or `master`), Synced date.
  - Example rows: vmendi/admin-dashboard (main), vmendi/ai-detector-and-humanizer (master), vmendi/fastapi-app (main), vmendi/simple-dashboard (main).

### Functionality
- "Re-sync" fetches the latest repository list from GitHub.
- "Reconnect" re-authorizes the GitHub App installation.
- Connected repositories appear in the repository picker when creating new apps.

---

## Screen 20: Settings — Billing

**URL:** `/settings/billing/`
**Purpose:** Billing and subscription management (placeholder/future).

---

## Screen 21: Chat List & Chat View

**URL:** `/chat/` (list), `/chat/<id>/` (view)
**Purpose:** General-purpose AI agent conversations. Not tied to a specific editor — can be used for ad-hoc questions, deployments, environment setup, permissions, etc.

### UI Description — Chat List
- **Two-panel layout:**
  - **Left panel (main):** Currently selected conversation or empty state with "Select a conversation" message and chat bubble icon.
  - **Right panel (sidebar, 384px):** Scrollable conversation list.
    - **"+ New Conversation" button** (indigo, full width, top).
    - **Conversation items:** Each shows:
      - **Title** (auto-generated or default "New Conversation", e.g., "Simple Dashboard Deployment Setup", "DB Portal Deployment Blueprint Recreation", "AWS Humanity Rules Sandbox Environment Setup")
      - **Relative time** (e.g., "2 weeks, 5 days ago")
      - **Agent Cost** (e.g., "$2.0809", "$0.0174")
      - **Context metadata:** Workspace name, Repository name, AWS Account name, Environment name (varies by conversation type)
      - **Status badge:** green "Active", yellow "Abandoned"

### UI Description — Chat View (conversation selected)
- **Header:** Conversation title, context metadata (Workspace, Repository or AWS Account, Environment), Fork button, status badge.
- **Message area (scrollable):**
  - **User messages:** Right-aligned or inline with user avatar.
  - **Agent messages:** Rendered markdown with:
    - Bold text, bullet lists, code blocks, links
    - Collapsible tool calls with timing (e.g., "Bash: List root directory contents — 85ms", "Read: src/Dockerfile — 12ms")
    - Interactive question buttons (e.g., "chsandbox.com" / "None (HTTP-only via Load Balancer)")
    - Tool results with status icons (green check for success)
  - **"Thinking..." indicator** while agent is processing.
  - **Scroll-to-bottom button** appears when user scrolls up during streaming.
- **Message input area:** Textarea with "Send" button. Enter submits, Shift+Enter for newlines.

### Functionality
- Real-time streaming via Server-Sent Events (SSE). Messages appear character-by-character with markdown rendering.
- Conversations track cost (cumulative LLM token cost displayed in sidebar).
- Fork creates a duplicate conversation for debugging/experimenting with different paths.
- Agent can perform tool calls: run bash commands, read files, analyze repositories, create/modify AWS resources, save configurations.
- Interactive choice questions let the user pick options by clicking buttons instead of typing.
- Conversations are contextual: they know which workspace, repository, AWS account, or environment they're operating on.

---

## Navigation Flow Summary

The typical user journey through the application:

1. **Landing Page** — Learn about the product, sign up for waitlist.
2. **Authentication** — Login via WorkOS AuthKit (SSO).
3. **Onboarding** — Create organization (first-time only).
4. **Settings > AWS Accounts** — Connect an AWS account (deploy CloudFormation stack).
5. **Settings > Git Integrations** — Connect GitHub (install GitHub App).
6. **Environments** — Create a deployment environment via the AI Environment Editor (provisions VPC, ECS cluster, ALB).
7. **Workspaces** — View the default workspace or create new ones.
8. **Workspace Detail > New App** — Pick a repo from the repository picker.
9. **Deployment Editor** — AI analyzes the repo, configures the app, selects an environment, deploys.
10. **App Detail** — Monitor the deployment, view live URL, manage configurations.
11. **Security Hub** — Review runtime permission errors, manage IAM permission requests.
12. **Security > Permissions Editor** — AI helps configure least-privilege IAM policies for the app.
13. **Security > People/Groups/Policies** — Manage ABAC access control.
14. **Chat** — General-purpose AI conversations for any deployment or infrastructure task.

---

## Key Demo-Worthy Moments

- **The AI reading code and configuring the app automatically** in the Deployment Editor — it reads Dockerfiles, discovers ports, health checks, and presents the configuration for user approval.
- **Interactive chat buttons** — the agent asking "Re-deploy to Production or Deploy to dev?" with clickable options.
- **Tool call visibility** — every action the AI takes is logged inline (file reads, bash commands, resource creation) with execution times, building trust and transparency.
- **Real-time state sync** — the left panel configuration updating live as the AI works in the right panel.
- **The IAM Permissions Editor** — AI suggesting what AWS permissions an app needs based on code analysis, with a visual policy editor.
- **ABAC security system** — the People/Groups/Policies screens showing enterprise-grade access control with attribute-based policies.
- **Environment provisioning** — AI setting up VPC, ECS cluster, ALB, Route53 domains through a natural conversation.
- **The landing page** — polished marketing site with clear problem/solution framing, persona cards, and feature grid.
- **End-to-end flow** — from connecting AWS + GitHub, through environment setup, to deploying a live app with a URL, all AI-guided.
