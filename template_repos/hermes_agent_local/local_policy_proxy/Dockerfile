# HUMR local policy proxy — dev-only X-Forwarded-Host shim for Hermes compose.
FROM python:3.14-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY local_policy_proxy ./local_policy_proxy

EXPOSE 8788

ENTRYPOINT ["python", "-m", "local_policy_proxy.main"]
