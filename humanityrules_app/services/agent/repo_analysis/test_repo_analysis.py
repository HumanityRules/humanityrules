"""
Test harness for the repository analysis sub-agent.

This module provides a CLI tool for testing the repo analysis agent against
reference apps using the SDK's native sub-agent invocation.

Usage:
    # Single app (verbose by default)
    uv run python -m humanityrules_app.services.agent.repo_analysis.test_repo_analysis --app django_postgres_app

    # All reference apps
    uv run python -m humanityrules_app.services.agent.repo_analysis.test_repo_analysis

    # Quiet mode (just final JSON)
    uv run python -m humanityrules_app.services.agent.repo_analysis.test_repo_analysis --app django_postgres_app --quiet
"""

# Set up Django FIRST so we can reuse existing app configuration (e.g. settings.CLAUDE_MODEL_GENERAL).
# This harness does NOT use Django models, streaming, or persistence — it only needs settings.
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "humanityrules_site.settings")
django.setup()

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, UserMessage, ResultMessage
from claude_agent_sdk.types import ToolUseBlock, ToolResultBlock, TextBlock
from django.conf import settings
from pydantic import ValidationError

from humanityrules_app.services.agent.agent_client import get_claude_env
from humanityrules_app.services.agent.repo_analysis.repo_analyzer_config import get_analyze_repository_agent
from humanityrules_app.services.agent.repo_analysis.repo_analysis_schema import RepoAnalysisOutput
from humanityrules_app.services.agent import llm_client


# Reference app expectations
# These define the expected analysis results for each test app.
# The test harness validates that the agent's output matches these expectations.
@dataclass
class AppExpectation:
    """Expected analysis results for a reference app."""

    language: str
    framework: str | None
    service_type: str
    datastores: list[str]
    aws_services: list[str]


REFERENCE_APPS: dict[str, AppExpectation] = {
    "django_postgres_app": AppExpectation(
        language="python",
        framework="django",
        service_type="web",
        datastores=["postgres"],
        aws_services=[],
    ),
    "fastapi_app": AppExpectation(
        language="python",
        framework="fastapi",
        service_type="web",
        datastores=[],
        aws_services=[],
    ),
    "job_processor": AppExpectation(
        language="python",
        framework=None,
        service_type="worker",
        datastores=[],
        aws_services=["sqs", "s3"],
    ),
    "nextjs_app": AppExpectation(
        language="node",
        framework="nextjs",
        service_type="web",
        datastores=[],
        aws_services=[],
    ),
    "phoenix_app": AppExpectation(
        language="elixir",
        framework="phoenix",
        service_type="web",
        datastores=["postgres"],
        aws_services=[],
    ),
    "file_processor": AppExpectation(
        language="python",
        framework=None,
        service_type="worker",
        datastores=[],
        aws_services=["s3"],
    ),
    "scheduled_tasks": AppExpectation(
        language="python",
        framework=None,
        service_type="scheduled_task",
        datastores=[],
        aws_services=["s3"],
    ),
    "simple_dashboard": AppExpectation(
        language="python",
        framework="streamlit",
        service_type="web",
        datastores=[],
        aws_services=[],
    ),
}


def get_deployable_repos_path() -> Path:
    """Get the path to the deployable-repos directory (sibling of project root)."""
    # Navigate up from this file to find the project root
    current = Path(__file__).resolve()
    # Go up: repo_analysis -> agent -> services -> humanityrules_app -> project root
    project_root = current.parent.parent.parent.parent.parent
    deployable_repos = project_root / ".." / "deployable-repos"

    if not deployable_repos.exists():
        raise RuntimeError(f"deployable-repos directory not found at {deployable_repos}")

    return deployable_repos.resolve()




def _extract_json_from_response(text: str) -> dict:
    """Extract JSON from the agent's response text."""
    # Try to find JSON in a code block first
    json_block_pattern = r"```(?:json)?\s*\n([\s\S]*?)\n```"
    matches = re.findall(json_block_pattern, text)

    if matches:
        # Use the last JSON block (most likely to be the final output)
        json_str = matches[-1].strip()
        return json.loads(json_str)

    # If no code block, try to parse the entire text as JSON
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Try to find JSON object pattern in the text
    json_obj_pattern = r"\{[\s\S]*\}"
    match = re.search(json_obj_pattern, text)
    if match:
        return json.loads(match.group())

    raise ValueError("Could not extract JSON from agent response")


