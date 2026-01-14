# Scheduled Tasks Runner

A scheduled task runner for periodic jobs triggered by EventBridge. This is **not a web server** - it runs a single task and exits.

## Overview

This application demonstrates cron-like scheduled jobs pattern commonly used in enterprise applications for:
- Data synchronization
- Report generation
- Cleanup operations
- Health checks

## Available Tasks

- **cleanup** - Cleans up old files in S3 (logs simulated deletions)
- **report** - Generates a JSON report and uploads to S3
- **heartbeat** - Logs a heartbeat message (useful for monitoring)

## Usage

Run via command line argument:
```bash
python -m tasks.main --task=cleanup
python -m tasks.main --task=report
python -m tasks.main --task=heartbeat
```

Or via environment variable:
```bash
TASK_NAME=cleanup python -m tasks.main
```

Command line arguments take precedence over environment variables.

## Exit Codes

- `0` - Task completed successfully
- `1` - Task failed or unknown task name

## Environment Variables

See `.env.example` for required configuration:
- `TASK_NAME` - Which task to run (cleanup, report, heartbeat)
- `S3_BUCKET` - S3 bucket for operations
- `AWS_REGION` - AWS region (defaults to us-east-1)
- `LOG_LEVEL` - Logging level (defaults to INFO)

## Deployment

This task runner is designed to be deployed as an ECS Fargate task triggered by EventBridge scheduled rules. The deployment agent will generate the appropriate Dockerfile.

### EventBridge Integration

When triggered by EventBridge, the task name can be passed via:
1. Container command override: `["--task=cleanup"]`
2. Environment variable in task definition: `TASK_NAME=cleanup`
