"""
Configuration for the generate-dockerfile sub-agent.

This configuration is used by the main deployment agent to spawn the Dockerfile
generator when the repository has no existing Dockerfile.
"""

from pathlib import Path

from claude_agent_sdk import AgentDefinition


def _load_system_prompt() -> str:
    """Load the system prompt from the markdown file."""
    prompt_path = Path(__file__).parent / "dockerfile_generator_system_prompt.md"
    return prompt_path.read_text()


def get_generate_dockerfile_agent() -> AgentDefinition:
    """
    Get the generate-dockerfile sub-agent configuration.

    Returns an AgentDefinition suitable for the `agents` parameter in ClaudeAgentOptions.
    Called as a function to ensure the system prompt is loaded fresh.
    """
    return AgentDefinition(
        description=(
            "Dockerfile generator specialist. Given a repository analysis JSON and "
            "target environment slug, generates a production-ready Dockerfile, "
            "validates it with a test build, and returns the path. "
            "Retries up to 3 times if the build fails."
        ),
        prompt=_load_system_prompt(),
        tools=["Write", "Read", "Glob", "Grep", "mcp__devopshero__test_docker_build"],
    )
