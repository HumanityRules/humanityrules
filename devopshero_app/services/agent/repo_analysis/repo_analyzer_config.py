"""
Configuration for the analyze-repository sub-agent.

This configuration is used by both the main agent and the test harness
to ensure consistent sub-agent behavior.
"""

from pathlib import Path

from claude_agent_sdk import AgentDefinition


def _load_system_prompt() -> str:
    """Load the system prompt from the markdown file."""
    prompt_path = Path(__file__).parent / "system_prompt.md"
    return prompt_path.read_text()


def get_analyze_repository_agent() -> AgentDefinition:
    """
    Get the analyze-repository sub-agent configuration.

    Returns an AgentDefinition suitable for the `agents` parameter in ClaudeAgentOptions.
    Called as a function to ensure the system prompt is loaded fresh.
    """
    return AgentDefinition(
        description=(
            "Deep repository analyzer. Use to analyze a repository and understand "
            "its architecture, dependencies, deployment requirements, potential issues, "
            "and questions to ask the user. Returns structured JSON with evidence."
        ),
        prompt=_load_system_prompt(),
        # prompt="Do a quick Bash ls of the repository and return your interpretation of the contents. Also, try to ls /Users/vmendi/websites/devopshero/",
        tools=["Bash", "Read", "Glob", "Grep"],
    )
