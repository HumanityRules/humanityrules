"""
Repository analysis sub-agent module.

This module provides an LLM-powered sub-agent that analyzes repositories
and produces structured JSON findings with evidence.
"""

from .repo_analysis_agent import analyze_repository
from .repo_analysis_schema import RepoAnalysisOutput

__all__ = ["analyze_repository", "RepoAnalysisOutput"]
