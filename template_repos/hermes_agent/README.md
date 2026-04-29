# Hermes Agent

[Hermes Agent](https://github.com/NousResearch/hermes-agent) with the [Hermes WebUI](https://github.com/nesquena/hermes-webui), packaged for DOH deployment on ECS + EFS.


## Local Setup

```bash
cp .env.example .env   # fill in keys
docker build -t hermes-agent .
docker run --rm --env-file .env -p 8787:8787 hermes-agent
```

WebUI at `http://localhost:8787`.


## Environment variables

Defined in `seed_app_templates.py` under `runtime_variables`. `category: "config"` vars land as plain ECS env vars; `category: "secret"` vars go through Secrets Manager and are wired into the task definition as `ecs.Secret` refs. See `template_deploy_service` and `secrets_utils` for the full flow.

Inside the container, `entrypoint.sh` renders **`~/.hermes/config.yaml`** from `config.yaml.template` on every boot. DOH owns this file; `terminal.backend` and the docker volumes are load-bearing for the sandbox, so user edits are not preserved.

API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `SLACK_*`, etc.) reach Hermes as plain ECS env vars and are consumed directly from the process environment — `run_agent.py` and the WebUI both resolve them via `os.getenv`. There is no `~/.hermes/.env` file; writing one would be redundant (python-dotenv defaults to `override=False`, so the file could never win over the process env anyway). The OpenAI-compatible base URL for `custom` providers is plumbed through `config.yaml`'s `model.base_url` (rendered from `DOH_LLM_BASE_URL`), not via `OPENAI_BASE_URL`.


## Provider config

DOH namespaces its own knobs as `DOH_LLM_*` (main) and `DOH_AUX_*` (auxiliary slots — vision, compression, skills hub, etc.; default to the main provider).

### AWS Bedrock

Bedrock has a few quirks handled by the entrypoint:

- Credentials come from the ECS task role via the standard boto3 credential chain; only `AWS_BEDROCK_REGION` needs to be set.
- `DOH_LLM_BASE_URL` is derived from the region.
- `boto3` is installed into the shared venv on first boot by `start.sh` (sentinel-guarded), so switching to Bedrock later doesn't require a rebuild.

For Claude on Bedrock, Hermes uses the `AnthropicBedrock` SDK (prompt caching, thinking budgets). Other models go through the Converse API.

### OpenAI and OpenAI-compatible APIs (`custom`)

