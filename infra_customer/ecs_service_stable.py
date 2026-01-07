"""
ECS utility functions for monitoring and managing ECS services.
"""

import time

from botocore.exceptions import ClientError


def check_stopped_tasks(ecs_client, cluster: str, service: str) -> list[str]:
    """
    Check if tasks are failing and return the reasons.
    
    Only returns failures for tasks that stopped unexpectedly (crashed, failed to start, etc.).
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
        
        # Get details on why they stopped (check up to 3 recent tasks)
        details = ecs_client.describe_tasks(
            cluster=cluster,
            tasks=stopped["taskArns"][:3],
        )
        
        for task in details["tasks"]:
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
) -> bool:
    """
    Wait for ECS service to stabilize, with diagnostics on failure.
    
    Requires multiple consecutive stable checks to confirm the service isn't
    just briefly running before crashing.
    
    Returns True if service is stable, False if timed out or tasks failing.
    """
    print(f"\nWaiting for service to stabilize (timeout: {timeout_seconds}s)...")
    
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
                print(f"   Tasks: {running}/{desired} running, {pending} pending")
                last_running = running
                consecutive_stable = 0  # Reset stability counter on change
            
            if running == desired and desired > 0 and pending == 0:
                consecutive_stable += 1
                if consecutive_stable >= STABLE_CHECKS_REQUIRED:
                    print("   ✅ Service stable!")
                    return True
                elif consecutive_stable == 1:
                    print(f"   ⏳ Confirming stability ({STABLE_CHECKS_REQUIRED - consecutive_stable} more checks)...")
            else:
                consecutive_stable = 0
            
            # Check for task failures
            failure_reasons = check_stopped_tasks(ecs_client, cluster, service)
            if failure_reasons:
                consecutive_failures += 1
                consecutive_stable = 0  # Reset stability on failures
                if consecutive_failures >= 2:  # Show failures after 2 checks
                    print("   ⚠️  Tasks are failing:")
                    for reason in failure_reasons[:3]:  # Show up to 3 reasons
                        print(f"      ❌ {reason}")
                    
                    # If we've seen failures for 3+ consecutive checks, give up early
                    if consecutive_failures >= 4:
                        print("   ❌ Too many task failures, aborting")
                        return False
            else:
                consecutive_failures = 0
            
        except ClientError as e:
            print(f"   ⚠️  Error checking service: {e}")
        
        time.sleep(10)
    
    print(f"   ❌ Timed out after {timeout_seconds}s waiting for service")
    return False

