"""
Utilities for managing application secrets in AWS Secrets Manager.
"""

import json
import secrets

import boto3
from botocore.exceptions import ClientError

from appconfig import AppConfig


def ensure_app_secrets_exist(session: boto3.Session, app_config: AppConfig) -> None:
    """
    Ensure app secrets exist in Secrets Manager. Creates if not exists.
    
    This is called BEFORE CDK runs because CloudFormation's GenerateSecretString
    can only auto-generate ONE random field per secret. By using boto3, we can
    generate multiple random fields (e.g., secret_key_base AND signing_salt).
    
    The app_config.app_secrets dict maps field names to values:
    - str value: use this literal value
    - None: generate a random 64-char alphanumeric string
    """
    if not app_config.app_secrets:
        return
    
    secret_name = f"doh/{app_config.app_name}/secrets"
    sm_client = session.client("secretsmanager")
    
    # Check if secret already exists
    try:
        sm_client.describe_secret(SecretId=secret_name)
        print(f"   ✅ Secret '{secret_name}' already exists")
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    
    # Generate values for None fields
    secret_values = {}
    for key, value in app_config.app_secrets.items():
        if value is None:
            # Generate a random 64-char alphanumeric string
            secret_values[key] = secrets.token_urlsafe(48)[:64]
        else:
            secret_values[key] = value
    
    # Create the secret
    print(f"   ⏳ Creating secret '{secret_name}'...")
    sm_client.create_secret(
        Name=secret_name,
        Description=f"Application secrets for {app_config.app_name}",
        SecretString=json.dumps(secret_values),
    )
    print(f"   ✅ Secret '{secret_name}' created")

