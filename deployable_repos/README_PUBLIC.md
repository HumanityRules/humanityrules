# Public Reference Applications (Vendored)

These are **public GitHub repositories** vendored into this repo under `deployable_repos/public/<slug>/` so DevOps Hero (DOH) can test deployment against “real” applications, not just our hand-made examples.

- **Import mechanism**: `git subtree add --squash`
- **Note on Dockerfiles**: these upstream repos may include Dockerfiles / Compose / IaC. We intentionally did **not** modify or remove them.

---

## Apps

### django_postgres

- **Path**: `deployable_repos/public/django_postgres`
- **Upstream**: `git@github.com:testdrivenio/django-on-docker.git`
- **Imported ref**: `26be9cd5f891734425e4fe754d6340b92180b471`
- **Container artifacts**:
  - **Dockerfile**: yes (`app/Dockerfile`, `app/Dockerfile.prod`, `nginx/Dockerfile`)
  - **docker-compose**: yes (`docker-compose.yml`, `docker-compose.prod.yml`)
- **What it is**: A production-oriented Django example showing the “classic” Django + Postgres pattern (settings via env, migrations, Gunicorn).
- **Tech stack**:
  - Django (Python)
  - PostgreSQL
  - Gunicorn (typical production WSGI server)
- **Deployment infra dependencies (DOH)**:
  - **ECS/Fargate** web service
  - **ALB** (health checks, routing)
  - **Aurora PostgreSQL** (or RDS Postgres)
  - **One-off migration task** (run `migrate` during deploy)

---

### fastapi_stateless

- **Path**: `deployable_repos/public/fastapi_stateless`
- **Upstream**: `git@github.com:ArmanShirzad/fastapi-production-template.git`
- **Imported ref**: `94b1cd830f6a3ff5646ac08f987963be2545e92e`
- **Container artifacts**:
  - **Dockerfile**: yes (`Dockerfile`)
  - **docker-compose**: yes (`docker-compose.yml`)
- **What it is**: A production-shaped FastAPI template that can run stateless (no database required) and is representative of “internal APIs”.
- **Tech stack**:
  - FastAPI (Python)
  - ASGI server (e.g. Uvicorn)
- **Deployment infra dependencies (DOH)**:
  - **ECS/Fargate** web service
  - **ALB**

---

### nextjs

- **Path**: `deployable_repos/public/nextjs`
- **Upstream**: `git@github.com:vercel/commerce.git`
- **Imported ref**: `1df2cf6f6c935f4782eed27351fa18f276917a4d`
- **Container artifacts**:
  - **Dockerfile**: none found
  - **docker-compose**: none found
- **What it is**: A substantial Next.js application (real product shape) for exercising Node/Next detection and SSR deployment.
- **Tech stack**:
  - Next.js (Node/React)
  - TypeScript
- **Deployment infra dependencies (DOH)**:
  - **ECS/Fargate** web service
  - **ALB**
  - **External integrations via env** (provider keys/config; depends on chosen backend)

---

### slack_bot

- **Path**: `deployable_repos/public/slack_bot`
- **Upstream**: `git@github.com:slack-samples/bolt-python-getting-started-app.git`
- **Imported ref**: `94b5333951799342aebe57112241b4cc682831f6`
- **Container artifacts**:
  - **Dockerfile**: none found
  - **docker-compose**: none found
- **What it is**: A canonical Slack Bolt (Python) app that receives events and responds—useful for testing “bot-style” service deployment.
- **Tech stack**:
  - Slack Bolt (Python)
  - HTTP event handling (Slack Events API / interactive components)
- **Deployment infra dependencies (DOH)**:
  - **ECS/Fargate** web service (publicly reachable)
  - **ALB** (TLS + stable URL for Slack to call)
  - **Secrets/env** for Slack tokens/signing secret

---

### phoenix_simple

- **Path**: `deployable_repos/public/phoenix_simple`
- **Upstream**: `git@github.com:phoenix-examples/hello_phoenix.git`
- **Imported ref**: `b95735efae68c0a47e0d2ad4ffefa6ab42614741`
- **Container artifacts**:
  - **Dockerfile**: none found
  - **docker-compose**: none found
- **What it is**: A simple Phoenix web app used to validate Elixir/Phoenix detection and containerization.
- **Tech stack**:
  - Elixir + Phoenix
  - Typical Phoenix endpoint/web server
- **Deployment infra dependencies (DOH)**:
  - **ECS/Fargate** web service
  - **ALB**

---

### ml_model_serving_api

- **Path**: `deployable_repos/public/ml_model_serving_api`
- **Upstream**: `git@github.com:aws-samples/lambda-serverless-inference-fastapi.git`
- **Imported ref**: `c7440956936204e6a09a51199b3095f91e9ade57`
- **Container artifacts**:
  - **Dockerfile**: yes (`model_endpoint/docker/Dockerfile`)
  - **docker-compose**: none found
- **What it is**: An AWS sample showing a FastAPI-based inference endpoint that downloads/loads model artifacts from S3-like storage (pattern match for “model serving API”).
- **Tech stack**:
  - FastAPI (Python)
  - Model artifact loading (S3/object storage)
- **Deployment infra dependencies (DOH)**:
  - **ECS/Fargate** web service + **ALB** (if adapted from Lambda to containers)
  - **S3** (model artifacts)
  - **IAM task role** allowing model reads
  - **Higher memory/ephemeral storage** may be required for larger models

