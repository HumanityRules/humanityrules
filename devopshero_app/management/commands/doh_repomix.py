"""
Run Repomix on a repository to generate an AI-friendly packed representation.

Usage:
    uv run manage.py doh_repomix /path/to/repo
    uv run manage.py doh_repomix /path/to/repo --output /tmp/output.xml
    uv run manage.py doh_repomix /path/to/repo --style markdown
    uv run manage.py doh_repomix /path/to/repo --compress
    uv run manage.py doh_repomix /path/to/repo --include "src/**/*.py,*.toml"
    uv run manage.py doh_repomix /path/to/repo --ignore "tests/**,docs/**"

Runs `npx repomix@latest` on the target repository and writes the packed output
to a file. Useful for evaluating Repomix as input for the repository analysis agent.
"""

import subprocess
import sys
import time

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run Repomix on a repository to generate AI-friendly packed output"

    def add_arguments(self, parser):
        parser.add_argument(
            "repo_path",
            help="Path to the repository to analyze",
        )
        parser.add_argument(
            "--output",
            help="Output file path (default: repomix-output.xml in the repo directory)",
        )
        parser.add_argument(
            "--style",
            choices=["xml", "markdown", "plain", "json"],
            default="xml",
            help="Output format (default: xml)",
        )
        parser.add_argument(
            "--compress",
            action="store_true",
            help="Use Tree-sitter compression to extract signatures only, reducing token count",
        )
        parser.add_argument(
            "--include",
            help="Glob patterns for files to include (comma-separated, e.g. 'src/**/*.py,*.toml')",
        )
        parser.add_argument(
            "--ignore",
            help="Glob patterns for files to exclude (comma-separated, e.g. 'tests/**,docs/**')",
        )
        parser.add_argument(
            "--remove-comments",
            action="store_true",
            help="Strip comments from source files",
        )
        parser.add_argument(
            "--line-numbers",
            action="store_true",
            help="Show line numbers in the output",
        )
        parser.add_argument(
            "--token-count-tree",
            action="store_true",
            help="Display token count tree instead of generating output",
        )

    def handle(self, *args, **options):
        """Execute Repomix on the target repository."""
        repo_path = Path(options["repo_path"]).resolve()
        if not repo_path.exists():
            raise CommandError(f"Repository path does not exist: {repo_path}")
        if not repo_path.is_dir():
            raise CommandError(f"Repository path is not a directory: {repo_path}")

        cmd = self._build_command(
            repo_path=repo_path,
            output=options["output"],
            style=options["style"],
            compress=options["compress"],
            include=options["include"],
            ignore=options["ignore"],
            remove_comments=options["remove_comments"],
            line_numbers=options["line_numbers"],
            token_count_tree=options["token_count_tree"],
        )

        self.stdout.write(f"Repository: {repo_path}")
        self.stdout.write(f"Command:    {' '.join(cmd)}")
        self.stdout.write("")

        start_time = time.monotonic()

        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
        )

        elapsed = time.monotonic() - start_time

        # Print stdout (Repomix progress and summary)
        if result.stdout:
            self.stdout.write(result.stdout)

        if result.returncode != 0:
            if result.stderr:
                self.stderr.write(result.stderr)
            raise CommandError(f"Repomix failed with exit code {result.returncode}")

        self.stdout.write(self.style.SUCCESS(f"Completed in {elapsed:.1f}s"))

        # Report output file details
        if not options["token_count_tree"]:
            output_path = self._resolve_output_path(
                repo_path=repo_path,
                output=options["output"],
                style=options["style"],
            )
            if output_path.exists():
                size_kb = output_path.stat().st_size / 1024
                self.stdout.write(f"Output:     {output_path} ({size_kb:.1f} KB)")
            else:
                self.stdout.write(f"Expected output at {output_path} but file not found")

    def _build_command(
        self,
        repo_path: Path,
        output: str | None,
        style: str,
        compress: bool,
        include: str | None,
        ignore: str | None,
        remove_comments: bool,
        line_numbers: bool,
        token_count_tree: bool,
    ) -> list[str]:
        """Build the npx repomix command."""
        cmd = ["npx", "--yes", "repomix@latest"]

        cmd.extend(["--style", style])

        if output:
            cmd.extend(["--output", output])

        if compress:
            cmd.append("--compress")

        if include:
            cmd.extend(["--include", include])

        if ignore:
            cmd.extend(["--ignore", ignore])

        if remove_comments:
            cmd.append("--remove-comments")

        if line_numbers:
            cmd.append("--output-show-line-numbers")

        if token_count_tree:
            cmd.append("--token-count-tree")

        return cmd

    def _resolve_output_path(self, repo_path: Path, output: str | None, style: str) -> Path:
        """Determine where Repomix will write the output file."""
        if output:
            output_path = Path(output)
            if output_path.is_absolute():
                return output_path
            return repo_path / output_path

        # Repomix default naming convention
        extension_map = {
            "xml": "xml",
            "markdown": "md",
            "plain": "txt",
            "json": "json",
        }
        ext = extension_map.get(style, "xml")
        return repo_path / f"repomix-output.{ext}"
