"""Generate the central RS256 keypair used to sign env-SSO session JWTs.

Run once when bootstrapping (or rotating) the central env-session signing key.
The control plane mints session JWTs with the private key; per-env policy-proxy
sidecars verify them via /.well-known/jwks.json.

Usage:
    uv run manage.py generate_env_session_jwt_keypair

Output is two values to paste into the operator's .env, then sync to Secrets
Manager via infra_humanityrules/sync_secrets.py:

    HUMR_ENV_SESSION_JWT_PRIVATE_KEY=<PEM, newline-escaped>
    HUMR_ENV_SESSION_JWT_KID=env-sso-YYYY-MM-DD

The PEM is printed with literal \\n separators so it round-trips through .env
files (settings.py un-escapes \\n -> newline at load time, same pattern as
GITHUB_APP_PRIVATE_KEY).
"""

import datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.management.base import BaseCommand


class Command(BaseCommand):

    help = "Generate the central env-SSO RS256 signing keypair (one-shot)."

    def handle(self, *args, **options) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        kid = f"env-sso-{datetime.date.today().isoformat()}"
        escaped_pem = private_pem.decode("ascii").replace("\n", "\\n")

        self.stdout.write("# Generated env-SSO signing keypair.")
        self.stdout.write("# Paste these two lines into .env, then run infra_humanityrules/sync_secrets.py.")
        self.stdout.write("")
        self.stdout.write(f"HUMR_ENV_SESSION_JWT_PRIVATE_KEY={escaped_pem}")
        self.stdout.write(f"HUMR_ENV_SESSION_JWT_KID={kid}")
        self.stdout.write("")
        self.stdout.write("# Public key (informational only — published by /.well-known/jwks.json):")
        for line in public_pem.decode("ascii").splitlines():
            self.stdout.write(f"# {line}")
