"""
Repository analysis sub-agent module.

This module provides configuration and schema for the repo-analyzer sub-agent
that analyzes repositories and produces structured JSON findings with evidence.

The sub-agent is invoked via the SDK's native sub-agents feature (Task tool),
not as a standalone function.
"""

from .repo_analysis_schema import RepoAnalysisOutput
from .repo_analyzer_config import get_repo_analyzer_agent

__all__ = ["RepoAnalysisOutput", "get_repo_analyzer_agent"]
