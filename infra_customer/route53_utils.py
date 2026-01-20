"""
Route53 utility functions for DNS operations.
"""

import boto3
from botocore.exceptions import ClientError


def get_hosted_zone_id(session: boto3.Session, hosted_zone_name: str) -> str | None:
    """
    Look up the Route53 hosted zone ID by name.

    Args:
        session: Boto3 session with credentials
        hosted_zone_name: The domain name (e.g., "chsandbox.com")

    Returns:
        The hosted zone ID if found, None otherwise.
    """
    route53_client = session.client("route53")

    # Ensure the name ends with a dot (Route53 convention)
    if not hosted_zone_name.endswith("."):
        hosted_zone_name = hosted_zone_name + "."

    try:
        response = route53_client.list_hosted_zones_by_name(
            DNSName=hosted_zone_name,
            MaxItems="1",
        )

        for zone in response.get("HostedZones", []):
            if zone["Name"] == hosted_zone_name:
                # Zone ID comes as "/hostedzone/XXXXX", extract just the ID
                zone_id = zone["Id"].replace("/hostedzone/", "")
                return zone_id

    except ClientError as e:
        print(f"   ❌ Failed to look up hosted zone: {e}")

    return None


def list_hosted_zones(session: boto3.Session) -> list[dict]:
    """
    List all public hosted zones in the account.

    Args:
        session: Boto3 session with credentials

    Returns:
        List of dicts with id, name, and record_count for each public hosted zone.
    """
    route53_client = session.client("route53")
    zones = []

    try:
        paginator = route53_client.get_paginator("list_hosted_zones")
        for page in paginator.paginate():
            for zone in page["HostedZones"]:
                # Skip private hosted zones
                if zone.get("Config", {}).get("PrivateZone", False):
                    continue
                zones.append({
                    "id": zone["Id"].replace("/hostedzone/", ""),
                    "name": zone["Name"].rstrip("."),
                    "record_count": zone["ResourceRecordSetCount"],
                })
    except ClientError as e:
        print(f"   ❌ Failed to list hosted zones: {e}")

    return zones

