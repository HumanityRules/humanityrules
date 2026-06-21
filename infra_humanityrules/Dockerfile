FROM python:3.14-slim

WORKDIR /app

# Install curl for health checks, Node.js for CDK/jsii, git for repo cloning, openssh-client + rsync for remote builder
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg git openssh-client rsync unzip && \
    curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install AWS CLI v2 (arm64) for remote builder SSM commands
RUN curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o /tmp/awscliv2.zip && \
    unzip -q /tmp/awscliv2.zip -d /tmp && \
    /tmp/aws/install && \
    rm -rf /tmp/aws /tmp/awscliv2.zip

# Install AWS Session Manager Plugin for SSH-over-SSM (remote builder)
RUN curl -fsSL "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_arm64/session-manager-plugin.deb" -o /tmp/ssm-plugin.deb && \
    dpkg -i /tmp/ssm-plugin.deb && \
    rm /tmp/ssm-plugin.deb
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
CMD ["uv", "run", "uvicorn", "humanityrules_site.asgi:application", "--host", "0.0.0.0", "--port", "8000"]
