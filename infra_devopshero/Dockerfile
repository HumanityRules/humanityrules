FROM python:3.14-slim

WORKDIR /app

# Install curl for health checks, Node.js for CDK/jsii, git for repo cloning, and uv for dependency management
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg git && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*
RUN pip install uv

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock ./

# Install dependencies (no dev dependencies in production)
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

# Collect static files
RUN uv run python manage.py collectstatic --noinput

# Create non-root user with explicit UID/GID 1000 (must match EFS Access Point configuration)
# Required for Claude SDK's bypassPermissions mode, and because it's a good practice
RUN groupadd --gid 1000 appuser && \
    useradd --create-home --shell /bin/bash --uid 1000 --gid 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# Expose the application port
EXPOSE 8000

# Run with uvicorn (ASGI server)
CMD ["uv", "run", "uvicorn", "devopshero_site.asgi:application", "--host", "0.0.0.0", "--port", "8000"]
