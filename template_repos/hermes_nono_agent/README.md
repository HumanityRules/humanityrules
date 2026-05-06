# Hermes nono Agent

Minimal Hermes Agent template for the nono sandbox path.

This intentionally starts smaller than the current `hermes_agent` template:

- one `hermes` container
- no Docker-in-Docker sidecar
- no policy proxy sidecar
- no Slack gateway
- no EFS mounts

State is ephemeral until the initial container shape is working.