def validate_result(app_name: str, result: RepoAnalysisOutput, expectation: AppExpectation) -> list[str]:
    """Validate analysis result against expectations."""
    errors = []

    # Check language (normalize to lowercase for comparison)
    result_language = result.language.lower()
    expected_language = expectation.language.lower()
    # Handle javascript/node/typescript as equivalent
    js_variants = {"javascript", "node", "typescript", "js", "ts"}
    if expected_language in js_variants:
        if result_language not in js_variants:
            errors.append(f"Language mismatch: expected {expectation.language}, got {result.language}")
    elif result_language != expected_language:
        errors.append(f"Language mismatch: expected {expectation.language}, got {result.language}")

    # Check framework (if expected)
    if expectation.framework:
        result_framework = (result.framework or "").lower()
        expected_framework = expectation.framework.lower()
        # Handle nextjs/next as equivalent
        if expected_framework in {"nextjs", "next"}:
            if result_framework not in {"nextjs", "next"}:
                errors.append(f"Framework mismatch: expected {expectation.framework}, got {result.framework}")
        elif result_framework != expected_framework:
            errors.append(f"Framework mismatch: expected {expectation.framework}, got {result.framework}")

    # Check service type
    result_service_type = result.service.type.lower()
    expected_service_type = expectation.service_type.lower()
    if result_service_type != expected_service_type:
        errors.append(f"Service type mismatch: expected {expectation.service_type}, got {result.service.type}")

    # Check datastores (order-independent)
    result_datastores = set(d.lower() for d in result.dependencies.datastores)
    expected_datastores = set(d.lower() for d in expectation.datastores)
    if result_datastores != expected_datastores:
        errors.append(f"Datastores mismatch: expected {expectation.datastores}, got {result.dependencies.datastores}")

    # Check AWS services (order-independent)
    result_aws = set(s.lower() for s in result.dependencies.aws_services)
    expected_aws = set(s.lower() for s in expectation.aws_services)
    if result_aws != expected_aws:
        errors.append(f"AWS services mismatch: expected {expectation.aws_services}, got {result.dependencies.aws_services}")

    return errors


async def analyze_repository(repo_file_url: str, verbose: bool) -> RepoAnalysisOutput:
    """Analyze a repository using the SDK's native sub-agent invocation."""
    options = ClaudeAgentOptions(
        model=llm_client.get_model_id(alias=settings.CLAUDE_MODEL_GENERAL),
        system_prompt="You are a test orchestrator. When asked to analyze a repository, use the analyze-repository agent.",
        agents={"analyze-repository": get_analyze_repository_agent()},
        permission_mode="bypassPermissions",
        env=get_claude_env(),
    )

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"Analyzing repository: {repo_file_url}")
        print(f"{'=' * 60}\n")

    # Collect all messages from the query
    messages: list = []
    async for message in query(
        prompt=f"Use analyze-repository to analyze the repository at {repo_file_url}",
        options=options,
    ):
        messages.append(message)
        if verbose:
            print(f"[{type(message).__name__}] {_summarize_message(message)}")

    # Extract sub-agent result from Task tool result
    task_result_text = _extract_task_result(messages)
    
    # Fall back to accumulated assistant text if no Task result
    if not task_result_text:
        task_result_text = _extract_assistant_text(messages)

    if verbose:
        print(f"\n{'=' * 60}")
        print("Extracting JSON from response...")
        print(f"{'=' * 60}\n")

    try:
        json_data = _extract_json_from_response(task_result_text)
        result = RepoAnalysisOutput.model_validate(json_data)
        if verbose:
            print("[SUCCESS] JSON validated successfully\n")
            print(json.dumps(result.model_dump(), indent=2))
        return result
    except json.JSONDecodeError as e:
        if verbose:
            print(f"[ERROR] Failed to parse JSON: {e}")
            print(f"Response text:\n{task_result_text[:1000]}...")
        raise ValueError(f"Failed to parse JSON from agent response: {e}") from e
    except ValidationError as e:
        if verbose:
            print(f"[ERROR] Schema validation failed: {e}")
        raise ValueError(f"Agent response failed schema validation: {e}") from e


