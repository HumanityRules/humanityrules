# Humanity Rules

Enterprise platform for deploying governed AI personal assistants inside your company’s own cloud (AWS VPC) — curated skills, sandboxed execution, ABAC permissions, SSO, approval workflows, and full audit trails. Built on the open-source Hermes agent and Hermes WebUI.


## Setup

```bash
# Syn Python dependencies
uv sync

# Copy environment variables
cp .env.example .env
# Edit .env with your WorkOS and AWS credentials

# Run migrations
uv run manage.py migrate
```

## Development

```bash
# Start Django + Tailwind (recommended, reads from Procfile.tailwind)
uv run manage.py tailwind dev

# Start Django without Tailwind reload
uv run run_dev.py

# The job worker needs to be run explicitly, unless you set DOH_RUN_JOB_WORKER=1 in .env
uv run manage.py run_job_worker

# Migrations
uv run manage.py makemigrations
uv run manage.py migrate

# Django admin (useful for debugging auth)
uv run manage.py createsuperuser
# Then visit /admin/
```

## Bootstrap (historical)

How this project was created:

```bash
uv init .
uv add django==6.0
uv run django-admin startproject devopshero_site .
uv run manage.py startapp devopshero_app
uv run manage.py migrate
```
