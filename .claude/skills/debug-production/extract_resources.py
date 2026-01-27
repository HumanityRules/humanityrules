#!/usr/bin/env python3
"""Extract DOH production resource names from CDK source code."""

import re
from pathlib import Path


def find_infra_dir() -> Path:
    """Walk up from current file until we find infra_devopshero/."""
    path = Path(__file__).resolve().parent
    while path != path.parent:
        candidate = path / "infra_devopshero"
        if candidate.is_dir():
            return candidate
        path = path.parent
    raise RuntimeError("Could not find infra_devopshero/")


def extract_resources() -> dict:
    """Parse CDK files and return resource names for debugging."""
    infra_dir = find_infra_dir()
    
    resources = {}
    
    # Extract prefix and stacks from app.py
    app_py = (infra_dir / "app.py").read_text()
    
    prefix_match = re.search(r'prefix\s*=\s*["\']([^"\']+)["\']', app_py)
    resources["stack_prefix"] = prefix_match.group(1) if prefix_match else None
    
    # Find all stack instantiations: f"{prefix}-name"
    stack_names = re.findall(r'f"{prefix}-(\w+)"', app_py)
    resources["stacks"] = stack_names
    
    # Extract from cluster_stack.py
    cluster_py = (infra_dir / "stacks" / "cluster_stack.py").read_text()
    
    cluster_match = re.search(r'cluster_name\s*=\s*["\']([^"\']+)["\']', cluster_py)
    resources["ecs_cluster"] = cluster_match.group(1) if cluster_match else None
    
    log_group_match = re.search(r'log_group_name\s*=\s*["\']([^"\']+)["\']', cluster_py)
    resources["log_group"] = log_group_match.group(1) if log_group_match else None
    
    # Extract from app_stack.py
    app_stack_py = (infra_dir / "stacks" / "app_stack.py").read_text()
    
    service_match = re.search(r'service_name\s*=\s*["\']([^"\']+)["\']', app_stack_py)
    resources["ecs_service"] = service_match.group(1) if service_match else None
    
    # Container names and stream prefixes
    container_matches = re.findall(r'container_name\s*=\s*["\']([^"\']+)["\']', app_stack_py)
    resources["containers"] = container_matches
    
    stream_matches = re.findall(r'stream_prefix\s*=\s*["\']([^"\']+)["\']', app_stack_py)
    resources["log_stream_prefixes"] = stream_matches
    
    return resources


def main():
    """Print resource names for debugging."""
    resources = extract_resources()
    
    print("DOH Production Resources")
    print("=" * 40)
    print(f"Stack prefix: {resources['stack_prefix']}")
    print(f"Stacks: {', '.join(resources['stacks'])}")
    print(f"ECS cluster: {resources['ecs_cluster']}")
    print(f"ECS service: {resources['ecs_service']}")
    print(f"Containers: {', '.join(resources['containers'])}")
    print(f"Log group: {resources['log_group']}")
    print(f"Log stream prefixes: {', '.join(resources['log_stream_prefixes'])}")


if __name__ == "__main__":
    main()
