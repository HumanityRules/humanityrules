# Database Configuration

## Purpose

Aurora database configuration for customer app deployments. This capability covers the `DatabaseConfig` data structure and its mapping to AWS CDK Aurora resources.

**Non-Goals:**
- Non-Aurora engines (standalone RDS MySQL/Postgres/MariaDB)
- Read replicas, global clusters, or cross-region replication
- Pricing calculations
- UI, Django models, or user flows

## Requirements

### Requirement: Engine Family Selection

The system SHALL support Aurora MySQL and Aurora PostgreSQL engine families via `DatabaseConfig.engine.family`.

#### Scenario: Aurora MySQL selected
- **WHEN** `engine.family` is `aurora-mysql`
- **THEN** the CDK stack creates an Aurora MySQL cluster
- **AND** the database port is 3306
- **AND** the connection URL scheme is `mysql://`

#### Scenario: Aurora PostgreSQL selected
- **WHEN** `engine.family` is `aurora-postgresql`
- **THEN** the CDK stack creates an Aurora PostgreSQL cluster
- **AND** the database port is 5432
- **AND** the connection URL scheme is `postgresql://`

#### Scenario: Invalid engine family
- **WHEN** `engine.family` is not `aurora-mysql` or `aurora-postgresql`
- **THEN** the deployment fails with a validation error

---

### Requirement: Engine Version Selection

The system SHALL support explicit engine version selection via `DatabaseConfig.engine.version`, with defaults from the Engine Version Catalog.

#### Scenario: Explicit version provided
- **WHEN** `engine.version` is a valid version string (e.g., `3.04.0` for MySQL)
- **THEN** the CDK stack uses that specific engine version

#### Scenario: Version is null
- **WHEN** `engine.version` is null
- **THEN** the CDK stack uses the default version from the Engine Version Catalog
- **AND** the default for Aurora MySQL is `3.04.0`
- **AND** the default for Aurora PostgreSQL is `15.4`

#### Scenario: Unsupported version
- **WHEN** `engine.version` is not in the Engine Version Catalog
- **THEN** the deployment fails with a validation error

---

### Requirement: Aurora Serverless v2 Mode

The system SHALL support Aurora Serverless v2 with configurable min and max ACU via `DatabaseConfig.deployment`.

#### Scenario: Serverless v2 deployment
- **WHEN** `deployment.mode` is `aurora_serverless_v2`
- **AND** `deployment.serverless_v2` is provided with `min_acu` and `max_acu`
- **THEN** the CDK stack creates an Aurora Serverless v2 cluster
- **AND** `deployment.provisioned` MUST be null

#### Scenario: ACU validation - min less than or equal to max
- **WHEN** `serverless_v2.min_acu` is greater than `serverless_v2.max_acu`
- **THEN** the deployment fails with a validation error

#### Scenario: ACU validation - 0.5 increments
- **WHEN** `min_acu` or `max_acu` is not a multiple of 0.5
- **THEN** the deployment fails with a validation error

#### Scenario: Missing serverless config
- **WHEN** `deployment.mode` is `aurora_serverless_v2`
- **AND** `deployment.serverless_v2` is null
- **THEN** the deployment fails with a validation error

---

### Requirement: Aurora Provisioned Mode

The system SHALL support Aurora provisioned clusters with configurable instance class via `DatabaseConfig.deployment`.

#### Scenario: Provisioned deployment
- **WHEN** `deployment.mode` is `aurora_provisioned`
- **AND** `deployment.provisioned` is provided with `instance_class`
- **THEN** the CDK stack creates an Aurora provisioned cluster with that instance type
- **AND** `deployment.serverless_v2` MUST be null
- **AND** `auto_minor_version_upgrade` from `engine` config is applied to the writer instance

#### Scenario: Instance class format
- **WHEN** `instance_class` is provided (e.g., `db.r6g.large`)
- **THEN** the leading `db.` prefix is stripped when converting to EC2 InstanceType

#### Scenario: Missing provisioned config
- **WHEN** `deployment.mode` is `aurora_provisioned`
- **AND** `deployment.provisioned` is null
- **THEN** the deployment fails with a validation error

---

### Requirement: Backup Configuration

The system SHALL support backup configuration via `DatabaseConfig.backups`.

#### Scenario: Backup retention
- **WHEN** `backups.retention_days` is provided
- **THEN** the Aurora cluster backup retention is set to that value

#### Scenario: Copy tags to snapshot
- **WHEN** `backups.copy_tags_to_snapshot` is true
- **THEN** Aurora snapshots inherit tags from the cluster (including `App={app_name}`)

