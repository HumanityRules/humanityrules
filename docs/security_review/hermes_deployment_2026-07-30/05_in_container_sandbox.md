# Area 5: In-Container Sandbox Boundary

## Scope

Privilege split inside the Hermes container: root supervisor runs broker + `aws_signer` outside the sandbox; agent/WebUI run as `hermeswebui` under nono with a locked profile; IMDS disabled; AWS calls go through loopback SigV4 signer; HTTPS forced through broker proxy. Question: what can a compromised agent process reach?

**In scope (HumR-owned only):**

- `template_repos/hermes_agent/humr_runtime/supervisor.sh`
- `hermes-nono-profile.json`
- `aws_signer.py`, `sandbox_seed.py`
- `persistent-root-runner.sh`, `webui.sh`, `render_hermes_config.py`
- `http_router/Caddyfile`
- `process_supervisor/`
- Dockerfile / build: `template_repos/hermes_agent/Dockerfile`, `build/overlay-webui.sh`
- Trust-boundary sections in `docs/integrations/integrations_broker_design.md`; `skills/development/aws-cli/` as needed
- Broker CONNECT / tunnel path only as it affects sandbox→credential leakage (`tls_intercept.py` opaque tunnel)

**Out of scope:** CP ABAC, OAuth provider wiring, ECS account install, MCP connector catalog (except localhost ports the sandbox can reach). Upstream Hermes under `vendor/` is not reviewed. Area 2 / Area 7 already cover control-API lifecycle CSRF and Web App `WEBAPP_PORT` / public-grant routing; those are cross-referenced here only where they change the sandbox blast radius.

## Summary

The intended split is real and mostly well implemented: credential-holding daemons stay root outside nono; the agent drops to UID 1024; nono blocks direct IMDS/ECS-creds ports; `allow_vars` strips container-credential env; the signer binds loopback only and never puts real AWS keys in the sandbox env; the broker CA private material is process-memory / `0700` private dir.

The decisive failure against that model is **not** direct IMDS from the sandbox — it is the **broker’s unrestricted opaque CONNECT**, which runs outside nono and will happily tunnel the sandboxed agent to `169.254.169.254` / `169.254.170.2`. On Hermes’ EC2 launch type that yields task-role credentials inside the sandbox and collapses the signer/broker custody story. Secondary High issues: `connect_port: [443]` direct egress, an unfiltered SigV4 signer (including STS response bodies), and the large set of unauthenticated loopback services the profile deliberately opens.

## Findings

### [Critical] Broker opaque CONNECT SSRF reaches IMDS / ECS task credentials

- **Location:** `template_repos/hermes_agent/humr_runtime/integrations/tls_intercept.py` `_handle_connection` (lines 233–243) and `_tunnel_opaque` (lines 447–466); invoked for any CONNECT host not in `host_to_provider`. Hermes template uses EC2 + `SYS_ADMIN` (`seed_app_templates.py` `HERMES_PERSONAL_TEMPLATE`, lines 205–236). Direct IMDS hardening in `supervisor.sh` (lines 31–33) and nono `network.block` / no port 80 in `open_port`/`connect_port`.
- **Issue:** Unknown-host CONNECT is a raw `asyncio.open_connection(destination_host, destination_port)` with **no** deny list for link-local, metadata IPs, localhost-beyond-intended-services, or RFC1918. Port defaults to 443 but any port in the CONNECT target is accepted (`port = int(port_str) if port_str else 443`). The proxy process is root, outside nono, with full task network — so a sandboxed client that does `CONNECT 169.254.169.254:80 HTTP/1.1` (or `169.254.170.2:80`) gets a bidirectional tunnel to the instance metadata / ECS credentials endpoint. `AWS_EC2_METADATA_DISABLED=true` and nono’s block of direct `:80` only stop **direct** sandbox sockets; they do not constrain the broker. On EC2 ECS, classic IMDS commonly exposes the task role under `/latest/meta-data/iam/security-credentials/…` (IMDSv2 token PUT works over the same tunnel). Design text claiming the malicious skill “cannot bypass the broker to reach a provider directly” (`integrations_broker_design.md` ~224) does not contemplate this path, which uses the broker as an SSRF gadget against the host’s own metadata plane.
- **Impact:** Compromised / prompt-injected agent obtains **live ECS task-role credentials inside the sandbox**, defeating the entire “credentials never enter Hermes” invariant for AWS. Blast radius becomes whatever IAM the task role holds (Bedrock when capability-granted, Secrets Manager for app/env secrets ARNs, user-granted S3/DynamoDB/etc., plus any future grants). Also general VPC SSRF (internal ALBs, other link-local services) via the same tunnel.
- **Next:** Before `_tunnel_opaque` / any CONNECT upstream: reject link-local (`169.254.0.0/16`, `fe80::/10`), loopback (except explicit allow), metadata DNS names, and ideally private RFC1918/CGNAT unless product explicitly needs them. Prefer allowlisting destination ports to `443` (and maybe `80` only for non-metadata public hosts). Add a regression test that `CONNECT 169.254.169.254:80` returns 403/502 from the proxy.

