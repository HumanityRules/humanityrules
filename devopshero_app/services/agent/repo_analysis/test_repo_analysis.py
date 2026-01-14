"""
Test harness for the repository analysis sub-agent.

This module provides a CLI tool for testing the repo analysis agent against
reference apps. It runs the agent and validates the output against expected results.

Usage:
    # Single app (verbose by default)
    uv run python -m devopshero_app.services.agent.repo_analysis.test_repo_analysis --app django_postgres_app

    # All reference apps
    uv run python -m devopshero_app.services.agent.repo_analysis.test_repo_analysis

    # Quiet mode (just final JSON)
    uv run python -m devopshero_app.services.agent.repo_analysis.test_repo_analysis --app django_postgres_app --quiet
"""

# Set up Django FIRST, before any other imports that might trigger Django model loading
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "devopshero_site.settings")
django.setup()

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path


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
    """Get the path to the deployable_repos directory."""
    # Navigate up from this file to find the project root
    current = Path(__file__).resolve()
    # Go up: repo_analysis -> agent -> services -> devopshero_app -> project root
    project_root = current.parent.parent.parent.parent.parent
    deployable_repos = project_root / "deployable_repos"

    if not deployable_repos.exists():
        raise RuntimeError(f"deployable_repos directory not found at {deployable_repos}")

    return deployable_repos


def validate_result(app_name: str, result: "RepoAnalysisOutput", expectation: AppExpectation) -> list[str]:
    """
    Validate analysis result against expectations.

    Returns a list of validation errors (empty if all checks pass).
    """
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


async def test_app(app_name: str, verbose: bool) -> tuple[bool, str]:
    """
    Test the repo analysis agent against a single reference app.

    Returns (success, message).
    """
    # Import here to avoid Django setup issues when just parsing args
    from devopshero_app.services.agent.repo_analysis.repo_analysis_agent import analyze_repository
    from devopshero_app.services.agent.repo_analysis.repo_analysis_schema import RepoAnalysisOutput

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
