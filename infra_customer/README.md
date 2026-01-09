# Customer Infrastructure Deployment

Deploy apps to customer AWS accounts using CDK.

## Usage

```bash
# Deploy base infrastructure (VPC + ECS cluster)
uv run python deploy.py --base

# Deploy an app
uv run python deploy.py --app simple-dashboard
uv run python deploy.py --app db-portal

# Deploy with specific image tag
uv run python deploy.py --app simple-dashboard --image-tag v1.2.3

# Teardown
uv run python deploy.py --app simple-dashboard --teardown
uv run python deploy.py --base --teardown
```

## Available Apps

Defined in `example_apps.py`:
- **simple-dashboard** — Streamlit app (no database)
- **db-portal** — Phoenix/Elixir app with Aurora MySQL

## Architecture

```
Customer AWS Account
├── VPC (private subnets + NAT Gateway)
├── ECS Cluster (Fargate)
├── ECR Repository (per app)
├── ALB with HTTPS (ACM cert, Route53)
└── Aurora Serverless v2 (if database needed)
```

## Key Files

- **deploy.py** — CLI entry point
- **deploy_base.py** — VPC + ECS cluster stacks
- **deploy_app.py** — ECR, Aurora, ALB, ECS service stacks
- **appconfig.py** — AppConfig and DatabaseConfig dataclasses
- **example_apps.py** — App configuration registry
