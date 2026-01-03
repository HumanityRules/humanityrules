import boto3
from botocore.exceptions import ClientError
import sys



def get_assumed_role_session(
    access_key: str,
    secret_key: str,
    account_id: str,
    external_id: str,
    region: str,
) -> boto3.Session:
    """
    Assume the DevOpsHero role in the target account and return a boto3 session.
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
    
    try:
        response = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName="devopshero-infra-deployment",
            ExternalId=external_id,
            DurationSeconds=3600,  # 1 hour
        )
    except ClientError as e:
        print(f"❌ Failed to assume role: {e}")
        sys.exit(1)
    
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
