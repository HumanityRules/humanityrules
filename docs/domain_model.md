# Domain Model

## Naming and core concepts
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
  * v1 Environments are workspace-scoped (default and only scope in v1). There are no "shared environments across workspaces" (membership model) yet.
  * v1 supports two modes for Environment networking:
    * Managed networking: the platform creates a new VPC for the environment.
    * Existing VPC attachment.


## Compute substrate
* v1 supports ECS/Fargate only.
* Therefore, users do not choose substrate at deployment time in v1.
* ECS/Fargate is treated as an implicit environment capacity.


## High-level mental model
* Workspace contains definitions (Apps, Datastores) and owns Environments.
* Environment is where things run (AWS account + region + VPC + ECS baseline).
* Deployment binds an App to an Environment and selects the git source and triggers.
* Datastore provisioning creates datastore instances into an Environment (or into that environment's network context).
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
