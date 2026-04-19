"""
Simulate the sidecar's decision pipeline against a local DOH instance.

Bypasses AWS entirely: generates an in-memory RSA keypair, mints a session JWT
the way the auth Lambda will, ensures a SidecarToken row for the app's env,
then calls the real POST /api/pdp/evaluate on the local server. Prints the
decision + reason.

Use this to sanity-check ABAC policy changes and the PDP endpoint without
deploying the sidecar or the auth Lambda.

Example:
    uv run manage.py runserver   # in another terminal
    uv run manage.py sidecar_simulate --app vmendi-hermes --user vmendi
"""

import hashlib
import json
import secrets
import time
from urllib.parse import urljoin

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from devopshero_app.models import App, DeploymentBlueprint, SidecarToken, User


class Command(BaseCommand):
    help = "Simulate a sidecar decision against the local PDP endpoint (no AWS required)."

    def add_arguments(self, parser):
        parser.add_argument("--app", required=True, help="App slug (app_id sent to the PDP).")
        parser.add_argument(
            "--user", required=True,
            help="User identifier: username, email, or oidc_sub. Must be unambiguous.",
        )
        parser.add_argument(
            "--path", default="/",
            help="Request path to evaluate (default '/'). Used for future route overrides.",
        )
        parser.add_argument(
            "--pdp-url", default="http://127.0.0.1:8000/api/pdp/evaluate",
            help="Full PDP endpoint URL (default %(default)s).",
        )
        parser.add_argument(
            "--env-slug", default=None,
            help="Environment slug to scope the call. Defaults to the only env with a blueprint for the app.",
        )

    def handle(self, *args, **opts):
        app_slug = opts["app"]
        user_arg = opts["user"]
        path = opts["path"]
        pdp_url = opts["pdp_url"]
        env_slug = opts["env_slug"]

        app = App.objects.filter(slug=app_slug).first()
        if app is None:
            raise CommandError(f"No App with slug {app_slug!r}.")

        user = User.objects.filter(
            Q(username=user_arg) | Q(email__iexact=user_arg) | Q(oidc_sub=user_arg),
        ).first()
        if user is None:
            raise CommandError(
                f"No User matching {user_arg!r} by username, email, or oidc_sub.",
            )
        if not user.oidc_sub:
            raise CommandError(
                f"User {user.username!r} has no oidc_sub — simulator needs an OIDC subject "
                f"to mint a realistic token. Set one manually for local testing, e.g. "
                f"User.objects.filter(pk={user.pk!r}).update(oidc_sub='okta|{user.username}')",
            )

        blueprint_qs = DeploymentBlueprint.objects.filter(app=app).select_related(
            "environment", "environment__aws_account__organization",
        )
        if env_slug:
            blueprint_qs = blueprint_qs.filter(environment__slug=env_slug)
        blueprint = blueprint_qs.first()
        if blueprint is None:
            raise CommandError(
                f"No DeploymentBlueprint for app {app_slug!r}"
                + (f" in env {env_slug!r}" if env_slug else "")
                + ". Deploy the app (or pass --env-slug) before simulating.",
            )

        environment = blueprint.environment
        organization = environment.aws_account.organization

        raw_token, token_row = _ensure_sidecar_token(environment=environment)

        jwt_value, public_key, kid = _mint_session_jwt(
            oidc_sub=user.oidc_sub,
            username=user.username,
            email=user.email or "",
        )

        # Optional: locally verify the JWT to catch key/alg mistakes before the PDP call.
        _self_check_jwt(jwt_value=jwt_value, public_key=public_key)

        self.stdout.write(self.style.MIGRATE_HEADING("Simulating sidecar decision"))
        self.stdout.write(f"  app       : {app.slug} (id {app.pk})")
        self.stdout.write(f"  env       : {environment.slug} (id {environment.pk})")
        self.stdout.write(f"  org       : {organization.slug}")
        self.stdout.write(f"  user      : {user.username} <{user.email}> sub={user.oidc_sub}")
        self.stdout.write(f"  path      : {path}")
        self.stdout.write(f"  jwt kid   : {kid}")
        self.stdout.write(f"  token row : {'reused' if token_row is not None else 'created'}")
        self.stdout.write(f"  pdp       : {pdp_url}")
        self.stdout.write("")

        try:
            response = httpx.post(
                pdp_url,
                headers={"Authorization": f"Bearer {raw_token}"},
                json={
                    "app_id": app.slug,
                    "oidc_sub": user.oidc_sub,
                    "username": user.username,
                    "path": path,
                },
                timeout=5.0,
            )
        except httpx.HTTPError as exc:
            raise CommandError(f"PDP call failed: {exc}")

        self.stdout.write(f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            self.stdout.write(response.text)
            raise CommandError("PDP returned a non-JSON body.")

        self.stdout.write(json.dumps(body, indent=2))

        if response.status_code != 200:
            return

        decision = body.get("decision")
        if decision == "allow":
            self.stdout.write(self.style.SUCCESS("\nALLOW"))
        elif decision == "deny":
            self.stdout.write(self.style.WARNING(f"\nDENY — {body.get('reason', '')}"))
        else:
            self.stdout.write(self.style.ERROR(f"\nunexpected decision: {decision!r}"))


def _ensure_sidecar_token(environment) -> tuple[str, SidecarToken | None]:
    """Return (raw_token, existing_row).

    If a SidecarToken row already exists for this env we can't recover the raw
    token (we only store the hash), so we create a fresh one and replace the
    row. existing_row is returned as-is when reused (always None currently — we
    always mint fresh for the simulator so subsequent runs are deterministic).
    """
    raw = secrets.token_urlsafe(48)  # ~64 chars of base64url
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    existing = SidecarToken.objects.filter(environment=environment).first()
    if existing is not None:
        existing.token_hash = token_hash
        existing.save(update_fields=["token_hash"])
        return raw, existing
    SidecarToken.objects.create(environment=environment, token_hash=token_hash)
    return raw, None


def _mint_session_jwt(
    oidc_sub: str, username: str, email: str,
) -> tuple[str, object, str]:
    """Generate an ephemeral RSA keypair and mint a JWT signed with it. Returns (jwt, public_key, kid)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    kid = "sidecar-simulate"
    now = int(time.time())
    token = jwt.encode(
        payload={
            "sub": oidc_sub,
            "username": username,
            "email": email,
            "iat": now,
            "exp": now + 600,
        },
        key=private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )
    return token, public_key, kid


def _self_check_jwt(jwt_value: str, public_key) -> None:
    """Decode the JWT locally so mistakes in minting surface here, not from the PDP."""
    try:
        jwt.decode(jwt_value, key=public_key, algorithms=["RS256"])
    except jwt.PyJWTError as exc:
        raise CommandError(f"Self-check of minted JWT failed: {exc}")
