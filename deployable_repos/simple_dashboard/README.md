# Simple Dashboard

A minimal Streamlit dashboard for testing the DevOps Hero deployment pipeline.

## What This Is

This is a **Track 1 MVP app** — the simplest possible application to prove the deployment pipeline works end-to-end:

- ✅ Single Python file
- ✅ No database
- ✅ No external services
- ✅ No secrets required
- ✅ Just serves a web UI on port 8501

## Local Development

```bash
cd deployable_repos/simple_dashboard

# Run with uv (auto-creates venv and installs deps)
uv run streamlit run app.py
```

Then visit http://localhost:8501

That's it! `uv run` reads `pyproject.toml`, creates a `.venv` if needed, installs dependencies, and runs the command — all in one step.

## Docker Build

```bash
# Build the image
docker build -t simple-dashboard .

# Run the container
docker run -p 8501:8501 simple-dashboard
```

## Deployment with DevOps Hero

This app is designed to be deployed via DevOps Hero's deployment pipeline:

1. Build → Docker image created
2. Push → Image pushed to ECR
3. Deploy → Fargate task/service created
4. URL → User gets a working URL

## Why This App Exists

See `docs/journal.md` entry "2025-12-28 - Strategic Direction: Dual-Track MVP Approach" for the full context.

TL;DR: We're proving the deployment pipeline with this simple app before tackling complex enterprise apps like `db_portal`.

