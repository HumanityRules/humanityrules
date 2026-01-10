"""
Repository inspection tool for analyzing application repositories.

This tool analyzes local repositories to detect application characteristics
like framework, language, build strategy, and configuration.
"""

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class RepositoryAnalysis:
    """Result of analyzing a repository."""

    detected_framework: str | None  # flask, django, fastapi, nextjs, express, etc.
    detected_language: str | None  # python, javascript, go, elixir, etc.
    has_dockerfile: bool
    dockerfile_path: str | None
    has_requirements: bool  # requirements.txt
    has_package_json: bool
    suggested_port: int | None
    suggested_health_path: str | None
    environment_variables: list[str]  # Required env vars detected
    detected_database: str | None  # postgres, mysql, etc.

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


def _parse_file_url(repo_url: str) -> Path:
    """
    Parse a file:// URL and return the local path.

    Args:
        repo_url: URL in file:// format.

    Returns:
        Path object for the local directory.

    Raises:
        ValueError: If URL is not a valid file:// URL.
    """
    parsed = urlparse(repo_url)

    if parsed.scheme != "file":
        raise ValueError(
            f"Only file:// URLs are supported in v1. Got: {parsed.scheme}://"
        )

    # Handle file:///path/to/repo and file://localhost/path/to/repo
    path = parsed.path
    if not path:
        raise ValueError(f"Invalid file URL: {repo_url}")

    repo_path = Path(path)
    if not repo_path.exists():
        raise ValueError(f"Repository path does not exist: {repo_path}")

    if not repo_path.is_dir():
        raise ValueError(f"Repository path is not a directory: {repo_path}")

    return repo_path


def _detect_python_framework(repo_path: Path) -> str | None:
    """Detect Python web framework from requirements or imports."""
    # Check requirements.txt
    requirements_file = repo_path / "requirements.txt"
    if requirements_file.exists():
        content = requirements_file.read_text().lower()
        if "flask" in content:
            return "flask"
        if "django" in content:
            return "django"
        if "fastapi" in content:
            return "fastapi"
        if "streamlit" in content:
            return "streamlit"

    # Check pyproject.toml
    pyproject_file = repo_path / "pyproject.toml"
    if pyproject_file.exists():
        content = pyproject_file.read_text().lower()
        if "flask" in content:
            return "flask"
        if "django" in content:
            return "django"
        if "fastapi" in content:
            return "fastapi"
        if "streamlit" in content:
            return "streamlit"

    return None


def _detect_node_framework(repo_path: Path) -> str | None:
    """Detect Node.js framework from package.json."""
    package_json = repo_path / "package.json"
    if not package_json.exists():
        return None

    try:
        data = json.loads(package_json.read_text())
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}

        if "next" in deps:
            return "nextjs"
        if "express" in deps:
            return "express"
        if "fastify" in deps:
            return "fastify"
        if "nuxt" in deps:
            return "nuxt"
        if "remix" in deps:
            return "remix"
    except json.JSONDecodeError:
        pass

    return None


def _detect_elixir_framework(repo_path: Path) -> str | None:
    """Detect Elixir framework from mix.exs."""
    mix_file = repo_path / "mix.exs"
    if not mix_file.exists():
        return None

    content = mix_file.read_text().lower()
    if "phoenix" in content:
        return "phoenix"

    return None


def _detect_language(repo_path: Path) -> str | None:
    """Detect primary programming language."""
    indicators = [
        ("requirements.txt", "python"),
        ("pyproject.toml", "python"),
        ("setup.py", "python"),
        ("package.json", "javascript"),
        ("go.mod", "go"),
        ("Cargo.toml", "rust"),
        ("mix.exs", "elixir"),
        ("Gemfile", "ruby"),
        ("pom.xml", "java"),
        ("build.gradle", "java"),
    ]

    for filename, language in indicators:
        if (repo_path / filename).exists():
            return language

    return None


def _find_dockerfile(repo_path: Path) -> str | None:
    """Find Dockerfile and return its path relative to repo root."""
    # Check common locations
    candidates = [
        "Dockerfile",
        "dockerfile",
        "docker/Dockerfile",
        ".docker/Dockerfile",
    ]

    for candidate in candidates:
        if (repo_path / candidate).exists():
            return candidate

    return None


def _detect_port_from_dockerfile(repo_path: Path, dockerfile_path: str) -> int | None:
    """Extract EXPOSE port from Dockerfile."""
    dockerfile = repo_path / dockerfile_path
    if not dockerfile.exists():
        return None

    content = dockerfile.read_text()
    # Match EXPOSE directive
    match = re.search(r"EXPOSE\s+(\d+)", content, re.IGNORECASE)
    if match:
        return int(match.group(1))

    return None


