# Slack Bot Worker

A worker-based Slack bot that processes events from an SQS queue. This is a **worker workload** - it does not serve HTTP traffic and does not require an ALB.

## Architecture

This bot follows the worker pattern for Slack event processing:

1. Slack events are received by an external ingress (API Gateway + Lambda, or similar)
2. Events are pushed to an SQS queue
3. This worker polls the queue and processes events
4. Responses are sent back to Slack via the Slack API

## Event Types

The bot handles the following events:

- **message** - Echoes messages back or responds with a greeting
- **app_mention** - Responds when the bot is @mentioned in a channel

## Configuration

All configuration is done via environment variables. See `.env.example` for required variables.

## Running Locally

### Prerequisites

- Python 3.12+
- A Slack app with Bot Token
- AWS credentials (for SQS access)
- An SQS queue for receiving events

### Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your credentials
```

### Local Mode (without SQS)

For local development without SQS, use the `--local` flag:

```bash
python -m worker.main --local
```

This starts an interactive mode where you can simulate events by typing messages.

### Production Mode (with SQS)

```bash
python -m worker.main
```

## Graceful Shutdown

The worker handles SIGTERM and SIGINT signals for graceful shutdown. When a shutdown signal is received:

1. The worker stops polling for new messages
2. Current message processing completes
3. The worker exits cleanly

This is important for container orchestration (ECS, Kubernetes) where graceful shutdown is expected.

## Deployment

This is a worker workload type - the deployment agent will:

- Generate an appropriate Dockerfile
- Deploy to ECS Fargate without an ALB
- Configure the task to run continuously

No HTTP health checks are needed since this is not a web service.
