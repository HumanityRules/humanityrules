"""
ACM utility functions for certificate operations.
"""

import logging

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)


def find_wildcard_certificate(session: boto3.Session, domain_name: str) -> str | None:
    """
    Find an existing wildcard certificate for a domain in ACM.

    Searches for certificates covering "*.{domain_name}" that are in ISSUED status.
    Checks both the primary domain name AND Subject Alternative Names (SANs).

    Args:
        session: Boto3 session with credentials.
        domain_name: The base domain (e.g., "dev.example.com").

    Returns:
        Certificate ARN if found, None otherwise.
    """
    acm_client = session.client("acm")
    wildcard_domain = f"*.{domain_name}"

    try:
        paginator = acm_client.get_paginator("list_certificates")
        for page in paginator.paginate(CertificateStatuses=["ISSUED"]):
            for cert in page["CertificateSummaryList"]:
                # Check primary domain name
                if cert["DomainName"] == wildcard_domain:
                    logger.info(
                        "Found existing wildcard certificate (primary domain) for '%(domain)s': %(arn)s",
                        {"domain": wildcard_domain, "arn": cert["CertificateArn"]},
                    )
                    return cert["CertificateArn"]
                # Check Subject Alternative Names (SANs)
                sans = cert.get("SubjectAlternativeNameSummaries", [])
                if wildcard_domain in sans:
                    logger.info(
                        "Found existing wildcard certificate (via SAN) for '%(domain)s': %(arn)s",
                        {"domain": wildcard_domain, "arn": cert["CertificateArn"]},
                    )
                    return cert["CertificateArn"]
    except ClientError as e:
        logger.error("Failed to list ACM certificates: %(error)s", {"error": e})

    logger.info("No existing wildcard certificate found for '%(domain)s'", {"domain": wildcard_domain})
    return None
