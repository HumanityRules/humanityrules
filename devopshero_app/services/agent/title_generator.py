"""Generate conversation titles using Claude Haiku."""

import logging
from dataclasses import dataclass

from . import llm_client

logger = logging.getLogger(__name__)

MODEL_ALIAS = "haiku-4.5"


@dataclass
class TitleResult:
    """Result of title generation including usage data for cost tracking."""

    title: str
    input_tokens: int
    output_tokens: int


def generate_title(user_message: str, agent_response: str, workspace_name: str | None, repo_name: str | None, aws_account_name: str | None) -> TitleResult:
    """Generate a short, descriptive title for a conversation."""
    context_parts = []
    if workspace_name:
        context_parts.append(f"Workspace: {workspace_name}")
    if repo_name:
        context_parts.append(f"Repository: {repo_name}")
    if aws_account_name:
        context_parts.append(f"AWS Account: {aws_account_name} (environment setup)")

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
    model_id = llm_client.get_model_id(alias=MODEL_ALIAS)

    response = client.messages.create(
        model=model_id,
        max_tokens=60,
        messages=[{"role": "user", "content": prompt}],
    )

    title = response.content[0].text.strip()

    # Remove quotes if the model wrapped the title
    if title.startswith('"') and title.endswith('"'):
        title = title[1:-1]
    if title.startswith("'") and title.endswith("'"):
        title = title[1:-1]

    # Note: cost_usd is not available from the raw Anthropic API response.
    # Only the Claude Agent SDK's ResultMessage computes cost. Token counts
    # are returned so the caller can log them; cost is left as None.
    return TitleResult(
        title=title[:50],
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
