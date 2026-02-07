"""
Sandbox directory structure for agent conversations.

Each conversation gets an isolated sandbox with:
- src/  — cloned repository (agent working directory)
- tmp/  — temp files for the conversation
"""

from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


@dataclass
class SandboxPaths:
    """Sandbox directory structure for a conversation."""
    root_path: Path   # sandbox/conv-{id}/ — conversation isolation folder
    src_path: Path    # sandbox/conv-{id}/src/ — cloned repository
    tmp_path: Path    # sandbox/conv-{id}/tmp/ — temp files for this conversation


def get_sandbox_paths(conversation_id) -> SandboxPaths:
    """Single source of truth for conversation sandbox paths."""
    root_path = settings.CLAUDE_SANDBOX_DIR / f"conv-{conversation_id}"
    return SandboxPaths(
        root_path=root_path,
        src_path=root_path / "src",
        tmp_path=root_path / "tmp",
    )
