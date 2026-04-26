"""
Mint a doh_session JWT by reading the env's private key directly from Secrets Manager.

Used by the policy-proxy end-to-end test (see policy_proxy_e2e_test) and for
manual browser testing: paste the printed token into a doh_session cookie on
the deployed app's parent domain and the policy proxy will accept it (within
TTL) without going through Okta.

Security note: this bypasses the auth Lambda. Only ever run against an environment
you control during testing. In production the auth Lambda is the sole code path
that mints these cookies.

Usage:
    uv run manage.py policy_proxy_mint_cookie \\
        --aws-account "CH Sandbox" --env-slug policy-proxy-e2e \\
        --username vmendi@gmail.com --sub 00u1238lznaj1cjpm698 \\
        --email vmendi@example.com
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from devopshero_app.models import AWSAccount, Environment
from devopshero_app.services.infra_customer import iam_utils


class Command(BaseCommand):
    help = "Mint a doh_session JWT signed with the env's private key. Prints the token."

    def add_arguments(self, parser):
        parser.add_argument("--aws-account", required=True, help="AWS account name (e.g., 'CH Sandbox').")
        parser.add_argument("--env-slug", required=True, help="Environment slug.")
        parser.add_argument("--username", required=True, help="JWT 'username' claim — matches the ABAC $resource.owner.")
        parser.add_argument("--sub", required=True, help="JWT 'sub' claim — Okta subject identifier.")
        parser.add_argument("--email", required=True, help="JWT 'email' claim.")
        parser.add_argument("--ttl", type=int, default=3600, help="Cookie TTL in seconds (default 3600).")
        parser.add_argument(
            "--tamper", action="store_true",
            help="Return the JWT with a flipped last-char of signature (useful for deny-path tests).",
        )

    def handle(self, *args, **options):
        token = mint_session_jwt(
            aws_account_name=options["aws_account"],
            env_slug=options["env_slug"],
            username=options["username"],
            oidc_sub=options["sub"],
            email=options["email"],
            ttl_seconds=options["ttl"],
            tamper=options["tamper"],
        )
        self.stdout.write(token)


def mint_session_jwt(
    aws_account_name: str,
    env_slug: str,
    username: str,
    oidc_sub: str,
    email: str,
    ttl_seconds: int,
    tamper: bool,
) -> str:
    """Fetch the env's signing key from Secrets Manager and return a signed JWT."""
    # Lazy imports so this module can be loaded without the dependencies for --help.
    import time

    import jwt

    try:
        aws_account = AWSAccount.objects.get(name=aws_account_name)
    except AWSAccount.DoesNotExist:
        raise CommandError(f"AWS account {aws_account_name!r} not found.")

    try:
        env = Environment.objects.get(aws_account=aws_account, slug=env_slug)
    except Environment.DoesNotExist:
        raise CommandError(
            f"Environment {env_slug!r} not found in account {aws_account_name!r}.",
        )

    # iam_utils prints "🔑 Assuming role" banners to stdout; this command's
    # stdout is supposed to be the JWT (so callers can pipe it). Silence the
    # banners by redirecting stdout during role assumption only.
    import contextlib
    import io
    import sys
    with contextlib.redirect_stdout(sys.stderr):
        session = iam_utils.get_assumed_role_session(
            access_key=settings.DOH_AWS_ACCESS_KEY,
            secret_key=settings.DOH_AWS_SECRET_KEY,
            account_id=aws_account.aws_account_id,
            external_id=str(aws_account.external_id),
            region=env.aws_region,
        )

    secret_name = f"devopshero/{env_slug}/policy-proxy-auth-config"
    sm = session.client("secretsmanager")
    try:
        response = sm.get_secret_value(SecretId=secret_name)
    except sm.exceptions.ResourceNotFoundException as exc:
        raise CommandError(
            f"Secret {secret_name!r} not found. Has a policy-proxy app been deployed to this env?",
        ) from exc

    payload = json.loads(response["SecretString"])
    jwt_key = payload["jwt_key"]
    private_pem = jwt_key["private_pem"].encode("utf-8")
    kid = jwt_key["kid"]

    now = int(time.time())
    token = jwt.encode(
        payload={
            "sub": oidc_sub,
            "username": username,
            "email": email,
            "iat": now,
            "exp": now + ttl_seconds,
        },
        key=private_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )
    if tamper:
        head, mid, sig = token.rsplit(".", 2)
        flipped = sig[:-1] + ("A" if sig[-1] != "A" else "B")
        token = f"{head}.{mid}.{flipped}"
    return token