### [High] `connect_port: [443]` allows direct HTTPS egress that bypasses the broker

- **Location:** `hermes-nono-profile.json` `network.block: true` with `connect_port: [443]` (lines 87–91) and `network_profile: null` (line 88). Contrast design: “no `allow_domain`… broker’s loopback port is what’s reachable” (`integrations_broker_design.md` ~224–232). Proxy path uses `open_port` `9950` (`supervisor.sh` lines 148–168).
- **Issue:** In nono, `connect_port` is the Linux allowlist for **direct outbound TCP** to that destination port on any IP (`--allow-connect-port`). Combined with `block: true`, this punches a hole for arbitrary `host:443` without going through `127.0.0.1:9950`. Legitimate proxied traffic only needs `open_port` → broker; the broker then dials upstream. Direct `:443` therefore bypasses TLS intercept, credential injection, CONNECT logging, and usage metering. Clients that ignore `HTTPS_PROXY` (or an agent that unsets it — those vars are injected via `/usr/bin/env`, not locked by nono) can use this hole immediately.
- **Impact:** Unmetered / unaudited egress; ability to talk to real provider endpoints without MITM (no injected secrets, but any secret that *does* enter the sandbox can be used directly); weaker enforcement of “all HTTPS through broker.”
- **Next:** Remove `connect_port: [443]` unless a specific non-proxy client is documented and unavoidable. If brew/git/node need exceptions, prefer fixing proxy/CA env (already partially done) or a narrow `allow_domain` via nono proxy mode — not a global port 443 allow.

### [High] AWS signer is an unfiltered SigV4 pass-through (including credential-bearing STS APIs)

- **Location:** `aws_signer.py` `Handler._proxy` (lines 86–172) and `main` port table (lines 207–225); child config in `sandbox_seed.py` `write_child_aws_config` (lines 55–108); skill list in `skills/development/aws-cli/SKILL.md` (lines 19–27). No path/action allowlist anywhere in the signer.
- **Issue:** Every HTTP method on each service port is re-signed with the task role and streamed back verbatim. There is no restriction to `GetCallerIdentity`, Bedrock invoke, or read-only APIs. **STS is especially dangerous:** if the task role (or a later user grant) allows `sts:AssumeRole`, `GetSessionToken`, `AssumeRoleWithWebIdentity`, etc., the **response body contains temporary credentials** that land inside the sandbox — another custody break, independent of IMDS. Even without STS minting, the signer exposes the full IAM surface of Bedrock / S3 / S3 Tables / DynamoDB / Cost Explorer that the role holds.
- **Impact:** Sandbox compromise ⇒ abuse of all signer-fronted IAM; possible extraction of new temporary keys via STS if those actions are ever granted. Secrets Manager is not on the signer port list (strength), but anything reachable via these seven ports is fully available.
- **Next:** Application-layer allowlist per port (e.g. STS: only `GetCallerIdentity` / needed read APIs; Bedrock: invoke + list; deny `AssumeRole*` / `GetSessionToken`). Optionally strip or refuse responses whose bodies match STS credential XML/JSON shapes. Keep IAM least-privilege as the outer bound, but do not treat IAM alone as sufficient for the signer.

### [High] Nono deliberately exposes unauthenticated privileged loopback services to the agent