def _suggest_port(
    language: str | None,
    framework: str | None,
    repo_path: Path,
    dockerfile_path: str | None,
) -> int | None:
    """Suggest a port based on framework/language conventions."""
    # Try Dockerfile first
    if dockerfile_path:
        port = _detect_port_from_dockerfile(repo_path, dockerfile_path)
        if port:
            return port

    # Framework-specific defaults
    framework_ports = {
        "flask": 5000,
        "django": 8000,
        "fastapi": 8000,
        "streamlit": 8501,
        "express": 3000,
        "nextjs": 3000,
        "nuxt": 3000,
        "phoenix": 4000,
    }

    if framework and framework in framework_ports:
        return framework_ports[framework]

    # Language-specific defaults
    language_ports = {
        "python": 8000,
        "javascript": 3000,
        "go": 8080,
        "rust": 8080,
        "elixir": 4000,
        "ruby": 3000,
        "java": 8080,
    }

    if language and language in language_ports:
        return language_ports[language]

    return None


def _suggest_health_path(framework: str | None) -> str | None:
    """Suggest a health check path based on framework."""
    health_paths = {
        "flask": "/health",
        "django": "/health/",
        "fastapi": "/health",
        "streamlit": "/_stcore/health",
        "express": "/health",
        "nextjs": "/api/health",
        "phoenix": "/health",
    }

    return health_paths.get(framework, "/health")


def _detect_database(repo_path: Path) -> str | None:
    """Detect database dependencies."""
    # Check Python requirements
    requirements_file = repo_path / "requirements.txt"
    if requirements_file.exists():
        content = requirements_file.read_text().lower()
        if "psycopg" in content or "asyncpg" in content:
            return "postgres"
        if "mysqlclient" in content or "pymysql" in content or "mysql" in content:
            return "mysql"
        if "motor" in content or "pymongo" in content:
            return "mongodb"

    # Check package.json
    package_json = repo_path / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text())
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            deps_str = " ".join(deps.keys()).lower()

            if "pg" in deps or "postgres" in deps_str:
                return "postgres"
            if "mysql" in deps_str:
                return "mysql"
            if "mongodb" in deps_str or "mongoose" in deps:
                return "mongodb"
        except json.JSONDecodeError:
            pass

    # Check Elixir mix.exs
    mix_file = repo_path / "mix.exs"
    if mix_file.exists():
        content = mix_file.read_text().lower()
        if "postgrex" in content:
            return "postgres"
        if "myxql" in content:
            return "mysql"

    return None


def _detect_env_vars(repo_path: Path) -> list[str]:
    """Detect required environment variables from various sources."""
    env_vars = set()

    # Check .env.example or .env.sample
    for env_file in [".env.example", ".env.sample", ".env.template"]:
        env_path = repo_path / env_file
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    var_name = line.split("=")[0].strip()
                    env_vars.add(var_name)

    # Check for common patterns in Python files
    # (This is a simple heuristic, not exhaustive)
    for py_file in repo_path.glob("**/*.py"):
        if ".venv" in str(py_file) or "node_modules" in str(py_file):
            continue
        try:
            content = py_file.read_text()
            # Match os.environ.get("VAR") or os.getenv("VAR")
            matches = re.findall(
                r'os\.(?:environ\.get|getenv)\s*\(\s*["\'](\w+)["\']', content
            )
            env_vars.update(matches)
        except (UnicodeDecodeError, PermissionError):
            continue

    return sorted(env_vars)


def inspect_repository(repo_url: str, branch: str) -> RepositoryAnalysis:
    """
    Analyze a repository's contents.

    Args:
        repo_url: Repository URL. Only file:// URLs supported in v1.
                  Example: file:///app/deployable_repos/flask-app
        branch: Branch to analyze (currently ignored for file:// URLs).

    Returns:
        RepositoryAnalysis with detected characteristics.

    Raises:
        ValueError: If URL is not a valid file:// URL or path doesn't exist.

    Note:
        Only file:// URLs are supported in v1.
        Git URLs (https://, git://) are out of scope.
    """
    # Parse the file URL
    repo_path = _parse_file_url(repo_url)

    # Detect language first
    language = _detect_language(repo_path)

    # Detect framework based on language
    framework = None
    if language == "python":
        framework = _detect_python_framework(repo_path)
    elif language == "javascript":
        framework = _detect_node_framework(repo_path)
    elif language == "elixir":
        framework = _detect_elixir_framework(repo_path)

    # Find Dockerfile
    dockerfile_path = _find_dockerfile(repo_path)

    # Detect other characteristics
    has_requirements = (repo_path / "requirements.txt").exists()
    has_package_json = (repo_path / "package.json").exists()

    suggested_port = _suggest_port(language, framework, repo_path, dockerfile_path)
    suggested_health_path = _suggest_health_path(framework)
    detected_database = _detect_database(repo_path)
    environment_variables = _detect_env_vars(repo_path)

    return RepositoryAnalysis(
        detected_framework=framework,
        detected_language=language,
        has_dockerfile=dockerfile_path is not None,
        dockerfile_path=dockerfile_path,
        has_requirements=has_requirements,
        has_package_json=has_package_json,
        suggested_port=suggested_port,
        suggested_health_path=suggested_health_path,
        environment_variables=environment_variables,
        detected_database=detected_database,
    )
