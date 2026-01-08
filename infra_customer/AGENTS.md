# CDK Code Style Guide

## Line Length and Function Calls

Prefer single-line function calls unless they exceed ~140 characters:

```python
# Good - fits on one line
self.cluster = ecs.Cluster(self, "EcsCluster", cluster_name="devopshero-cluster", vpc=vpc, container_insights_v2=ecs.ContainerInsights.ENABLED)

# Good - too long, use multi-line
self.task_execution_role = iam.Role(
    self, "TaskExecutionRole",
    role_name="devopshero-ecs-task-execution-role",
    assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
    managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonECSTaskExecutionRolePolicy")],
)

# Good - nested structures benefit from multi-line for readability
self.vpc = ec2.Vpc(
    self, "Vpc",
    vpc_name="devopshero-vpc",
    ip_addresses=ec2.IpAddresses.cidr(vpc_cidr),
    max_azs=2,
    nat_gateways=1,
    subnet_configuration=[
        ec2.SubnetConfiguration(name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
        ec2.SubnetConfiguration(name="Private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24),
    ],
)
```

## CfnOutput

Use one-liners without description (the output name is self-explanatory):

```python
# Good
CfnOutput(self, "VpcId", value=self.vpc.vpc_id, export_name="devopshero-vpc-id")

# Bad
CfnOutput(
    self,
    "VpcId",
    value=self.vpc.vpc_id,
    export_name="devopshero-vpc-id",
    description="VPC ID",
)
```

## Environment-Agnostic Stacks

Don't pass explicit `env` to stacks. This makes them portable and uses `Fn::GetAZs` at deploy time (like raw CloudFormation):

```python
# Good - AZs resolve at deploy time via Fn::GetAZs
vpc_stack = VpcStack(cdk_app, "devopshero-vpc-cdk", vpc_cidr=vpc_cidr)

# Bad - triggers synth-time AZ resolution, causes dummy values in cross-account scenarios
vpc_stack = VpcStack(cdk_app, "devopshero-vpc-cdk", vpc_cidr=vpc_cidr,
    env={"account": account_id, "region": region})
```

## Comments

Add comments that explain WHY, non-obvious behavior, or WHAT when it's not immediately clear from the code:

```python
# Good - explains non-obvious behavior
# Environment-agnostic: AZs resolve to Fn::GetAZs at deploy time
self.vpc = ec2.Vpc(...)

# Good - explains what a non-obvious value means
desired_count=0,  # Start at 0, scaled up after image push

# Good - clarifies what a parameter does when not obvious
capture_output=False,  # Show output in real-time

# Bad - obvious from the code
# Create VPC with public and private subnets
self.vpc = ec2.Vpc(...)

# Bad - obvious from the variable name
# Task Definition
task_definition = ecs.FargateTaskDefinition(...)
```

## Naming: Field Names Should Reflect Their Types

Field names should hint at their type to avoid ambiguity:

```python
# Good - field name correlates with the type
database_config: DatabaseConfig | None = None
user_settings: UserSettings | None = None

# Bad - "database" could be a connection, a name, a config...
database: DatabaseConfig | None = None
```
