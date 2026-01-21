"""
ECS utility functions for monitoring and managing ECS services.
"""

import logging
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def check_stopped_tasks(
    ecs_client,
    cluster: str,
    service: str,
    deployment_start_time: datetime,
) -> list[str]:
    """
    Check if tasks are failing and return the reasons.
    
    Only returns failures for tasks that:
    - Were started AFTER deployment_start_time (ignores old failed tasks)
    - Stopped unexpectedly (crashed, failed to start, etc.)
    
    Tasks stopped due to normal deployment/scaling activities are ignored.
    
    Returns a list of failure reasons (empty if no failures).
    """
    # Stop codes that indicate intentional stops (not failures)
    INTENTIONAL_STOP_CODES = {
        "ServiceSchedulerInitiated",  # Normal deployment/scaling rotation
        "UserInitiated",              # User manually stopped the task
        "SpotInterruption",           # Spot instance interrupted (not app's fault)
    }
    
    reasons = []
    
    try:
        # Get recently stopped tasks
        stopped = ecs_client.list_tasks(
            cluster=cluster,
            serviceName=service,
            desiredStatus="STOPPED",
        )
        
        if not stopped["taskArns"]:
            return reasons
        
        # Get details on why they stopped (check up to 5 recent tasks)
        details = ecs_client.describe_tasks(
            cluster=cluster,
            tasks=stopped["taskArns"][:5],
        )
        
        for task in details["tasks"]:
            # Skip tasks that started before our deployment (old failures)
            task_started_at = task.get("startedAt")
            if task_started_at and task_started_at < deployment_start_time:
                continue
            
            stop_code = task.get("stopCode", "")
            
            # Skip tasks that were intentionally stopped (not failures)
            if stop_code in INTENTIONAL_STOP_CODES:
                continue
            
            reason = task.get("stoppedReason", "Unknown")
            reasons.append(reason)
            
            # Check container-level failures
            for container in task.get("containers", []):
                if container.get("reason"):
                    reasons.append(f"Container '{container['name']}': {container['reason']}")
        
    except ClientError:
        pass
    
    return reasons


def wait_for_service_stable(
    ecs_client,
    cluster: str,
    service: str,
    timeout_seconds: int,
    deployment_start_time: datetime,
) -> bool:
    """
    Wait for ECS service to stabilize, with diagnostics on failure.
    
    Requires multiple consecutive stable checks to confirm the service isn't
    just briefly running before crashing.
    
    Args:
        deployment_start_time: Only check for failures in tasks started after this time.
                              This prevents old failed tasks from triggering false alarms.
    
    Returns True if service is stable, False if timed out or tasks failing.
    """
    logger.info("Waiting for service to stabilize (timeout: %(timeout_seconds)s)...", {"timeout_seconds": timeout_seconds})
    
    STABLE_CHECKS_REQUIRED = 3  # Need 3 consecutive stable checks (30 seconds)
    
    start = time.time()
    last_running = -1
    consecutive_failures = 0
    consecutive_stable = 0
    
    while time.time() - start < timeout_seconds:
        try:
            response = ecs_client.describe_services(cluster=cluster, services=[service])
            svc = response["services"][0]
            
            running = svc["runningCount"]
            desired = svc["desiredCount"]
            pending = svc["pendingCount"]
            
            if running != last_running:
                logger.info(
                    "   Tasks: %(running)s/%(desired)s running, %(pending)s pending",
                    {"running": running, "desired": desired, "pending": pending},
                )
                last_running = running
                consecutive_stable = 0  # Reset stability counter on change
            
            if running == desired and desired > 0 and pending == 0:
                consecutive_stable += 1
                if consecutive_stable >= STABLE_CHECKS_REQUIRED:
                    logger.info("   Service stable")
                    return True
                elif consecutive_stable == 1:
                    logger.info(
                        "   Confirming stability (%(checks_remaining)s more checks)",
                        {"checks_remaining": STABLE_CHECKS_REQUIRED - consecutive_stable},
                    )
            else:
                consecutive_stable = 0
            
            # Check for task failures (only tasks started after deployment)
            failure_reasons = check_stopped_tasks(
                ecs_client=ecs_client,
                cluster=cluster,
                service=service,
                deployment_start_time=deployment_start_time,
            )
            if failure_reasons:
                consecutive_failures += 1
                consecutive_stable = 0  # Reset stability on failures
                if consecutive_failures >= 2:  # Show failures after 2 checks
                    logger.error("   Tasks are failing:")
                    for reason in failure_reasons[:3]:  # Show up to 3 reasons
                        logger.error("      %(reason)s", {"reason": reason})
                    
                    # If we've seen failures for 3+ consecutive checks, give up early
                    if consecutive_failures >= 4:
                        logger.error("   Too many task failures, aborting")
                        return False
            else:
                consecutive_failures = 0
            
        except ClientError as e:
            logger.error("   Error checking service: %(error)s", {"error": str(e)})
        
        time.sleep(10)
    
    logger.error("   Timed out after %(timeout_seconds)s waiting for service", {"timeout_seconds": timeout_seconds})
    return False


def start_ecs_service(session: boto3.Session, service_name: str, cluster_name: str) -> bool:
    """Start the ECS service (set desiredCount to 1) and wait for stabilization."""
    logger.info(
        "Starting ECS service (cluster=%(cluster_name)s, service=%(service_name)s, desiredCount=1)",
        {"cluster_name": cluster_name, "service_name": service_name},
    )
    ecs_client = session.client("ecs")

    # Record deployment start time to filter out old failed tasks
    deployment_start_time = datetime.now(timezone.utc)

    try:
        ecs_client.update_service(cluster=cluster_name, service=service_name, desiredCount=1, forceNewDeployment=True)
        logger.info("   Deployment triggered (desiredCount=1)")
    except ClientError as e:
        logger.error("   Failed to trigger deployment: %(error)s", {"error": str(e)})
        return False

    stable = wait_for_service_stable(
        ecs_client=ecs_client,
        cluster=cluster_name,
        service=service_name,
        timeout_seconds=180,
        deployment_start_time=deployment_start_time,
    )

    if not stable:
        logger.error("Service failed to stabilize. Check ECS console for details.")
        return False

    return True
