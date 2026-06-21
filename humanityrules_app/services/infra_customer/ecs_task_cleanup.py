#!/usr/bin/env python3
"""
Clean up unused ECS task definition revisions.

ECS task definitions accumulate revisions over time (each deployment creates a new one).
AWS doesn't automatically clean them up, but you can DEREGISTER inactive revisions.

This script:
1. Lists all task definition families matching a prefix
2. For each family, identifies which revisions are in use (by services or running tasks)
3. Deregisters unused revisions, optionally keeping the latest N for safety

Usage:
    cd infra_customer
    
    # Dry run (default) - show what would be deleted
    uv run python ecs_task_cleanup.py
        
    # Preview what the above command would delete (dry run)
    uv run python ecs_task_cleanup.py --prefix "" --keep 0
    
    # Conservative mode: keep 3 most recent revisions per family (default)
    uv run python ecs_task_cleanup.py --execute
    
    # Only clean up families matching a prefix
    uv run python ecs_task_cleanup.py --prefix "humr-" --execute

    # Delete ALL unused task definitions (no prefix filter, no retention)
    # This deregisters every revision not actively used by a service or running task.
    uv run python ecs_task_cleanup.py --prefix "" --keep 0 --execute

Note: Deregistered task definitions become INACTIVE. AWS eventually garbage-collects
them, but they may remain visible in INACTIVE state for a while.
"""

import argparse
import os
import sys
from pathlib import Path
from collections import defaultdict

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

from . import iam_utils


# Default configuration (same as deploy_app.py)
TARGET_ACCOUNT_ID = "266117665083"
TARGET_EXTERNAL_ID = "9e62c988-09dd-4f96-b5a7-a67646dd285b"
TARGET_REGION = "us-east-1"


