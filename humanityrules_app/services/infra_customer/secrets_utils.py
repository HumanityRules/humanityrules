"""
Utilities for managing application secrets in AWS Secrets Manager.

For listing secrets or purging secrets scheduled for deletion in a customer account,
use the Django management command: ``uv run manage.py humr_secrets``.
"""

import hashlib
import json
import logging
import secrets

import boto3
from botocore.exceptions import ClientError

from .appconfig import AppConfig

logger = logging.getLogger(__name__)


def _generate_secret_value(key: str, value: str | None) -> str:
    """Generate a secret value: use literal if provided, otherwise random 64-char token."""
    if value is None:
        return secrets.token_urlsafe(48)[:64]
    return value


def _resolve_secret_value(key: str, value: str | None, shared_secrets: dict[str, str]) -> str:
    """Resolve a secret value using shared secrets as fallback for empty placeholders."""
    if value is None:
        return secrets.token_urlsafe(48)[:64]
    if value == "" and shared_secrets.get(key):
        return shared_secrets[key]
    return value


def env_shared_secrets_namespace(env) -> str:
    """Path namespace for an env's shared-secrets bag — per-org for HumR-sandbox envs, else the env slug.

    Every org's sandbox env shares one AWS account and the fixed slug "sandbox", so keying the secret
    by slug alone collides every org on one bag (and one env bearer → wrong-org resolution). Dedicated
    customer accounts already have a unique slug, so they keep it. *env* is a Django Environment.
    """
    aws_account = env.aws_account
    if aws_account.is_humr_sandbox:
        return f"sandbox/{aws_account.organization.slug}"
    return env.slug


def shared_secrets_secret_name(env) -> str:
    """Secrets Manager name for an env's shared-secrets bag (holds HUMR_ENV_BEARER + shared keys)."""
    return f"humr/{env_shared_secrets_namespace(env)}/shared-secrets"


def get_shared_secrets(session: boto3.Session, env) -> dict[str, str]:
    """Read the environment's shared secrets from Secrets Manager. Returns {} if none exist."""
    secret_name = shared_secrets_secret_name(env)
    sm_client = session.client("secretsmanager")
    try:
        response = sm_client.get_secret_value(SecretId=secret_name)
        return json.loads(response["SecretString"])
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return {}
        raise


def app_secrets_secret_name(env_slug: str, app_slug: str) -> str:
    """Secrets Manager name for one app's own bag (template secrets + HUMR_APP_BEARER)."""
    return f"humr/{env_slug}/{app_slug}/secrets"


def app_secrets_secret_arn(session: boto3.Session, env_slug: str, app_slug: str) -> str:
    """Complete ARN of an app's bag. The bag must already exist (see ensure_app_secrets_exist)."""
    sm_client = session.client("secretsmanager")
    return _get_secret_arn(sm_client, app_secrets_secret_name(env_slug=env_slug, app_slug=app_slug))


