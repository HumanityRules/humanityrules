"""
Utilities for managing application secrets in AWS Secrets Manager.

Usage:
    # From infra_customer directory:
    uv run python -m devopshero_app.services.infra_customer.secrets_utils --list
    uv run python -m devopshero_app.services.infra_customer.secrets_utils --purge-deleted
    uv run python -m devopshero_app.services.infra_customer.secrets_utils --purge-deleted --dry-run
"""

import argparse
import json
import os
import secrets

import boto3
from botocore.exceptions import ClientError

from .appconfig import AppConfig


def _generate_secret_value(key: str, value: str | None) -> str:
    """Generate a secret value: use literal if provided, otherwise random 64-char token."""
    if value is None:
        return secrets.token_urlsafe(48)[:64]
    return value


def ensure_app_secrets_exist(session: boto3.Session, app_config: AppConfig) -> None:
    """
    Ensure all app secrets exist in Secrets Manager. Creates or merges as needed.
    
    This is called BEFORE CDK runs because CloudFormation's GenerateSecretString
    can only auto-generate ONE random field per secret. By using boto3, we can
    generate multiple random fields (e.g., secret_key_base AND signing_salt).
    
    The app_config.app_secrets dict maps field names to values:
    - str value: use this literal value
    - None: generate a random 64-char alphanumeric string
    
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
            existing_values[key] = _generate_secret_value(key=key, value=app_config.app_secrets[key])
        
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
        secret_values[key] = _generate_secret_value(key=key, value=value)
    
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


def main() -> None:
    """CLI entry point for secrets management."""
    from pathlib import Path
    
    from dotenv import load_dotenv
    
    from . import iam_utils
    
    parser = argparse.ArgumentParser(description="Manage secrets in customer AWS account")
    
    # Actions (mutually exclusive)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list",
        action="store_true",
        help="List all secrets in Secrets Manager",
    )
    group.add_argument(
        "--purge-deleted",
        action="store_true",
        help="Permanently delete all secrets scheduled for deletion",
    )
    
    # Options
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    parser.add_argument(
        "--include-deleted",
        action="store_true",
        help="Include secrets scheduled for deletion in --list output",
    )
    parser.add_argument(
        "--account",
        default="266117665083",
        help="AWS account name or ID (default: 266117665083)",
    )
    
    args = parser.parse_args()
    
    # Load credentials from project root .env
    project_root = Path(__file__).parent.parent.parent.parent
    load_dotenv(project_root / ".env")
    
    # Get target account from database
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "devopshero_site.settings")
    import django
    django.setup()
    
    from devopshero_app.models import AWSAccount
    
    connected_accounts = list(AWSAccount.objects.filter(status="connected"))
    
    if not connected_accounts:
        print("❌ No connected AWS accounts found in database")
        return
    
    # Find by name or AWS account ID
    aws_account = next(
        (a for a in connected_accounts 
         if a.name == args.account or a.aws_account_id == args.account),
        None
    )
    if not aws_account:
        print(f"❌ No connected AWS account found matching '{args.account}'")
        print("\nAvailable accounts:")
        for acc in connected_accounts:
            print(f"  - {acc.name} ({acc.aws_account_id})")
        return
    
    print(f"Using AWS account: {aws_account.name} ({aws_account.aws_account_id})")
    print()
    
    session = iam_utils.get_assumed_role_session(
        access_key=os.getenv("DOH_AWS_ACCESS_KEY"),
        secret_key=os.getenv("DOH_AWS_SECRET_KEY"),
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region="us-east-1",
    )
    print()
    
    # Execute action
    if args.list:
        secrets_list = list_secrets(
            session=session,
            include_deleted=args.include_deleted,
        )
        
        if not secrets_list:
            print("No secrets found.")
            return
        
        print(f"=== Secrets ({len(secrets_list)}) ===")
        print()
        for secret in secrets_list:
            status = "🗑️ " if secret["deleted_date"] else "📌"
            print(f"{status} {secret['name']}")
            if secret["description"]:
                print(f"   Description: {secret['description']}")
            print(f"   ARN: {secret['arn']}")
            if secret["deleted_date"]:
                print(f"   Scheduled deletion: {secret['deleted_date']}")
            print()
    
    elif args.purge_deleted:
        purge_deleted_secrets(session=session, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