def _summarize_message(message) -> str:
    """Return a one-line summary of a message for verbose logging."""
    if isinstance(message, AssistantMessage):
        tools = [b.name for b in message.content if isinstance(b, ToolUseBlock)]
        texts = [b.text[:50] for b in message.content if isinstance(b, TextBlock) and b.text]
        parts = []
        if tools:
            parts.append(f"tools={tools}")
        if texts:
            parts.append(f"text={texts}")
        return " ".join(parts) or "(empty)"
    elif isinstance(message, UserMessage):
        if isinstance(message.content, list):
            tool_results = [b.tool_use_id[:8] for b in message.content if isinstance(b, ToolResultBlock)]
            if tool_results:
                return f"tool_results={tool_results}"
        return str(message.content)[:100]
    elif isinstance(message, ResultMessage):
        cost = f"${message.total_cost_usd:.4f}" if message.total_cost_usd else "N/A"
        return f"turns={message.num_turns}, cost={cost}"
    return str(message)[:100]


def _extract_task_result(messages: list) -> str:
    """Extract the Task tool result (sub-agent output) from messages."""
    # Find Task tool use IDs from AssistantMessages
    task_tool_ids = set()
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock) and block.name == "Task":
                    task_tool_ids.add(block.id)

    # Find corresponding tool results
    result_text = ""
    for msg in messages:
        if isinstance(msg, UserMessage) and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolResultBlock) and block.tool_use_id in task_tool_ids:
                    if isinstance(block.content, list):
                        for content_block in block.content:
                            if isinstance(content_block, dict) and content_block.get("type") == "text":
                                result_text += content_block.get("text", "")
                    elif isinstance(block.content, str):
                        result_text += block.content
    return result_text


def _extract_assistant_text(messages: list) -> str:
    """Extract all text from AssistantMessages as fallback."""
    text = ""
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text:
                    text += block.text
    return text


async def test_app(app_name: str, verbose: bool) -> tuple[bool, str]:
    """Test the repo analysis agent against a single reference app."""
    deployable_repos = get_deployable_repos_path()
    app_path = deployable_repos / app_name

    if not app_path.exists():
        return False, f"App directory not found: {app_path}"

    repo_url = f"file://{app_path}"

    try:
        result = await analyze_repository(repo_file_url=repo_url, verbose=verbose)

        # Get expectation if we have one
        expectation = REFERENCE_APPS.get(app_name)
        if expectation:
            errors = validate_result(app_name=app_name, result=result, expectation=expectation)
            if errors:
                return False, f"Validation errors:\n  " + "\n  ".join(errors)

        # Output JSON result
        if not verbose:
            print(json.dumps(result.model_dump(), indent=2))

        return True, "Analysis completed successfully"

    except Exception as e:
        return False, f"Error: {e}"


async def main() -> int:
    """Main entry point for the test harness."""
    parser = argparse.ArgumentParser(
        description="Test the repository analysis sub-agent against reference apps."
    )
    parser.add_argument(
        "--app",
        type=str,
        help="Name of the app to test (e.g., django_postgres_app). If not provided, tests all apps.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Quiet mode - only output final JSON, no verbose logging.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available reference apps and exit.",
    )

    args = parser.parse_args()

    # Handle --list flag
    if args.list:
        print("Available reference apps:")
        for app_name, expectation in REFERENCE_APPS.items():
            print(f"  {app_name}: {expectation.language}/{expectation.framework or 'no-framework'} ({expectation.service_type})")
        return 0

    verbose = not args.quiet

    if args.app:
        # Test single app
        success, message = await test_app(app_name=args.app, verbose=verbose)
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"Result: {'PASS' if success else 'FAIL'}")
            print(f"Message: {message}")
            print(f"{'=' * 60}")
        return 0 if success else 1
    else:
        # Test all apps
        results = []
        for app_name in REFERENCE_APPS:
            if verbose:
                print(f"\n{'#' * 60}")
                print(f"# Testing: {app_name}")
                print(f"{'#' * 60}")

            success, message = await test_app(app_name=app_name, verbose=verbose)
            results.append((app_name, success, message))

            if verbose:
                print(f"\nResult: {'PASS' if success else 'FAIL'} - {message}")

        # Summary
        print(f"\n{'=' * 60}")
        print("SUMMARY")
        print(f"{'=' * 60}")
        passed = sum(1 for _, success, _ in results if success)
        failed = len(results) - passed
        print(f"Passed: {passed}/{len(results)}")
        print(f"Failed: {failed}/{len(results)}")

        if failed > 0:
            print("\nFailed apps:")
            for app_name, success, message in results:
                if not success:
                    print(f"  {app_name}: {message}")

        return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
