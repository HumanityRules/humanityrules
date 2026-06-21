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


def get_shared_secrets(session: boto3.Session, env_slug: str) -> dict[str, str]:
    """Read the environment's shared secrets from Secrets Manager. Returns {} if none exist."""
    secret_name = f"humr/{env_slug}/shared-secrets"
    sm_client = session.client("secretsmanager")
    try:
        response = sm_client.get_secret_value(SecretId=secret_name)
        return json.loads(response["SecretString"])
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return {}
        raise


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

    secret_name = f"humr/{env_slug}/{app_config.app_name}/secrets"
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
# Environment bearer / auth Lambda secrets (per-environment, see docs/policy_proxy_design.md)
# ---------------------------------------------------------------------------


SHARED_SECRETS_KEY_HUMR_ENV_BEARER = "HUMR_ENV_BEARER"


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

    Returns the ARN of the shared-secrets entry. *env* is a Django Environment
    instance — passed in rather than imported so this module stays free of
    Django model imports at top level.
    """
    # Local import so test harnesses that don't have Django set up can still
    # exercise the AWS-side helpers in isolation.
    from humanityrules_app.models import EnvironmentBearerToken

    env_slug = env.slug
    secret_name = f"humr/{env_slug}/shared-secrets"
    sm_client = session.client("secretsmanager")

    existing_row = EnvironmentBearerToken.objects.filter(environment=env).first()
    existing_secret = get_shared_secrets(session=session, env_slug=env_slug) if _secret_exists(sm_client, secret_name) else None
    has_token_in_secret = bool(existing_secret and existing_secret.get(SHARED_SECRETS_KEY_HUMR_ENV_BEARER))

    # Happy path: both sides already present → trust them, no-op.
    if existing_row is not None and has_token_in_secret:
        return _get_secret_arn(sm_client, secret_name)

    # Otherwise: generate a fresh raw token and write both sides atomically.
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
