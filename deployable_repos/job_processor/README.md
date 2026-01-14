# Job Processor

A background worker that processes jobs from an SQS queue. This is a **worker process**, not a web server - it runs continuously consuming messages from a queue.

## Architecture

- **Queue-driven**: Long-polls SQS for messages
- **Artifact storage**: Reads/writes job artifacts to S3
- **Graceful shutdown**: Handles SIGTERM/SIGINT for clean container stops

## Job Types

- **echo** - Logs the message payload (useful for testing)
- **transform_data** - Reads JSON from S3, applies transformation, writes result back

## Configuration

Set these environment variables (see `.env.example`):

- **SQS_QUEUE_URL** - Full URL of the SQS queue to poll
- **S3_BUCKET** - Bucket name for job artifacts
- **AWS_REGION** - AWS region (default: us-east-1)

## Running

### Production (with SQS)

```bash
python -m worker.main
```

### Local Development (without SQS)

Use `--local` mode to read jobs from stdin or a file:

```bash
# From stdin (one JSON job per line)
echo '{"job_type": "echo", "payload": {"message": "Hello"}}' | python -m worker.main --local

# From a file
python -m worker.main --local --file jobs.jsonl
```

## Message Format

Jobs sent to SQS should have this JSON structure:

```json
{
  "job_type": "echo",
  "payload": {
    "message": "Hello from the queue"
  }
}
```

For `transform_data` jobs:

```json
{
  "job_type": "transform_data",
  "payload": {
    "input_key": "input/data.json",
    "output_key": "output/result.json",
    "transform": "uppercase"
  }
}
```

## Deployment

This worker is designed to run as an ECS task (Fargate). The deployment agent will generate the appropriate Dockerfile. Key deployment considerations:

- No port exposure needed (pure worker)
- Set appropriate task CPU/memory based on job complexity
- Configure SQS visibility timeout based on expected job duration
- Use dead-letter queue for failed messages