Hermes does not treat `openai` as a runtime provider name for direct API access (it's not in `PROVIDER_REGISTRY`). Use **`custom`** with **`DOH_LLM_BASE_URL`** pointing at an OpenAI-compatible endpoint; the entrypoint writes that to `config.yaml`'s `model.base_url`, and `run_agent.py` passes it explicitly to the OpenAI SDK constructor — no env-var bridge needed.

**Example — GPT 5.4 Mini on the official OpenAI API** (e.g. from-template overrides):

| Variable | Value |
|----------|--------|
| `DOH_LLM_PROVIDER` | `custom` |
| `DOH_LLM_MODEL` | `gpt-5.4-mini` |
| `DOH_LLM_BASE_URL` | `https://api.openai.com/v1` |
| `OPENAI_API_KEY` | set via Secrets Manager (per-app or shared env secret) |

**Model id:** use the bare name (`gpt-5.4-mini`). A slash in the id (e.g. `openai/gpt-5.4-mini`) follows **OpenRouter-style** routing in Hermes, not the direct OpenAI API.

**Auxiliary LLM:** `DOH_AUX_PROVIDER` and `DOH_AUX_MODEL` default to the main values, but for **`custom`**, `DOH_AUX_BASE_URL` is **not** auto-filled (only Bedrock derives aux base URL). Set `DOH_AUX_BASE_URL` to the same URL as main (and optionally a different `DOH_AUX_MODEL`) if auxiliary features should hit the same API. Main and aux share **`OPENAI_API_KEY`**; there is no separate aux API key variable.


## Slack gateway

`start.sh` supervises WebUI + optional `python -m gateway.run`. Auto-enabled when `SLACK_APP_TOKEN` or `SLACK_BOT_TOKEN` is set — leave both **unset** (not empty) to disable. If either process dies the container exits and ECS restarts it.


## MCP aggregator sidecar (Slack template only)

The `hermes-slack` AppTemplate ships with a second container — `learneo-mcp` — in the same ECS task. Hermes reaches it over loopback at `http://127.0.0.1:7777/mcp` via the static `mcp_servers.learneo` entry in `config.yaml.template`. No auth header is needed: loopback binding is the security boundary (no port mapping, so nothing outside the task can reach the MCP).

The MCP image is referenced as a **prebuilt** container:

- **Repo:** `doh/{env_slug}/learneo-mcp` (per-env ECR).
- **Tag:** `LEARNEO_MCP_IMAGE_VERSION` in `seed_app_templates.py`.
- **How it's built:** operator-driven from a local checkout of the `learneo-mcp` source, pushed with `manage.py doh_build_prebuilt_image --account <acct> [--env <env>] --source-dir <path-to-learneo-mcp> --ecr-repo learneo-mcp --tag <version>`.

Upstream credentials (`LEARNEO_MCP_GITLAB_TOKEN`, `LEARNEO_MCP_ATLASSIAN_*`, `LEARNEO_MCP_DATADOG_*`, etc.) are declared on the Slack template as empty-value secrets. Ops populates them once in `devopshero/{env_slug}/shared-secrets` and every Hermes in the env inherits them via the fall-through mechanism in `secrets_utils._resolve_secret_value`.


## Patches

`patches/` carries DOH-owned fixes against the pinned `hermes-agent` tree: numbered `*.patch` files applied idempotently (`patch -N --forward`) and an `overlay/` tree for whole files DOH owns. `apply.py` runs on every boot against the **EFS-backed** copy, so a new image's patches reach already-deployed volumes. Already-applied patches become no-ops, so upstream fixes soft-land on the next rebuild.


## Bundled skills

Upstream ships 70+ skills under `hermes-agent/skills/` (GitHub CLI wrappers, creative generators, mlops toolkits, macOS-host skills, competitor coding agents, red-team kits, etc.). `entrypoint.sh` calls `sync_skills()` on every boot, which copies that tree into `~/.hermes/skills/` on EFS so the WebUI skills panel can pick them up.

DOH curates which skills ship via an **allowlist** at `skills-allowlist.txt` — one path per line, relative to `hermes-agent/skills/`. At image build time, `prune-skills.sh` runs inside the `git clone` layer and deletes every skill directory not listed. Allowlist, not exclusion list, on purpose: a version bump can't silently add new skills to Enterprise deployments — additions require a conscious edit.

The pruner fails the build if any allowlisted path no longer exists upstream (rename or removal). That's the point: on a version bump, an upstream rename should block the build so we can re-anchor the allowlist, not silently drop a skill.

**Adding or removing a skill:**

1. Edit `skills-allowlist.txt`. Keep it grouped by category; blank lines and `#` comments are fine.
2. Rebuild the image. Look for `[skills-allowlist] kept N skills` in the build log.
3. Redeploy. New apps get the new set on first boot.

**What stays behind on existing deployments:** `sync_skills()` in upstream only cleans its manifest when a bundled skill disappears — it **does not** delete the copy in `~/.hermes/skills/`, on the assumption the user may have customized it. So skills removed from the allowlist will stop being re-seeded on new apps but will persist in existing EFS volumes until manually scrubbed. The WebUI picks them up from `~/.hermes/skills/`, so they'll still appear for those tenants until a cleanup pass.

**On a version bump:** after bumping the `git clone --branch` pin, expect the build to fail with `FATAL: allowlisted skill 'foo/bar' not found in bundled repo` for any renamed or removed upstream skill. Clone the new tag locally, diff its `skills/` tree against the previous one, reconcile the allowlist, then rebuild.


## Storage: ephemeral vs EFS

All Hermes state (`config.yaml`, `SOUL.md`, `hermes-agent/`, `skills/`, `memories/`, `sessions/`, `workspace/`, WebUI state) lives on an EFS access point mounted at `~/.hermes`, scoped per app with the template's UID/GID (1024 today). The Dockerfile symlinks `/workspace` into this path so terminal tools persist their output.

Docker-backed terminal tools run in the sibling `docker-dind` container from the local `doh-toolbox:latest` tag. Hermes's persistent Docker cleanup stops but does not remove `hermes-*` containers; the DinD snapshotter commits stopped containers back into `doh-toolbox:latest` on the Docker `die` event and periodically saves that image to the `docker-persistence` EFS access point.

Everything else (image layers, `/opt/hermes-defaults/` seeds, the WebUI binary) is ephemeral and replaced on each task. First boot seeds EFS from `/opt/hermes-defaults/`; subsequent boots refresh `config.yaml` and re-run patches. User edits to `SOUL.md` are preserved; `config.yaml` is DOH-owned and regenerated on every boot. Details in `entrypoint.sh`.


## ECS compute

Hermes supports both DOH ECS compute modes:

- **Fargate:** the original serverless ECS path. The task runs in private subnets with an AWS-managed host lifecycle.
- **EC2 capacity:** the task runs on DOH-managed ECS container instances in the customer's environment. The service still uses `awsvpc`, private subnets, the same ALB target-group model, ECS task roles, and the same EFS access point.

The Hermes templates default to EC2 capacity so long-lived agents can use customer-owned compute, while the deploy form can still choose Fargate. Both modes keep Bedrock credentials on the ECS task role and keep Hermes state on EFS.


## Version pins

Both upstreams are pinned in the Dockerfile and bumped manually:

- **WebUI**: `FROM ghcr.io/nesquena/hermes-webui:X.Y.Z`. Container tag drops the leading `v` of the release tag.
- **Agent framework**: `git clone --branch vYYYY.M.D`. Uses CalVer git tags; ignore the parallel semver in release names.

On existing deployments, only the WebUI updates on redeploy — `hermes-agent/` is frozen on EFS at the version seeded on first boot. Updating it requires user-run `hermes update` or an EFS wipe. When bumping either pin, re-run `patches/apply.py` against a fresh checkout to confirm anchors still match.