---

### Requirement: Security Configuration

The system SHALL support security settings via `DatabaseConfig.security`.

#### Scenario: Storage encryption
- **WHEN** `security.storage_encrypted` is true
- **THEN** the Aurora cluster has encryption at rest enabled

#### Scenario: Deletion protection
- **WHEN** `security.deletion_protection` is true
- **THEN** the Aurora cluster has deletion protection enabled

---

### Requirement: Connection Injection

The system SHALL inject database connection details into ECS tasks via Secrets Manager.

#### Scenario: Connection URL injection
- **WHEN** an app has a `database_config` with `connection.env_var_name`
- **THEN** the ECS task receives that env var (default `DATABASE_URL`) with the connection URL
- **AND** the URL format is `{scheme}://{username}:{password}@{host}:{port}/{dbname}`

#### Scenario: Individual field injection
- **WHEN** an app has a `database_config`
- **THEN** the ECS task receives these env vars from Secrets Manager:
  - `DATABASE_HOST`
  - `DATABASE_PORT`
  - `DATABASE_NAME`
  - `DATABASE_USERNAME`
  - `DATABASE_PASSWORD`

#### Scenario: Secret isolation
- **WHEN** an app's task role is created
- **THEN** it has IAM permissions ONLY for its own database connection secret
- **AND** it cannot read other apps' secrets

---

### Requirement: Resource Naming

The system SHALL namespace all Aurora resources by app name to avoid collisions.

#### Scenario: Cluster identifier
- **WHEN** an Aurora cluster is created for app `{app_name}`
- **THEN** the cluster identifier is `devopshero-{app_name}-aurora`

#### Scenario: Credentials secret
- **WHEN** Aurora generates credentials
- **THEN** the secret name is `devopshero/{app_name}/aurora/credentials`

#### Scenario: Connection secret
- **WHEN** the derived connection secret is created
- **THEN** the secret name is `devopshero/{app_name}/aurora/connection`

#### Scenario: Resource tagging
- **WHEN** Aurora resources are created
- **THEN** they are tagged with `App={app_name}`

---

### Requirement: Network Security

The system SHALL deploy Aurora clusters securely within the VPC.

#### Scenario: Private subnet deployment
- **WHEN** an Aurora cluster is created
- **THEN** it is deployed into private subnets (PRIVATE_WITH_EGRESS)

#### Scenario: Security group rules
- **WHEN** an Aurora cluster is created
- **THEN** its security group allows inbound traffic ONLY from the ECS task default security group
- **AND** the allowed port is engine-specific (3306 for MySQL, 5432 for PostgreSQL)

---

## Implementation Notes

### DatabaseConfig Schema

```python
DatabaseConfig:
  name: str                    # Default database name on cluster
  engine: EngineConfig
  deployment: DeploymentConfig
  backups: BackupConfig
  security: SecurityConfig
  connection: ConnectionConfig

EngineConfig:
  family: str                  # "aurora-mysql" | "aurora-postgresql"
  version: str | None          # Version key or None for default
  auto_minor_version_upgrade: bool

DeploymentConfig:
  mode: str                    # "aurora_serverless_v2" | "aurora_provisioned"
  serverless_v2: ServerlessV2Config | None
  provisioned: ProvisionedConfig | None

ServerlessV2Config:
  min_acu: float
  max_acu: float

ProvisionedConfig:
  instance_class: str          # e.g., "db.r6g.large"

BackupConfig:
  retention_days: int
  copy_tags_to_snapshot: bool

SecurityConfig:
  storage_encrypted: bool
  deletion_protection: bool

ConnectionConfig:
  env_var_name: str | None     # Defaults to "DATABASE_URL"
```

### Engine Version Catalog

| Family | Version Key | CDK Constant |
|--------|-------------|--------------|
| aurora-mysql | 3.04.0 | `AuroraMysqlEngineVersion.VER_3_04_0` |
| aurora-postgresql | 15.4 | `AuroraPostgresEngineVersion.VER_15_4` |

### Hardcoded Values

- Database admin username: `dbadmin`
- No reader instances in v1
- `RemovalPolicy.DESTROY` on cluster and subnet group (for dev/test teardown)

### Key Files

- `infra_customer/appconfig.py` — DatabaseConfig dataclasses
- `infra_customer/deploy_app.py` — AuroraClusterStack CDK implementation
- `infra_customer/example_apps.py` — Example usage in db-portal config