---

### background_job_processor

- **Path**: `deployable_repos/public/background_job_processor`
- **Upstream**: `git@github.com:aws-samples/aws-batch-celery-worker-example.git`
- **Imported ref**: `e986f105223b67bf50ffda51df9f8727d8bd24c3`
- **Container artifacts**:
  - **Dockerfile**: yes (`cdk-project/batch_celery_container/Dockerfile`)
  - **docker-compose**: none found
- **What it is**: A concrete “background worker” reference using Celery + SQS; maps to our worker workload type (consume messages, process, emit artifacts).
- **Tech stack**:
  - Python worker
  - Celery
  - SQS (message queue / broker pattern)
- **Deployment infra dependencies (DOH)**:
  - **ECS/Fargate** worker service (no ALB)
  - **SQS** queue
  - **IAM task role** allowing SQS access (+ optional S3 if used for artifacts)

---

### graphql_api

- **Path**: `deployable_repos/public/graphql_api`
- **Upstream**: `git@github.com:petarivanovv9/graphql-api-ts-ddd-clean-architecture.git`
- **Imported ref**: `b50470329bed9eca27ab62558704330c839d91c0`
- **Container artifacts**:
  - **Dockerfile**: none found
  - **docker-compose**: none found
- **What it is**: Apollo Server (TypeScript) GraphQL API with a more “enterprise” code structure—good for GraphQL detection.
- **Tech stack**:
  - Node.js + TypeScript
  - Apollo Server (GraphQL)
- **Deployment infra dependencies (DOH)**:
  - **ECS/Fargate** web service
  - **ALB**
  - Optional datastore depending on configuration (many GraphQL APIs add Postgres/Redis later)

---

### internal_admin_dashboard

- **Path**: `deployable_repos/public/internal_admin_dashboard`
- **Upstream**: `git@github.com:netbox-community/netbox.git`
- **Imported ref**: `6bd083b7ed4c5a6767a3be23bf26832b3eb26e02`
- **Container artifacts**:
  - **Dockerfile**: none found
  - **docker-compose**: none found
- **What it is**: NetBox is a well-known internal admin tool pattern (Django + Postgres + background workers + caching), representative of real enterprise deployments.
- **Tech stack**:
  - Django (Python)
  - PostgreSQL
  - Redis (commonly used by NetBox for caching/queues)
- **Deployment infra dependencies (DOH)**:
  - **ECS/Fargate** web service + **ALB**
  - **Aurora PostgreSQL**
  - **Redis** (ElastiCache) + often a **separate worker** service
  - **One-off migration task** during deploy

---

### scheduled_task_runner

- **Path**: `deployable_repos/public/scheduled_task_runner`
- **Upstream**: `git@github.com:aws-samples/aws-ecs-scheduled-tasks.git`
- **Imported ref**: `bba3ca7a4ba6874e05ca736069bc750be8df1f92`
- **Container artifacts**:
  - **Dockerfile**: yes (`container-code/src/Dockerfile`)
  - **docker-compose**: none found
- **What it is**: AWS sample showing the “scheduled container task” pattern (cron → run a task → exit), matching our EventBridge-triggered job category.
- **Tech stack**:
  - Containerized task (language varies; pattern is the key)
  - AWS scheduling primitives
- **Deployment infra dependencies (DOH)**:
  - **ECS task definition** (run-to-completion)
  - **EventBridge** schedule rules targeting ECS RunTask
  - **IAM** for EventBridge to run tasks, plus task role perms for workload

---

### realtime_websocket_app

- **Path**: `deployable_repos/public/realtime_websocket_app`
- **Upstream**: `git@github.com:aws-samples/simple-websockets-chat-app.git`
- **Imported ref**: `fb1f0d686ae499194b2e9879e840c6222fd11f42`
- **Container artifacts**:
  - **Dockerfile**: none found
  - **docker-compose**: none found
- **What it is**: A DynamoDB-backed WebSocket chat sample (API Gateway WebSockets) that’s a good analogue for “real-time app with DynamoDB state/history”.
- **Tech stack**:
  - WebSocket API (AWS API Gateway WebSockets in this repo)
  - DynamoDB
- **Deployment infra dependencies (DOH)**:
  - **DynamoDB** table(s)
  - For DOH’s ECS-only substrate: this pattern typically requires adapting to an **ECS WebSocket server behind an ALB**, while keeping DynamoDB for state/history

---

### file_processing_service

- **Path**: `deployable_repos/public/file_processing_service`
- **Upstream**: `git@github.com:anuragsati/distributed-image-processor.git`
- **Imported ref**: `eb3d081a67698ce9b163e9bac9e5f45138554881`
- **Container artifacts**:
  - **Dockerfile**: none found
  - **docker-compose**: none found
- **What it is**: An event-driven image/file processing system that uses S3 as the artifact store and processes uploaded objects—matches the “S3 upload + triggered processing” enterprise pattern.
- **Tech stack**:
  - Image/file processing pipeline (service + workers)
  - S3/object storage
  - Queue/event-driven components (often SQS/Lambda/EventBridge depending on implementation)
- **Deployment infra dependencies (DOH)**:
  - **S3** bucket(s) (uploads + outputs)
  - **Event trigger path** (S3 event → queue/trigger → processor)
  - **ECS/Fargate** worker(s) (or run-to-completion tasks), depending on how the repo wires processing
  - **IAM task role** granting S3 access (+ queue/event perms if applicable)

