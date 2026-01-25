FROM python:3.14-slim

WORKDIR /app

# Install curl for health checks, Node.js for CDK/jsii, and uv for dependency management
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg && \
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

# Expose the application port
EXPOSE 8000

# Run with uvicorn (ASGI server)
CMD ["uv", "run", "uvicorn", "devopshero_site.asgi:application", "--host", "0.0.0.0", "--port", "8000"]
