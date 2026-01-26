#!/usr/bin/env python3
"""
Sync secrets from local .env to AWS Secrets Manager.

Usage:
    python sync_secrets.py          # Sync all secrets
    python sync_secrets.py --dry-run # Show what would be created/updated
"""

import argparse
import json
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import dotenv_values

# Secret definitions: maps AWS secret name to the env vars it should contain
SECRET_DEFINITIONS = {
    "devopshero/prod/django": [
        "DJANGO_SECRET_KEY",
        "DJANGO_SUPERUSER_EMAIL",
    ],
    "devopshero/prod/workos": [
        "WORKOS_CLIENT_ID",
        "WORKOS_API_KEY",
    ],
    "devopshero/prod/github": [
        "GITHUB_APP_ID",
        "GITHUB_APP_CLIENT_ID",
        "GITHUB_APP_CLIENT_SECRET",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_WEBHOOK_SECRET",
    ],
    "devopshero/prod/bedrock": [
        "AWS_BEDROCK_REGION",
        "AWS_BEDROCK_ACCESS_KEY_ID",
        "AWS_BEDROCK_SECRET_ACCESS_KEY",
    ],
    "devopshero/prod/api": [
        "DOH_API_SECRET_KEY",
    ],
}


def load_env_values(env_path: Path) -> dict[str, str]:
    """Load values from .env file."""
    if not env_path.exists():
        print(f"Error: .env file not found at {env_path}")
        sys.exit(1)
    
    return dotenv_values(env_path)


def get_secrets_client(env_values: dict[str, str]) -> boto3.client:
    """Create Secrets Manager client with credentials from env."""
    return boto3.client(
        "secretsmanager",
        region_name="us-east-1",
        aws_access_key_id=env_values.get("DOH_AWS_ACCESS_KEY"),
        aws_secret_access_key=env_values.get("DOH_AWS_SECRET_KEY"),
    )


def secret_exists(client: boto3.client, secret_name: str) -> bool:
    """Check if a secret exists in Secrets Manager."""
    try:
        client.describe_secret(SecretId=secret_name)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return False
        raise


def create_or_update_secret(client: boto3.client, secret_name: str, secret_value: dict, dry_run: bool) -> str:
    """Create or update a secret. Returns 'created', 'updated', or 'unchanged'."""
    secret_string = json.dumps(secret_value)
    
    if secret_exists(client, secret_name):
        # Check if value changed
        try:
            current = client.get_secret_value(SecretId=secret_name)
            if current.get("SecretString") == secret_string:
                return "unchanged"
        except ClientError:
            pass
        
        if dry_run:
            return "would_update"
        
        client.update_secret(SecretId=secret_name, SecretString=secret_string)
        return "updated"
    else:
        if dry_run:
            return "would_create"
        
        client.create_secret(
            Name=secret_name,
            SecretString=secret_string,
            Description=f"DevOps Hero production secret: {secret_name}",
        )
        return "created"


def sync_secrets(dry_run: bool) -> None:
    """Sync all secrets from .env to AWS Secrets Manager."""
    env_path = Path(__file__).parent.parent / ".env"
    env_values = load_env_values(env_path)
    
    client = get_secrets_client(env_values)
    
    print(f"\n{'DRY RUN - ' if dry_run else ''}Syncing secrets to AWS Secrets Manager\n")
    print(f"{'Secret Name':<40} {'Status':<15} {'Keys'}")
    print("-" * 80)
    
    for secret_name, env_keys in SECRET_DEFINITIONS.items():
        # Build secret value from env keys
        secret_value = {}
        missing_keys = []
        
        for key in env_keys:
            value = env_values.get(key)
            if value:
                secret_value[key] = value
            else:
                missing_keys.append(key)
        
        if missing_keys:
            print(f"{secret_name:<40} {'SKIPPED':<15} Missing: {', '.join(missing_keys)}")
            continue
        
        if not secret_value:
            print(f"{secret_name:<40} {'SKIPPED':<15} No values found")
            continue
        
        try:
            status = create_or_update_secret(client, secret_name, secret_value, dry_run)
            status_display = {
                "created": "CREATED",
                "updated": "UPDATED",
                "unchanged": "UNCHANGED",
                "would_create": "WOULD CREATE",
                "would_update": "WOULD UPDATE",
            }.get(status, status.upper())
            
            print(f"{secret_name:<40} {status_display:<15} {', '.join(env_keys)}")
        except ClientError as e:
            print(f"{secret_name:<40} {'ERROR':<15} {e.response['Error']['Message']}")
    
    print()
    if dry_run:
        print("Dry run complete. Run without --dry-run to apply changes.")
    else:
        print("Secrets sync complete.")


def main():
    parser = argparse.ArgumentParser(description="Sync secrets from .env to AWS Secrets Manager")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    args = parser.parse_args()
    
    sync_secrets(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
