"""
Dockerfile generation sub-agent module.

This module provides configuration for the generate-dockerfile sub-agent
that produces a production-ready Dockerfile from repository analysis JSON.
The sub-agent is invoked via the SDK's Task tool by the main deployment agent.
"""

from .dockerfile_gen_config import get_generate_dockerfile_agent

__all__ = ["get_generate_dockerfile_agent"]
