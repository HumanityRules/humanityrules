# Domain Model

## Naming and core concepts
* **Workspace:**
  * Primary unit of ownership, configuration, and governance.
  * Each Workspace has a primary Git repo (additional repos can be added later in v2).
  * Workspaces contain Apps and Datastores.
* **Apps:**
  * Compute workloads users deploy.
* **Datastores:**
  * Stateful dependencies the platform provisions and manages (Aurora, DynamoDB in v1).
* **Environments:**
  * Deployment targets that are independent of any app or workspace.
  * Environments are **account-scoped**: they belong to an AWS Account, not a Workspace.
  * Multiple workspaces can deploy apps to the same environment (e.g., "prod").
  * A "default" environment is auto-created when an AWS account is connected.
  * Branch/tag/commit selection occurs when creating a Deployment (binding an App to an Environment).
  * v1 supports two modes for Environment networking:
    * Managed networking: the platform creates a new VPC for the environment.
    * Existing VPC attachment.


## Compute substrate
* v1 supports ECS/Fargate only.
* Therefore, users do not choose substrate at deployment time in v1.
* ECS/Fargate is treated as an implicit environment capacity.


## High-level mental model
* Organization has AWS Accounts.
* AWS Account has Environments (shared infrastructure: VPC, ECS cluster).
* Workspace contains definitions (Apps, Datastores) — the "what" to deploy.
* Environment is where things run (AWS account + region + VPC + ECS baseline) — the "where".
* Deployment binds an App to an Environment and selects the git source.
* Multiple workspaces can deploy to the same environment, sharing VPC and cluster while having isolated app resources (ECR, ALB, Aurora).
* Datastore provisioning creates datastore instances into an Environment's network context.
* Binding explicitly attaches Apps to Datastore instances with specific permissions.
  

## Object model

### Organization
* A user can belong to many organizations.
* An organization can have many AWS accounts.
  
### AWS Account
* Associated to a particular organization through a wizard flow.
* CloudFormation template that the customer executes on their account.
* Has many Environments (default, prod, staging, etc.).

### Environment
An Environment defines the runtime fabric (VPC, ECS/Fargate) and is **account-scoped**.
* environment_id
* aws_account_id (FK to AWS Account)
* name (default/staging/prod, or any user-chosen label)
* slug
* status (pending, provisioning, ready, error)
* VPC and ECS cluster (one of each per environment)
* baseline security groups and defaults
* logging/metrics plumbing configuration

A "default" environment is auto-created when an AWS account is connected. Users can create additional environments (prod, staging) as needed.

### Workspace
* workspace_id
* organization_id
* aws_account_id (target account for deployments)
* aws_region
* primary_repo
* RBAC policies (workspace admins, developers, viewers)
* Collections:
  * Apps
  * Datastores

### Network configuration
A first-class concept representing the network a given Environment uses.
* VPC
* Subnets:
  * Private subnets for Fargate tasks
  * Datastore subnet group inputs (Aurora needs subnets)

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