- **Location:** `hermes-nono-profile.json` `open_port` (lines 102–120): `9901–9907` (signer), `9950–9952` (broker proxy/control/MCP), `9956–9957` (process-compose), `8642` / `8787` / `8789`. `webui.sh` exports fixed `API_SERVER_KEY=humr-loopback-gateway-key` (lines 24–32). Broker control API has no request auth (`control_api.py`; design: “Sandbox → broker: no authentication,” `integrations_broker_design.md` ~38). Process-compose REST restart used by broker (`credentials_service.py` `_post_process_compose_restart` ~394–396) is similarly open on `127.0.0.1:9956`.
- **Issue:** A compromised agent process can, without leaving nono: drive the full AWS signer; call broker lifecycle routes on `:9951` (disconnect, device-flow start, permissions relay, refresh — see Area 2); speak MCP on `:9952`; restart/replace supervision via process-compose; call the Hermes gateway OpenAI API with the well-known loopback bearer. This matches the product trust model (“sandbox peers trust each other”) but is a large blast radius once the agent or any same-uid Web App/Widget is compromised.
- **Impact:** Full use of connected integrations and task-role AWS (via signer); lifecycle sabotage; agent API abuse / credit burn; supervisor YAML under `/workspace/.config/process-compose/` is agent-writable so commands for `system.webui` / `system.gateway` can be rewritten then restarted (still inside nono — not a root escape, but durable hijack of platform processes).
- **Next:** Product judgment on how much “agent owns the box” is acceptable. Defense-in-depth options: split lifecycle (`9951`) from tool MCP (`9952`) with a capability token; authenticate process-compose; mint a per-boot `API_SERVER_KEY`; integrity-check system process-compose entries against image-owned seeds on interval.

### [Medium] Agent-writable Caddy fragments + `--watch` can publish root loopback services on the agent hostname

- **Location:** `http_router/Caddyfile` imports `/workspace/.config/caddy/webapps.caddy` and `widgets.caddy` before broker/WebUI handlers (lines 8–25); `webui.sh` `caddy run … --watch` (line 133); workspace is nono readwrite. Overlaps Area 7 (`WEBAPP_PORT` escape and hand-edited fragments).
- **Issue:** From the sandbox-boundary view, the agent can rewrite fragments to `reverse_proxy 127.0.0.1:9905` (S3 signer), `:9951` (broker control), `:9956` (process-compose), etc., on the bare or webapp host matched by `X-Forwarded-Host`. Policy proxy still requires auth for personal-assistant traffic, but any principal who can use the app then reaches **root-owned** daemons that were never meant to be browser-facing except via the narrow `/__humr_broker/*` path.
- **Impact:** Authenticated confused-deputy access to signer/broker/supervisor APIs; with a public Web App grant (Area 7), anonymous internet exposure of those same ports.
- **Next:** Same as Area 7: validate route targets to `4000–4019` only; consider regenerating fragments from non-writable desired state; disable free-form `handle` in imported fragments if Caddy can constrain adapters.

### [Medium] `filesystem.allow` grants `/run` readwrite; private broker dir relies only on Unix mode

- **Location:** `hermes-nono-profile.json` `filesystem.allow` includes `/run` (lines 44–52); `filesystem.read` adds `/run/humr/integrations-broker/ca` (lines 54–57). Design claims private dir is “deliberately not granted” (`integrations_broker_design.md` ~231). `tls_certificate_authority.py` `chmod 0o700` on private dir and `0o600` on leaf PEMs (lines 96–97, 171–173); supervisor `chmod 700` on private (`supervisor.sh` lines 119–120).
- **Issue:** Nono `allow` is full readwrite for the path tree; the narrower `read` grant does not subtract from `/run`. Isolation of CA/leaf private keys therefore depends entirely on root ownership + `0700`/`0600`. That holds today for UID 1024, but leaf PEMs are briefly written to disk under a Landlock-visible tree, and any future mis-chmod or group-readable slip becomes sandbox-readable. Broader `/run` also lets the agent write other runtime sockets/files if Unix DAC allows.
- **Impact:** Defense-in-depth gap around the broker crypto material; increases severity of any file-mode bug.
- **Next:** Remove `/run` from `allow`; keep only `filesystem.read` for the CA directory (and any other specific `/run/...` paths actually required). Confirm with `nono` learn/trace that nothing else under `/run` is needed.

### [Medium] Persistent-root checkpoint preserves agent-mutated system state across restarts

- **Location:** `persistent-root-runner.sh` checkpoint includes essentially the whole rootfs except `/opt/humr`, `/opt/hermes`, and ephemeral mounts (lines 221–236); `IMAGE_OWNED_DIRS` re-sync only those two trees (lines 10–13, 95–110). Agent-writable: `/workspace`, `/home/linuxbrew`, process-compose YAML, Caddy fragments, and any hermeswebui-writable paths under the persistent root.
- **Issue:** Malicious installs (`brew`, pip into workspace venv), rewritten supervisor config, and planted login hooks in user-owned trees survive task replacement via EFS checkpoint / host bind mount. Image-owned HumR runtime is refreshed (good), but the durable root is an attacker persistence plane once the sandbox is compromised.
- **Impact:** Sticky backdoors inside the assistant’s durable filesystem; harder incident recovery than “redeploy clears it.”
- **Next:** Document redeploy ≠ wipe; provide an operator “reset persistent root” path; optionally exclude `/home/linuxbrew` from checkpoint or re-bootstrap brew from image.