def ensure_app_secrets_exist(session: boto3.Session, env_slug: str, app_config: AppConfig, shared_secrets: dict[str, str]) -> None:
    """
    Ensure all app secrets exist in Secrets Manager. Creates or merges as needed.

    This is called BEFORE CDK runs because CloudFormation's GenerateSecretString
    can only auto-generate ONE random field per secret. By using boto3, we can
    generate multiple random fields (e.g., secret_key_base AND signing_salt).

    The app_config.app_secrets dict maps field names to values:
    - str value: use this literal value
    - None: generate a random 64-char alphanumeric string

    Empty-placeholder values ("") are resolved from shared_secrets when available.

    If the secret already exists, any new keys from app_config.app_secrets are
    merged in without overwriting existing keys. Additionally, any shared-
    placeholder keys ("") whose stored value is still empty will be filled in
    from shared_secrets on subsequent runs.
    """
    if not app_config.app_secrets:
        return

    secret_name = app_secrets_secret_name(env_slug=env_slug, app_slug=app_config.app_name)
    sm_client = session.client("secretsmanager")
    
    # Check if secret already exists
    try:
        response = sm_client.get_secret_value(SecretId=secret_name)
        existing_values = json.loads(response["SecretString"])

        missing_keys = set(app_config.app_secrets.keys()) - set(existing_values.keys())

        # Heal shared-placeholder keys whose stored value is still "" because
        # the shared secret wasn't populated at first-deploy. Without this,
        # later shared-set calls wouldn't propagate to the app secret and the
        # container would keep starting with empty credentials.
        healed_keys = {
            key for key, value in app_config.app_secrets.items()
            if value == "" and existing_values.get(key) == "" and shared_secrets.get(key)
        }

        if not missing_keys and not healed_keys:
            print(f"   ✅ Secret '{secret_name}' already exists with all required keys")
            return

        for key in missing_keys | healed_keys:
            existing_values[key] = _resolve_secret_value(key=key, value=app_config.app_secrets[key], shared_secrets=shared_secrets)

        parts = []
        if missing_keys:
            parts.append(f"adding {len(missing_keys)} missing key(s): {', '.join(sorted(missing_keys))}")
        if healed_keys:
            parts.append(f"filling {len(healed_keys)} empty key(s) from shared secrets: {', '.join(sorted(healed_keys))}")
        print(f"   ⏳ Updating '{secret_name}': {'; '.join(parts)}")
        sm_client.put_secret_value(SecretId=secret_name, SecretString=json.dumps(existing_values))
        print(f"   ✅ Secret '{secret_name}' updated")
        return
        
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    
    # Secret doesn't exist — create it with all fields
    secret_values = {}
    for key, value in app_config.app_secrets.items():
        secret_values[key] = _resolve_secret_value(key=key, value=value, shared_secrets=shared_secrets)
    
    shared_keys_used = [k for k, v in app_config.app_secrets.items() if v == "" and shared_secrets.get(k)]
    if shared_keys_used:
        print(f"   🔗 Resolved {len(shared_keys_used)} key(s) from shared secrets: {', '.join(sorted(shared_keys_used))}")
    
    print(f"   ⏳ Creating secret '{secret_name}'...")
    sm_client.create_secret(
        Name=secret_name,
        Description=f"Application secrets for {app_config.app_name} in env '{env_slug}'",
        SecretString=json.dumps(secret_values),
    )
    print(f"   ✅ Secret '{secret_name}' created")


def list_secrets(session: boto3.Session, include_deleted: bool) -> list[dict]:
    """List all secrets in Secrets Manager. Returns list of secret metadata dicts."""
    sm_client = session.client("secretsmanager")
    paginator = sm_client.get_paginator("list_secrets")
    
    secrets_list = []
    for page in paginator.paginate(IncludePlannedDeletion=include_deleted):
        for secret in page["SecretList"]:
            secrets_list.append({
                "name": secret["Name"],
                "arn": secret["ARN"],
                "description": secret.get("Description", ""),
                "created_date": secret.get("CreatedDate"),
                "deleted_date": secret.get("DeletedDate"),
            })
    
    return secrets_list


# ---------------------------------------------------------------------------
# Control-plane bearer tokens (see docs/policy_proxy_design.md)
#
# Two tokens live here while the per-app bearer lands: the retired per-environment
# HUMR_ENV_BEARER in the shared-secrets bag, and the per-app HUMR_APP_BEARER in
# each app's own bag. Both follow the same custody rule — the raw value only ever
# exists in the customer account, the control plane keeps just its SHA-256 hash.
# ---------------------------------------------------------------------------


SHARED_SECRETS_KEY_HUMR_ENV_BEARER = "HUMR_ENV_BEARER"

# Control-plane-owned key inside the per-app bag humr/{env}/{app}/secrets.
# Reserved: a template may not declare it (see app_config_builder._union_app_secrets).
APP_SECRETS_KEY_HUMR_APP_BEARER = "HUMR_APP_BEARER"


