# DOH Policy Proxy — enforces SSO + ABAC in front of app containers.
FROM python:3.14-slim-bookworm

WORKDIR /app

# uvicorn + FastAPI + httpx + pyjwt[crypto] — pinned in requirements.txt.
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt || pip install --no-cache-dir -r requirements.txt

COPY policy_proxy ./policy_proxy

# Expose the policy proxy's listen port. The actual port is driven by
# HUMR_LISTEN_PORT at runtime and must match the ALB target group config.
EXPOSE 8443

# Uvicorn reads HUMR_LISTEN_PORT and binds to 0.0.0.0 on it.
ENTRYPOINT ["python", "-m", "policy_proxy.main"]
