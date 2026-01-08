import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv


def load_credentials_from_env() -> None:
    """
    Load AWS credentials from .env file.

    Loads environment variables from the .env file in the project root
    and validates that required AWS credentials are present.

    Raises:
        FileNotFoundError: If .env file is not found
        ValueError: If required environment variables are missing
    """
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        raise FileNotFoundError(f".env file not found at {env_path}")
    
    load_dotenv(env_path)

    required_vars = ["DOH_AWS_ACCESS_KEY", "DOH_AWS_SECRET_KEY"]
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")


def get_assumed_role_session(
    access_key: str,
    secret_key: str,
    account_id: str,
    external_id: str,
    region: str,
) -> boto3.Session:
    """
    Assume the DevOpsHero role in the target account and return a boto3 session.

    Raises:
        ClientError: If role assumption fails
    """
    # Create STS client with control plane credentials
    sts_client = boto3.client(
        "sts",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )

    # The role ARN follows the pattern from cf_install_template.json
    role_arn = f"arn:aws:iam::{account_id}:role/devopshero-{external_id}"

    print(f"🔑 Assuming role: {role_arn}")

    response = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName="devopshero-deployment",
        ExternalId=external_id,
        DurationSeconds=3600,  # 1 hour
    )

    credentials = response["Credentials"]

    # Create a new session with the assumed role credentials
    session = boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )

    # Verify we're in the right account
    sts = session.client("sts")
    identity = sts.get_caller_identity()
    print(f"✅ Assumed role in account: {identity['Account']}")

    return session
