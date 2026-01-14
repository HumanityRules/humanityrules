"""
Shared configuration for the repo-analyzer sub-agent.

This configuration is used by both the main agent and the test harness
to ensure consistent sub-agent behavior.
"""

from pathlib import Path

from claude_agent_sdk import AgentDefinition


def _load_system_prompt() -> str:
    """Load the system prompt from the markdown file."""
    prompt_path = Path(__file__).parent / "system_prompt.md"
    return prompt_path.read_text()


def get_repo_analyzer_agent() -> AgentDefinition:
    """
    Get the repo-analyzer sub-agent configuration.

    Returns an AgentDefinition suitable for the `agents` parameter in ClaudeAgentOptions.
    Called as a function to ensure the system prompt is loaded fresh.
    """
    return AgentDefinition(
        description=(
            "Expert repository analyzer. Use when you need to analyze a repository "
            "to understand its language, framework, dependencies, service type, "
            "and deployment requirements. Returns structured JSON with evidence."
        ),
        prompt=_load_system_prompt(),
        tools=["Bash", "Read", "LS", "Glob", "Grep"],
    )
