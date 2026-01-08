# OpenSpec: Database Configuration for Customer Apps (Aurora Only)

## Metadata
- Spec ID: DOH-DBCFG-001
- Status: Draft
- Owner: DevOps Hero
- Last updated: 2026-01-07

## Summary
Define Aurora-only database configuration options for customer app deployments. The spec is limited to fields that will exist on `DatabaseConfig` and their mapping to AWS CDK resources. No UI flows or DevOps Hero domain modeling are included.

## Current State
- The example deployer in `infra_customer/` only supports Aurora Serverless v2 (MySQL).
- `DatabaseConfig` only includes a single field: the database name.
- Aurora settings are fixed (min 0.5 ACU, max 2 ACU, 7-day backups, encryption at rest).
- ECS tasks receive individual DB secrets (`DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME`, `DATABASE_USERNAME`, `DATABASE_PASSWORD`) only.

## Goals
- Support Aurora MySQL and Aurora PostgreSQL with configurable engine versions.
- Allow Aurora Serverless v2 scaling configuration.
- Allow Aurora provisioned clusters as an optional mode.
- Expose backup retention, copy-tags-to-snapshot, deletion protection, and encryption-at-rest settings.
- Inject both `DATABASE_URL` and individual DB fields into app containers via Secrets Manager.

## Non-Goals
- Non-Aurora engines (standalone RDS MySQL/Postgres/MariaDB).
- UI design, user flows, or business-domain modeling in the DevOps Hero database.
- Read replicas, global clusters, or cross-region replication.
- Pricing calculations.

## Functional Requirements
- FR-1: `DatabaseConfig` supports Aurora engine family selection: `aurora-mysql` or `aurora-postgresql`.
- FR-2: `DatabaseConfig` supports explicit engine version selection.
- FR-3: `DatabaseConfig` supports Aurora Serverless v2 with configurable min and max ACU.
- FR-4: `DatabaseConfig` supports Aurora provisioned mode with an instance class.
- FR-5: `DatabaseConfig` parameters are orthogonal by deployment mode (only the relevant child config is set).
- FR-6: `DatabaseConfig` supports backup retention days, copy-tags-to-snapshot, deletion protection, and encryption at rest.
- FR-7: App containers always receive `DATABASE_URL` and the individual DB fields via Secrets Manager injection.

## DatabaseConfig
### Schema
```
DatabaseConfig:
  name: string
  engine: EngineConfig
  deployment: DeploymentConfig
  backups: BackupConfig
  security: SecurityConfig
  connection: ConnectionConfig

EngineConfig:
  family: aurora-mysql | aurora-postgresql
  version: string
  auto_minor_version_upgrade: boolean

DeploymentConfig:
  mode: aurora_serverless_v2 | aurora_provisioned
  serverless_v2: ServerlessV2Config | null
  provisioned: ProvisionedConfig | null

ServerlessV2Config:
  min_acu: number
  max_acu: number

ProvisionedConfig:
  instance_class: string

BackupConfig:
  retention_days: number
  copy_tags_to_snapshot: boolean

SecurityConfig:
  storage_encrypted: boolean
  deletion_protection: boolean

ConnectionConfig:
  env_var_name: string
```

### Orthogonality Rules
- When `deployment.mode == aurora_serverless_v2`, `serverless_v2` is required and `provisioned` is null.
- When `deployment.mode == aurora_provisioned`, `provisioned` is required and `serverless_v2` is null.

### Field Notes
- `name` is the default database name created on the cluster.
- `engine.version` maps to a concrete CDK engine version constant per family.
- `connection.env_var_name` defaults to `DATABASE_URL`.

## Defaults and Validation
- `name` is required and must conform to Aurora naming rules.
- `engine.family` is required; default `aurora-mysql` for backward compatibility.
- `engine.version` defaults to the latest supported version for the chosen family.
- `deployment.mode` defaults to `aurora_serverless_v2`.
- Serverless mode requires `deployment.serverless_v2.min_acu` and `max_acu` with `min_acu <= max_acu`.
- Provisioned mode requires `deployment.provisioned.instance_class`.
- `backups.retention_days` defaults to 7 days; enforce engine limits.
- `backups.copy_tags_to_snapshot` defaults to true.
- `security.storage_encrypted` defaults to true.
- `security.deletion_protection` defaults to false.
- `engine.auto_minor_version_upgrade` defaults to true.

## Engine Version Catalog
Initial versions to expose:
- Aurora MySQL: `3.04.0` (maps to `AuroraMysqlEngineVersion.VER_3_04_0`).
- Aurora PostgreSQL: `15.4` (maps to `AuroraPostgresEngineVersion.VER_15_4`).

The catalog is hardcoded and should be updated as CDK exposes newer versions.

## CDK Mapping
### Aurora Serverless v2
- Use `aws_rds.DatabaseCluster`.
- Configure:
  - Engine: `aurora-mysql` or `aurora-postgresql` with explicit version.
  - `serverless_v2_min_capacity` and `serverless_v2_max_capacity`.
  - `writer=rds.ClusterInstance.serverless_v2("writer")`.
  - `default_database_name` from `DatabaseConfig.name`.
  - `credentials=rds.Credentials.from_generated_secret(...)`.
  - `backup` retention and `storage_encrypted`.
  - `copy_tags_to_snapshot` from `DatabaseConfig.backups.copy_tags_to_snapshot`.
  - `deletion_protection` on the cluster.

### Aurora Provisioned
- Use `aws_rds.DatabaseCluster`.
- Configure:
  - Engine with explicit version.
  - `writer=rds.ClusterInstance.provisioned("writer", instance_type=...)` using `instance_class`.
  - No reader instances in v1.
  - `default_database_name`, `credentials`, backup retention, `copy_tags_to_snapshot`, encryption, deletion protection.

## Secrets and App Injection
- Secrets live in Secrets Manager and are generated by Aurora.
- ECS tasks receive:
  - `DATABASE_URL` from a Secrets Manager secret field.
  - `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME`, `DATABASE_USERNAME`, `DATABASE_PASSWORD` from the Aurora secret fields.
- Keep the current injection paradigm: use `ecs.Secret.from_secrets_manager(...)` for all fields.
- `DATABASE_URL` must be injected without exposing plaintext credentials in CloudFormation. If the Aurora secret does not include a URL field, provision a derived secret (or update the Aurora secret) after cluster creation and reference it via Secrets Manager.

## Security and Access Control
- Database security group allows inbound from app task security groups only.
- Databases are deployed into private subnets (no public access in this spec).
- App task roles receive IAM permissions scoped to their database secrets only.

## Migration Notes
- Expand `DatabaseConfig` while preserving current behavior by default:
  - `engine_family=aurora-mysql`
  - `deployment_mode=aurora_serverless_v2`
  - `serverless_min_acu=0.5`, `serverless_max_acu=2`
  - `backup_retention_days=7`
  - `storage_encrypted=true`

## Open Questions
- None.