def load_env():
    """Load environment variables from .env file."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        print(f"❌ .env file not found at {env_path}")
        sys.exit(1)
    load_dotenv(env_path)
    
    required_vars = ["HUMR_AWS_ACCESS_KEY", "HUMR_AWS_SECRET_KEY"]
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        sys.exit(1)


def list_task_definition_families(
    ecs_client,
    prefix: str,
) -> list[str]:
    """
    List all task definition families matching the given prefix.
    
    Returns a list of family names (e.g., ["humr-simple-dashboard", ...])
    """
    families = []
    paginator = ecs_client.get_paginator("list_task_definition_families")
    
    for page in paginator.paginate(familyPrefix=prefix, status="ACTIVE"):
        families.extend(page.get("families", []))
    
    return families


def list_task_definition_revisions(
    ecs_client,
    family: str,
) -> list[str]:
    """
    List all ACTIVE task definition revisions for a family.
    
    Returns a list of full ARNs sorted by revision number (newest first).
    """
    revisions = []
    paginator = ecs_client.get_paginator("list_task_definitions")
    
    for page in paginator.paginate(familyPrefix=family, status="ACTIVE", sort="DESC"):
        revisions.extend(page.get("taskDefinitionArns", []))
    
    return revisions


def get_task_definitions_in_use(
    ecs_client,
    cluster_name: str,
) -> set[str]:
    """
    Get all task definition ARNs currently in use.
    
    A task definition is "in use" if:
    1. It's referenced by an active ECS service
    2. It's referenced by a running task
    
    Returns a set of task definition ARNs.
    """
    in_use = set()
    
    # Get task definitions used by services
    try:
        paginator = ecs_client.get_paginator("list_services")
        for page in paginator.paginate(cluster=cluster_name):
            service_arns = page.get("serviceArns", [])
            if service_arns:
                # Describe services to get their task definitions
                # API allows max 10 services per call
                for i in range(0, len(service_arns), 10):
                    batch = service_arns[i:i + 10]
                    response = ecs_client.describe_services(
                        cluster=cluster_name,
                        services=batch,
                    )
                    for service in response.get("services", []):
                        task_def = service.get("taskDefinition")
                        if task_def:
                            in_use.add(task_def)
    except ClientError as e:
        print(f"   ⚠️  Warning: Could not list services: {e}")
    
    # Get task definitions used by running tasks
    try:
        paginator = ecs_client.get_paginator("list_tasks")
        for page in paginator.paginate(cluster=cluster_name, desiredStatus="RUNNING"):
            task_arns = page.get("taskArns", [])
            if task_arns:
                # Describe tasks to get their task definitions
                # API allows max 100 tasks per call
                for i in range(0, len(task_arns), 100):
                    batch = task_arns[i:i + 100]
                    response = ecs_client.describe_tasks(
                        cluster=cluster_name,
                        tasks=batch,
                    )
                    for task in response.get("tasks", []):
                        task_def = task.get("taskDefinitionArn")
                        if task_def:
                            in_use.add(task_def)
    except ClientError as e:
        print(f"   ⚠️  Warning: Could not list tasks: {e}")
    
    return in_use


def deregister_task_definition(
    ecs_client,
    task_definition_arn: str,
    dry_run: bool,
) -> bool:
    """
    Deregister a task definition revision.
    
    Returns True if successful (or dry run), False on error.
    """
    if dry_run:
        return True
    
    try:
        ecs_client.deregister_task_definition(taskDefinition=task_definition_arn)
        return True
    except ClientError as e:
        print(f"      ❌ Failed to deregister {task_definition_arn}: {e}")
        return False


def cleanup_task_definitions(
    ecs_client,
    prefix: str,
    cluster_name: str,
    keep_count: int,
    dry_run: bool,
) -> dict:
    """
    Clean up unused task definition revisions.
    
    Args:
        ecs_client: Boto3 ECS client
        prefix: Only consider families starting with this prefix
        cluster_name: ECS cluster to check for in-use task definitions
        keep_count: Keep at least this many revisions per family (newest first)
        dry_run: If True, don't actually deregister anything
    
    Returns a dict with cleanup statistics.
    """
    stats = {
        "families_processed": 0,
        "revisions_found": 0,
        "revisions_in_use": 0,
        "revisions_kept": 0,
        "revisions_deregistered": 0,
        "errors": 0,
    }
    
    mode = "DRY RUN" if dry_run else "EXECUTING"
    print(f"\n{'='*60}")
    print(f"🧹 ECS Task Definition Cleanup ({mode})")
    print(f"{'='*60}")
    print(f"   Prefix filter: {prefix}")
    print(f"   Cluster: {cluster_name}")
    print(f"   Keep latest: {keep_count} revisions per family")
    
    # Step 1: Get all task definitions currently in use
    print(f"\n📋 Finding task definitions in use...")
    in_use = get_task_definitions_in_use(ecs_client=ecs_client, cluster_name=cluster_name)
    print(f"   Found {len(in_use)} task definitions in use")
    
    # Step 2: List all families
    print(f"\n📋 Listing task definition families (prefix: {prefix})...")
    families = list_task_definition_families(ecs_client=ecs_client, prefix=prefix)
    print(f"   Found {len(families)} families")
    
    if not families:
        print("   Nothing to clean up!")
        return stats
    
    # Step 3: Process each family
    print(f"\n🔍 Processing families...")
    for family in sorted(families):
        stats["families_processed"] += 1
        
        revisions = list_task_definition_revisions(ecs_client=ecs_client, family=family)
        stats["revisions_found"] += len(revisions)
        
        if not revisions:
            continue
        
        print(f"\n   📦 {family} ({len(revisions)} revisions)")
        
        # Determine which to keep vs deregister
        to_keep = []
        to_deregister = []
        
        for i, revision_arn in enumerate(revisions):
            # Extract revision number for display
            revision_num = revision_arn.split(":")[-1]
            
            if revision_arn in in_use:
                to_keep.append(revision_arn)
                stats["revisions_in_use"] += 1
                print(f"      ✅ :{revision_num} - IN USE (by service or task)")
            elif i < keep_count:
                to_keep.append(revision_arn)
                stats["revisions_kept"] += 1
                print(f"      🔒 :{revision_num} - KEPT (within keep_count={keep_count})")
            else:
                to_deregister.append(revision_arn)
                print(f"      🗑️  :{revision_num} - WILL DEREGISTER")
        
        # Deregister unused revisions
        for revision_arn in to_deregister:
            success = deregister_task_definition(
                ecs_client=ecs_client,
                task_definition_arn=revision_arn,
                dry_run=dry_run,
            )
            if success:
                stats["revisions_deregistered"] += 1
            else:
                stats["errors"] += 1
    
    return stats


def print_summary(stats: dict, dry_run: bool):
    """Print cleanup summary."""
    print(f"\n{'='*60}")
    print(f"📊 Summary")
    print(f"{'='*60}")
    print(f"   Families processed:      {stats['families_processed']}")
    print(f"   Total revisions found:   {stats['revisions_found']}")
    print(f"   Revisions in use:        {stats['revisions_in_use']}")
    print(f"   Revisions kept (safety): {stats['revisions_kept']}")
    
    if dry_run:
        print(f"   Would deregister:        {stats['revisions_deregistered']}")
        print(f"\n   ℹ️  This was a dry run. Use --execute to actually deregister.")
    else:
        print(f"   Revisions deregistered:  {stats['revisions_deregistered']}")
        if stats["errors"]:
            print(f"   Errors:                  {stats['errors']}")


def main():
    parser = argparse.ArgumentParser(
        description="Clean up unused ECS task definition revisions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually deregister task definitions (default is dry-run)",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=3,
        help="Keep at least N most recent revisions per family (default: 3)",
    )
    parser.add_argument(
        "--prefix",
        default="humr-",
        help="Only process families starting with this prefix (default: humr-)",
    )
    parser.add_argument(
        "--cluster",
        default="humr-cluster",
        help="ECS cluster name to check for in-use task definitions (default: humr-cluster)",
    )
    args = parser.parse_args()
    
    dry_run = not args.execute
    
    # Load environment and assume role
    load_env()
    
    session = iam_utils.get_assumed_role_session(
        access_key=os.getenv("HUMR_AWS_ACCESS_KEY"),
        secret_key=os.getenv("HUMR_AWS_SECRET_KEY"),
        account_id=TARGET_ACCOUNT_ID,
        external_id=TARGET_EXTERNAL_ID,
        region=TARGET_REGION,
    )
    
    ecs_client = session.client("ecs")
    
    # Run cleanup
    stats = cleanup_task_definitions(
        ecs_client=ecs_client,
        prefix=args.prefix,
        cluster_name=args.cluster,
        keep_count=args.keep,
        dry_run=dry_run,
    )
    
    print_summary(stats=stats, dry_run=dry_run)


if __name__ == "__main__":
    main()