def _secret_exists(sm_client, secret_name: str) -> bool:
    try:
        sm_client.describe_secret(SecretId=secret_name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def _get_secret_arn(sm_client, secret_name: str) -> str:
    return sm_client.describe_secret(SecretId=secret_name)["ARN"]


def _get_secret_json_or_none(sm_client, secret_name: str) -> dict[str, str] | None:
    """Return the secret's decoded JSON, or None when the entry does not exist."""
    try:
        response = sm_client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise
    return json.loads(response["SecretString"])


def _create_or_merge_secret(
    sm_client,
    secret_name: str,
    description: str,
    values_to_write: dict[str, str],
    merge_mode: bool,
) -> str:
    """Create a Secrets Manager entry with JSON *values_to_write* or merge them in.

    When *merge_mode* is True and the secret already exists, existing keys are
    preserved and only missing keys from *values_to_write* are added. When
    False, the secret is replaced in full on update. Returns the ARN.
    """
    try:
        response = sm_client.get_secret_value(SecretId=secret_name)
        existing = json.loads(response["SecretString"])
        if merge_mode:
            missing = {k: v for k, v in values_to_write.items() if k not in existing}
            if not missing:
                return response["ARN"]
            merged = {**existing, **missing}
            sm_client.put_secret_value(SecretId=secret_name, SecretString=json.dumps(merged))
            logger.info("secret '%s' merged: added %s", secret_name, sorted(missing.keys()))
            return response["ARN"]
        sm_client.put_secret_value(SecretId=secret_name, SecretString=json.dumps(values_to_write))
        logger.info("secret '%s' replaced", secret_name)
        return response["ARN"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    response = sm_client.create_secret(
        Name=secret_name,
        Description=description,
        SecretString=json.dumps(values_to_write),
    )
    logger.info("secret '%s' created", secret_name)
    return response["ARN"]


def ensure_env_bearer_token_exists(session: boto3.Session, env) -> str:
    """Ensure HUMR_ENV_BEARER exists both in shared-secrets and as an EnvironmentBearerToken row.

    The shared-secrets entry is the source of truth for the raw token: whenever it
    already holds a HUMR_ENV_BEARER, the DB row is made to match its hash (adopted when
    missing, re-synced when drifted) and the secret is never rewritten. A fresh token is
    minted only when the secret has none. This keeps every control-plane DB that deploys
    into the (account-shared) env consistent with the one live secret — e.g. a local-DB
    deploy against a freshly recreated env adopts the existing token instead of clobbering
    it and stranding every other DB (including prod) on a now-stale hash.

    Returns the ARN of the shared-secrets entry. *env* is a Django Environment
    instance — passed in rather than imported so this module stays free of
    Django model imports at top level.
    """
    # Local import so test harnesses that don't have Django set up can still
    # exercise the AWS-side helpers in isolation.
    from humanityrules_app.models import EnvironmentBearerToken

    env_slug = env.slug
    secret_name = shared_secrets_secret_name(env)
    sm_client = session.client("secretsmanager")

    existing_row = EnvironmentBearerToken.objects.filter(environment=env).first()
    existing_secret = get_shared_secrets(session=session, env=env) if _secret_exists(sm_client, secret_name) else None
    secret_raw_token = existing_secret.get(SHARED_SECRETS_KEY_HUMR_ENV_BEARER) if existing_secret else None

    # Secret already carries a token → it wins. Make the DB row match its hash and
    # leave the secret untouched: adopt when the row is missing, re-sync when it has
    # drifted (the drift case self-heals a DB whose hash fell out of step with the
    # live secret — the desync that wedges deploys).
    if secret_raw_token:
        secret_token_hash = hashlib.sha256(secret_raw_token.encode("utf-8")).hexdigest()
        if existing_row is None:
            EnvironmentBearerToken.objects.create(environment=env, token_hash=secret_token_hash)
            logger.info("env bearer token row adopted from existing secret for env '%s'", env_slug)
        elif existing_row.token_hash != secret_token_hash:
            existing_row.token_hash = secret_token_hash
            existing_row.save(update_fields=["token_hash"])
            logger.info("env bearer token row re-synced to secret (healed drift) for env '%s'", env_slug)
        return _get_secret_arn(sm_client, secret_name)

    # Secret has no token (true first provisioning, or it lost the key): mint a
    # fresh raw token and write both sides together.
    raw = secrets.token_urlsafe(48)[:64]
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    values = dict(existing_secret or {})
    values[SHARED_SECRETS_KEY_HUMR_ENV_BEARER] = raw
    arn = _create_or_merge_secret(
        sm_client=sm_client,
        secret_name=secret_name,
        description=f"Shared secrets for env '{env_slug}' (per-env keys merged across apps)",
        values_to_write=values,
        merge_mode=False,  # we already merged in-memory; replace authoritatively
    )

    if existing_row is None:
        EnvironmentBearerToken.objects.create(environment=env, token_hash=token_hash)
        logger.info("env bearer token row created for env '%s'", env_slug)
    else:
        existing_row.token_hash = token_hash
        existing_row.save(update_fields=["token_hash"])
        logger.info("env bearer token row rotated for env '%s'", env_slug)

    return arn


# ---------------------------------------------------------------------------
# Per-app bearer (humr/{env}/{app}/secrets, key HUMR_APP_BEARER)
# ---------------------------------------------------------------------------


def ensure_app_bearer_token_exists(session: boto3.Session, env_slug: str, app) -> str:
    """Ensure the app's HUMR_APP_BEARER exists in its per-app bag and as an AppBearerToken row.

    "DB wins", i.e. the last deployment wins. The control plane only ever accepts
    the hash it has stored, so whenever the two sides disagree — or either is
    missing — we mint a fresh raw token and write both together. The only no-op
    case is a row whose hash already matches the value in the bag.

    Unlike the retired per-env bearer there is no adopt/re-sync branch: an app
    only ever talks to the control plane that last deployed it (the control-plane
    URL is baked into its task definition), so a stale row in some other control
    plane's DB is harmless.

    The bag humr/{env_slug}/{app_slug}/secrets is created on demand — most
    templates declare no secrets at all, so the bearer is usually its first key.
    Runs after ensure_app_secrets_exist so the read-modify-write of the single
    HUMR_APP_BEARER key preserves whatever template secrets landed there.

    Returns the complete ARN of the per-app bag. *app* is a Django App instance —
    passed in rather than imported so this module stays free of Django model
    imports at top level.
    """
    # Local import so test harnesses that don't have Django set up can still
    # exercise the AWS-side helpers in isolation.
    from humanityrules_app.models import AppBearerToken

    secret_name = app_secrets_secret_name(env_slug=env_slug, app_slug=app.slug)
    sm_client = session.client("secretsmanager")

    existing_row = AppBearerToken.objects.filter(app=app).first()
    existing_secret = _get_secret_json_or_none(sm_client=sm_client, secret_name=secret_name)
    secret_raw_token = existing_secret.get(APP_SECRETS_KEY_HUMR_APP_BEARER) if existing_secret else None
    secret_token_hash = hashlib.sha256(secret_raw_token.encode("utf-8")).hexdigest() if secret_raw_token else None

    if existing_row is not None and existing_row.token_hash == secret_token_hash:
        logger.info("app bearer token already consistent for app '%s' in env '%s'", app.slug, env_slug)
        return _get_secret_arn(sm_client, secret_name)

    raw = secrets.token_urlsafe(48)[:64]
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    values = dict(existing_secret or {})
    values[APP_SECRETS_KEY_HUMR_APP_BEARER] = raw
    arn = _create_or_merge_secret(
        sm_client=sm_client,
        secret_name=secret_name,
        description=f"Application secrets for {app.slug} in env '{env_slug}'",
        values_to_write=values,
        merge_mode=False,  # we already merged in-memory; replace authoritatively
    )

    AppBearerToken.objects.update_or_create(app=app, defaults={"token_hash": token_hash})
    logger.info("app bearer token minted for app '%s' in env '%s'", app.slug, env_slug)
    return arn


def ensure_env_policy_proxy_secrets_exist(session: boto3.Session, env) -> dict[str, str]:
    """Top-level helper: provision per-env policy-proxy secrets and return their ARNs.

    Returns a dict with keys: 'shared_secrets_arn'. Called from the deploy
    pipeline before the policy-proxy stack runs.
    """
    return {
        "shared_secrets_arn": ensure_env_bearer_token_exists(session=session, env=env),
    }


def delete_secrets_matching_prefix(session: boto3.Session, subprefix: str, dry_run: bool, force_immediate: bool) -> int:
    """Delete secrets whose names start with *subprefix* (AWS name-prefix filter).

    When *force_immediate* is False, each secret is scheduled for deletion with a
    7-day recovery window. When True, uses ForceDeleteWithoutRecovery (same as purge-deleted).
    """
    sm_client = session.client("secretsmanager")
    paginator = sm_client.get_paginator("list_secrets")

    secrets_list: list[dict[str, str]] = []
    for page in paginator.paginate(
        Filters=[{"Key": "name", "Values": [subprefix]}],
        IncludePlannedDeletion=False,
    ):
        for secret in page["SecretList"]:
            secrets_list.append({"name": secret["Name"], "arn": secret["ARN"]})

    if not secrets_list:
        print(f"No active secrets found with name prefix '{subprefix}'.")
        return 0

    plan = "permanent removal" if force_immediate else "scheduled deletion (7-day recovery window)"
    print(f"Found {len(secrets_list)} secret(s) matching prefix '{subprefix}' — {plan}:")
    for secret in secrets_list:
        print(f"  🗑️  {secret['name']}")
    print()

    if dry_run:
        print("Dry run — no secrets were deleted.")
        return len(secrets_list)

    deleted_count = 0
    for secret in secrets_list:
        try:
            if force_immediate:
                sm_client.delete_secret(SecretId=secret["arn"], ForceDeleteWithoutRecovery=True)
                print(f"✅ Permanently deleted: {secret['name']}")
            else:
                sm_client.delete_secret(SecretId=secret["arn"], RecoveryWindowInDays=7)
                print(f"✅ Scheduled for deletion: {secret['name']}")
            deleted_count += 1
        except ClientError as e:
            print(f"❌ Failed to delete {secret['name']}: {e}")

    return deleted_count


def purge_deleted_secrets(session: boto3.Session, dry_run: bool) -> int:
    """Permanently delete all secrets that are scheduled for deletion."""
    sm_client = session.client("secretsmanager")
    
    # Get secrets scheduled for deletion
    all_secrets = list_secrets(session=session, include_deleted=True)
    deleted_secrets = [s for s in all_secrets if s["deleted_date"] is not None]
    
    if not deleted_secrets:
        print("No secrets scheduled for deletion.")
        return 0
    
    print(f"Found {len(deleted_secrets)} secret(s) scheduled for deletion:")
    for secret in deleted_secrets:
        print(f"  🗑️  {secret['name']}")
        print(f"      Scheduled: {secret['deleted_date']}")
    print()
    
    if dry_run:
        print("Dry run - no secrets were deleted.")
        return len(deleted_secrets)
    
    # Force delete each secret
    deleted_count = 0
    for secret in deleted_secrets:
        try:
            sm_client.delete_secret(
                SecretId=secret["arn"],
                ForceDeleteWithoutRecovery=True,
            )
            print(f"✅ Permanently deleted: {secret['name']}")
            deleted_count += 1
        except ClientError as e:
            print(f"❌ Failed to delete {secret['name']}: {e}")
    
    return deleted_count
