# Reference Applications

Test applications for the DevOps Hero deployment agent. Each app validates different detection patterns, frameworks, and AWS infrastructure integrations.

## Standards

All apps follow these conventions:
- **README.md** — Local setup instructions
- **`.env.example`** — Documents required environment variables
- **No Dockerfile** — The deployment agent generates this
- **Health endpoint** — `/health` for web apps (required for ALB health checks)

---

## Phase 1: Core Web Patterns

### django_postgres_app

**What:** Django web app with PostgreSQL database. Primary test case for the deployment agent — the most common pattern for target users (Full Stack Engineers, Data Scientists).

**Tech Stack:**
- Python 3.12
- Django 5.x
- PostgreSQL via `dj-database-url`
- Gunicorn

**Features:**
- User authentication (Django built-in)
- Note CRUD model with migrations
- Django admin interface
- Health check at `/health`

**Infrastructure:**
- ECS Fargate
- ALB
- Aurora PostgreSQL

---

### fastapi_app

**What:** Stateless FastAPI application with no database. Validates that the agent can deploy apps without provisioning Aurora.

**Tech Stack:**
- Python 3.12
- FastAPI
- Uvicorn

**Features:**
- REST endpoints (`/items`, `/echo`)
- Environment info endpoint (`/env`)
- OpenAPI docs at `/docs`
- Health check at `/health`

**Infrastructure:**
- ECS Fargate
- ALB

---

## Phase 2: Framework Variety

### nextjs_app

**What:** Next.js application testing Node.js/JavaScript detection and SSR deployment.

**Tech Stack:**
- Node 20
- Next.js 14 (App Router)
- TypeScript
- React 18

**Features:**
- Server-side rendered page
- Static page generation
- API routes (`/api/hello`, `/api/health`)
- Standalone output for optimized Docker builds

**Infrastructure:**
- ECS Fargate
- ALB

---

### phoenix_app

**What:** Elixir/Phoenix application with PostgreSQL. Clean test case for Elixir detection.

**Tech Stack:**
- Elixir 1.16+
- Phoenix 1.7+
- Ecto
- Bandit (web server)

**Features:**
- Post CRUD API with Ecto migrations
- JSON API controllers
- Health check at `/health` with DB verification

**Infrastructure:**
- ECS Fargate
- ALB
- Aurora PostgreSQL

---

### graphql_api

**What:** GraphQL API using Apollo Server. Tests modern API patterns and GraphQL detection.

**Tech Stack:**
- Node 20
- Apollo Server 4
- Express
- ES Modules

**Features:**
- Book schema with queries and mutations
- In-memory data store
- GraphQL Playground at `/graphql`
- REST health check at `/health`

**Infrastructure:**
- ECS Fargate
- ALB

---

### admin_dashboard

**What:** React admin dashboard with Express backend. Tests modern SPA deployment — exactly the kind of internal tool DevOps Hero targets.

**Tech Stack:**
- Node 20
- Vite 5
- React 18
- TypeScript
- Express 4
- React Router 6

**Features:**
- Dashboard with stats and charts
- User management views (list, detail)
- Express API (`/api/users`, `/api/stats`)
- Backend serves built React app in production
- Health check at `/health`

**Infrastructure:**
- ECS Fargate
- ALB
- Aurora PostgreSQL (for production data)

---

## Phase 3: AWS Service Integrations

### ml_model_api

**What:** ML model serving API for data science teams. Tests S3 integration for model storage and larger memory requirements.

**Tech Stack:**
- Python 3.12
- FastAPI
- scikit-learn
- boto3
- joblib

**Features:**
- Model inference at `/predict`
- Load model from S3 or local file
- Model info at `/model/info`
- Includes training script and sample Iris classifier
- Health check at `/health`

**Infrastructure:**
- ECS Fargate (larger memory)
- ALB
- S3 (model storage)

---

### realtime_app

**What:** WebSocket real-time chat application. Tests WebSocket support and DynamoDB integration for connection state.

**Tech Stack:**
- Python 3.12
- FastAPI
- WebSockets
- boto3

**Features:**
- WebSocket endpoint at `/ws`
- Broadcast messages to all connected clients
- Store connections and messages in DynamoDB
- In-memory fallback for local development
- REST endpoint `/messages` for history
- Health check at `/health`

**Infrastructure:**
- ECS Fargate
- ALB (WebSocket support)
- DynamoDB (connections table, messages table)

---

### file_processor

**What:** File processing service with S3 integration. Tests event-driven patterns common in enterprise (document conversion, image resizing).

**Tech Stack:**
- Python 3.12
- FastAPI
- boto3
- Pillow

**Features:**
- Upload files to S3 (`POST /upload`)
- Download files (`GET /files/{file_id}`)
- List files (`GET /files`)
- Image resizing processor
- S3 event webhook (`POST /webhook/s3`)
- Local storage fallback for development
- Health check at `/health`

**Infrastructure:**
- ECS Fargate
- ALB
- S3 (uploads and processed files)
- S3 Event Notifications

---

## Phase 4: Worker Patterns (No HTTP)

### job_processor

**What:** Background job processor consuming messages from SQS. Tests worker pattern with queue processing — no ALB needed.

**Tech Stack:**
- Python 3.12
- boto3

**Features:**
- SQS long-polling message consumption
- Job types: `echo`, `transform_data`
- S3 integration for job artifacts
- Graceful shutdown (SIGTERM/SIGINT)
- `--local` mode for testing without SQS

**Infrastructure:**
- ECS Fargate (no ALB)
- SQS
- S3

---

### slack_bot

**What:** Slack bot running as a worker process. Tests worker workload type for bots and background services.

**Tech Stack:**
- Python 3.12
- slack-sdk
- boto3

**Features:**
- SQS polling for Slack events
- Handle `message` and `app_mention` events
- Echo responses and greetings
- Graceful shutdown (SIGTERM/SIGINT)
- `--local` mode for interactive testing

**Infrastructure:**
- ECS Fargate (no ALB)
- SQS

---

### scheduled_tasks

**What:** Scheduled task runner for EventBridge triggers. Tests cron-like scheduled jobs pattern — runs, completes, exits.

**Tech Stack:**
- Python 3.12
- boto3

**Features:**
- Tasks: `cleanup`, `report`, `heartbeat`
- Run via `--task=<name>` or `TASK_NAME` env var
- S3 integration for artifacts
- Proper exit codes (0 success, 1 failure)
- No persistent HTTP server

**Infrastructure:**
- ECS Fargate (no ALB)
- EventBridge (scheduled rules)
- S3

---

## Infrastructure Summary

| App | ECS | ALB | Aurora | DynamoDB | S3 | SQS | EventBridge |
|-----|-----|-----|--------|----------|-----|-----|-------------|
| django_postgres_app | x | x | x | | | | |
| fastapi_app | x | x | | | | | |
| nextjs_app | x | x | | | | | |
| phoenix_app | x | x | x | | | | |
| graphql_api | x | x | | | | | |
| admin_dashboard | x | x | x | | | | |
| ml_model_api | x | x | | | x | | |
| realtime_app | x | x | | x | | | |
| file_processor | x | x | | | x | | |
| job_processor | x | | | | x | x | |
| slack_bot | x | | | | | x | |
| scheduled_tasks | x | | | | x | | x |
