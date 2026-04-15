"""
Utilities for managing application secrets in AWS Secrets Manager.

For listing secrets or purging secrets scheduled for deletion in a customer account,
use the Django management command: ``uv run manage.py doh_secrets``.
"""

import json
import secrets

import boto3
from botocore.exceptions import ClientError

from .appconfig import AppConfig


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
    secret_name = f"devopshero/{env_slug}/shared-secrets"
    sm_client = session.client("secretsmanager")
    try:
        response = sm_client.get_secret_value(SecretId=secret_name)
        return json.loads(response["SecretString"])
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return {}
        raise


def ensure_app_secrets_exist(session: boto3.Session, app_config: AppConfig, shared_secrets: dict[str, str]) -> None:
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
    merged in without overwriting existing keys.
    """
    if not app_config.app_secrets:
        return
    
    secret_name = f"devopshero/{app_config.app_name}/secrets"
    sm_client = session.client("secretsmanager")
    
    # Check if secret already exists
    try:
        response = sm_client.get_secret_value(SecretId=secret_name)
        existing_values = json.loads(response["SecretString"])
        
        # Find keys that are in app_config but missing from the stored secret
        missing_keys = set(app_config.app_secrets.keys()) - set(existing_values.keys())
        if not missing_keys:
            print(f"   ✅ Secret '{secret_name}' already exists with all required keys")
            return
        
        # Merge new keys into existing secret, preserving existing values
        for key in missing_keys:
            existing_values[key] = _resolve_secret_value(key=key, value=app_config.app_secrets[key], shared_secrets=shared_secrets)
        
        print(f"   ⏳ Adding {len(missing_keys)} new key(s) to '{secret_name}': {', '.join(sorted(missing_keys))}")
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
        Description=f"Application secrets for {app_config.app_name}",
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