### [Low] `HERMES_WEBUI_PASSWORD` is allowlisted into the sandbox

- **Location:** `hermes-nono-profile.json` `allow_vars` (line 33).
- **Issue:** If the deployment sets a WebUI password env var, every sandboxed process can read it. Loopback WebUI binding (`HERMES_WEBUI_HOST=127.0.0.1`, `supervisor.sh` lines 20–21) limits remote use, but same-uid peers and agent tools see the secret.
- **Impact:** Low on personal-assistant (owner already controls the app); higher if password is reused elsewhere or WebUI auth is mistaken for a strong boundary.
- **Next:** Prefer not injecting password into agent tool env; keep it only on the WebUI process if still required.

### [Low] Overlay / image build does not alter the runtime privilege split

- **Location:** `build/overlay-webui.sh`; Dockerfile `ENTRYPOINT` `persistent-root-runner.sh` → `supervisor.sh` → `webui.sh` (lines 342–343).
- **Issue:** No additional finding — noted to record that the overlay only replaces WebUI source under `/apptoo` before install. Runtime boundary is entirely in `humr_runtime/` + nono profile + capabilities on the task (`SYS_ADMIN` for persistent-root mounts, then dropped via `setpriv` in the runner, lines 312–315).
- **Impact:** N/A.
- **Next:** Keep capability drop immediately after chroot; never re-raise `CAP_SYS_ADMIN` inside the supervised tree.

## Sound design notes

1. **Clear custody split.** `aws_signer` and `humr_broker` start as root before `runuser`/`nono` (`supervisor.sh` Stage 1/2, lines 215–227). Comments correctly identify `/proc/<pid>/environ` + dropped `CAP_SYS_PTRACE` as the reason real secrets stay out of the agent UID.
2. **Layered IMDS denial for *direct* access.** `AWS_EC2_METADATA_DISABLED=true`, credential URI env vars omitted from `allow_vars`, and nono `block` without port 80 in connect/open lists. (Insufficient alone — see Critical CONNECT finding.)
3. **Signer never puts real keys in the sandbox env.** Dummy keys in `sandbox_seed.py`; signing happens only in the parent process; hop-by-hop auth headers from the child are stripped before re-sign (`aws_signer.py` `_STRIP_BEFORE_SIGN`, lines 47–52).
4. **Secrets Manager not fronted by the signer.** Task role may `GetSecretValue` for ECS injection, but there is no `990x` port for secretsmanager — the agent cannot call SM through the SigV4 proxy.
5. **Broker CA private key handling.** Boot-generated, memory-resident CA; leaf PEMs `0600` under `0700` private dir and unlinked after load (`tls_certificate_authority.py`); sandbox only needs `bundle.pem`.
6. **WebUI / Caddy bind posture.** WebUI pinned to `127.0.0.1:8789`; Caddy `admin off`; policy proxy is the ENI-facing gate. Persistent-root runner drops `sys_admin` and all inheritable/ambient caps after chroot so the long-lived supervisor is not an ongoing mount oracle.
7. **HTTPS_PROXY + multi-client CA env.** Supervisor sets `SSL_CERT_FILE`, `GIT_SSL_CAINFO`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, and `HOMEBREW_GIT_PATH` so common TLS clients actually use the MITM path (`supervisor.sh` lines 147–168).

## Open questions / needs product judgment

1. **Is unrestricted opaque CONNECT a hard product requirement** (arbitrary `git clone`, package downloads), and if so can it still deny metadata/link-local while allowing the public internet?
2. **Was `connect_port: [443]` added for a specific broken client?** If yes, name it and replace with a narrower fix; if no, delete it.
3. **How much AWS should a compromised agent retain by design?** Signer-as-full-IAM-proxy vs. tight action allowlists — especially STS.
4. **Is “agent may rewrite Caddy / process-compose” accepted** under the personal-assistant threat model, or should platform routing/supervision be integrity-protected against the agent UID?
5. **Incident response:** after a suspected sandbox compromise, is wiping `/var/lib/humr/hermes-roots/{slug}` (+ checkpoint) a documented operator step? Persistent-root makes “redeploy the task” insufficient.
