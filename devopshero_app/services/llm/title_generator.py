"""Generate conversation titles using Claude Haiku."""

import logging

from . import llm_client

logger = logging.getLogger(__name__)


def generate_title(user_message: str, agent_response: str, workspace_name: str | None, repo_name: str | None) -> str:
    """
    Generate a short, descriptive title for a conversation.

    Uses Claude Haiku for fast, cheap title generation.

    Args:
        user_message: The user's first message in the conversation.
        agent_response: The agent's response to the user's message.
        workspace_name: Optional workspace name for context.
        repo_name: Optional repository name for context.

    Returns:
        A title string (max 50 characters).
    """
    context_parts = []
    if workspace_name:
        context_parts.append(f"Workspace: {workspace_name}")
    if repo_name:
        context_parts.append(f"Repository: {repo_name}")

    context = "\n".join(context_parts) if context_parts else "General DevOps conversation"

    prompt = f"""Generate a short title (max 50 chars) for this DevOps deployment conversation.

Context:
{context}

User's message:
{user_message[:500]}

Agent's response:
{agent_response[:500]}

Reply with ONLY the title, no quotes or explanation."""

    client = llm_client.get_client()
    model = llm_client.get_model_id(alias="haiku-4.5")

    response = client.messages.create(
        model=model,
        max_tokens=60,
        messages=[{"role": "user", "content": prompt}],
    )

    title = response.content[0].text.strip()

    # Remove quotes if the model wrapped the title
    if title.startswith('"') and title.endswith('"'):
        title = title[1:-1]
    if title.startswith("'") and title.endswith("'"):
        title = title[1:-1]

    return title[:50]
