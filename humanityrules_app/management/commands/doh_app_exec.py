"""
Run a bash script non-interactively in a deployed customer app container and
return clean stdout/stderr/exit_code. For an interactive shell, use doh_app_shell.

Usage:
    uv run manage.py doh_app_exec --account "CH Sandbox" --app my-app --as hermeswebui <<'EOF'
    /app/venv/bin/python -c "from tools.mcp_tool import discover_mcp_tools; print(discover_mcp_tools())"
    EOF

    uv run manage.py doh_app_exec --account "CH Sandbox" --app my-app --script-file probe.sh --format json

Flags: --as USER, --timeout SECONDS, --cwd PATH, --set KEY=VALUE, --format text|json, --ignore-exit.

Requires: AWS CLI v2 + AWS Session Manager Plugin + DOH creds in .env.
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys

from django.core.management.base import BaseCommand, CommandError

from humanityrules_app.models import App

from . import _aws_account_resolver
from . import doh_app_shell


_MAX_OUTPUT_BYTES = 1_048_576

_SENTINEL_BEGIN = "___HUMR_EXEC_BEGIN___"
_SENTINEL_END = "___HUMR_EXEC_END___"
_RC_LINE_RE = re.compile(rf"^{re.escape(_SENTINEL_END)} RC=(-?\d+)$")


def _build_wrapper_script(user_script_b64: str, run_as: str | None, timeout: int | None, cwd: str | None, env_vars: dict[str, str]) -> str:
    """Build the server-side wrapper that tags stdout/stderr and prints a RC line."""
    env_exports = "".join(f"export {k}={_sh_quote(v)}; " for k, v in env_vars.items())
    cwd_cmd = f"cd {_sh_quote(cwd)} && " if cwd else ""

    inner = f"{env_exports}{cwd_cmd}bash /tmp/doh_exec_script.sh"
    if timeout is not None:
        inner = f"timeout --preserve-status {int(timeout)}s {inner}"
    if run_as is not None:
        # -H so ~ resolves to the target user's home (config loaders depend on it).
        inner = f"sudo -EH -u {_sh_quote(run_as)} bash -c {_sh_quote(inner)}"

    # FIFOs + explicit PIDs so `wait` joins the sed pipes before we echo RC.
    # Process substitution (`> >(sed ...)`) is async and can't be waited on.
    return f"""
set +e
echo '{user_script_b64}' | base64 -d > /tmp/doh_exec_script.sh
chmod +x /tmp/doh_exec_script.sh
FIFO_O=$(mktemp -u)
FIFO_E=$(mktemp -u)
mkfifo "$FIFO_O" "$FIFO_E"
sed 's/^/O:/' < "$FIFO_O" &
PID_O=$!
sed 's/^/E:/' < "$FIFO_E" &
PID_E=$!
echo {_SENTINEL_BEGIN}
( {inner} ) > "$FIFO_O" 2> "$FIFO_E"
_RC=$?
wait "$PID_O" "$PID_E"
rm -f "$FIFO_O" "$FIFO_E" /tmp/doh_exec_script.sh
echo "{_SENTINEL_END} RC=$_RC"
""".strip()


def _sh_quote(value: str) -> str:
    """Single-quote a value for safe bash interpolation."""
    return "'" + value.replace("'", "'\\''") + "'"


def _parse_wrapped_output(raw: str) -> tuple[int | None, str, str, bool, bool]:
    """Split wrapper output into (exit_code, stdout, stderr, stdout_truncated, stderr_truncated)."""
    begin_idx = raw.find(_SENTINEL_BEGIN)
    if begin_idx == -1:
        raise CommandError(
            "Could not find output begin sentinel — the script likely never started. "
            f"Raw output:\n{raw[:2000]}"
        )

    tail = raw[begin_idx + len(_SENTINEL_BEGIN):]

    exit_code: int | None = None
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    for line in tail.splitlines():
        if line.startswith("O:"):
            stdout_lines.append(line[2:])
            continue
        if line.startswith("E:"):
            stderr_lines.append(line[2:])
            continue
        rc_match = _RC_LINE_RE.match(line)
        if rc_match:
            exit_code = int(rc_match.group(1))
            break

    stdout = "\n".join(stdout_lines)
    stderr = "\n".join(stderr_lines)

    stdout_truncated = len(stdout) > _MAX_OUTPUT_BYTES
    stderr_truncated = len(stderr) > _MAX_OUTPUT_BYTES
    if stdout_truncated:
        stdout = stdout[:_MAX_OUTPUT_BYTES] + f"\n[... TRUNCATED at {_MAX_OUTPUT_BYTES} bytes ...]"
    if stderr_truncated:
        stderr = stderr[:_MAX_OUTPUT_BYTES] + f"\n[... TRUNCATED at {_MAX_OUTPUT_BYTES} bytes ...]"

    return exit_code, stdout, stderr, stdout_truncated, stderr_truncated


def _task_container_user(ecs_client, cluster: str, task_arn: str, container_name: str) -> str | None:
    """Return the task definition's `user` field for the container, if set."""
    task_resp = ecs_client.describe_tasks(cluster=cluster, tasks=[task_arn])
    task_def_arn = task_resp["tasks"][0]["taskDefinitionArn"]
    td_resp = ecs_client.describe_task_definition(taskDefinition=task_def_arn)
    for container in td_resp["taskDefinition"]["containerDefinitions"]:
        if container["name"] == container_name:
            return container.get("user")
    return None


def _run_execute_command_capture(session, cluster: str, task_id: str, container_name: str, wrapper_script: str) -> str:
    """Invoke aws ecs execute-command and return combined stdout+stderr as a string."""
    creds = session.get_credentials().get_frozen_credentials()

    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = creds.access_key
    env["AWS_SECRET_ACCESS_KEY"] = creds.secret_key
    env["AWS_SESSION_TOKEN"] = creds.token
    region = session.region_name
    if region:
        env["AWS_DEFAULT_REGION"] = region

    cmd = [
        "aws",
        "ecs",
        "execute-command",
        "--cluster", cluster,
        "--task", task_id,
        "--container", container_name,
        "--interactive",
        "--command", f"bash -c {_sh_quote(wrapper_script)}",
    ]

    # Keep stdin open; closing it makes SSM end the session before the remote
    # script finishes ("Cannot perform start session: EOF").
    feeder = subprocess.Popen(["sleep", "3600"], stdout=subprocess.PIPE)
    try:
        result = subprocess.run(
            cmd,
            env=env,
            stdin=feeder.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    finally:
        feeder.terminate()
        feeder.wait(timeout=5)
    return result.stdout


class Command(BaseCommand):
    help = "Run a bash script in a customer app container and return exit_code/stdout/stderr (non-interactive)"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        _aws_account_resolver.add_aws_target_args(parser=parser, env_default="default")
        parser.add_argument("--app", required=True, help="App slug")
        parser.add_argument(
            "--container",
            help=(
                "Template container name. Defaults to the sole container, or the template's "
                "ALB target when multiple containers exist."
            ),
        )
        parser.add_argument(
            "--as",
            dest="run_as",
            help=(
                "Run the script as this user (via sudo -EH -u). Defaults to the user "
                "from the task definition's containerDefinitions[].user field. Pass --as root "
                "to run as the ECS Exec default (root)."
            ),
        )
        parser.add_argument(
            "--script-file",
            help="Read the bash script from this file instead of stdin.",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            help="Kill the inner script server-side after this many seconds.",
        )
        parser.add_argument(
            "--cwd",
            help="Run the script in this working directory.",
        )
        parser.add_argument(
            "--set",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            dest="env_vars",
            help="Export an env var into the script's environment. Repeatable.",
        )
        parser.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            help="Output format. 'text' prints stdout/stderr/exit_code separated; 'json' emits a single JSON object.",
        )
        parser.add_argument(
            "--ignore-exit",
            action="store_true",
            help="Don't propagate the inner script's exit code to doh_app_exec's own exit code.",
        )

    def handle(self, *args, **options) -> None:
        script_text = self._read_script(script_file=options.get("script_file"))

        target = _aws_account_resolver.resolve_aws_target(options=options)
        if target.aws_account is None:
            raise CommandError("doh_app_exec requires DB mode (--account/--env); raw mode is not supported.")
        aws_account = target.aws_account
        session = target.session
        env_slug = target.env_slug

        try:
            app = App.objects.select_related("source_template").get(
                organization=aws_account.organization,
                slug=options["app"],
            )
        except App.DoesNotExist:
            raise CommandError(f"No app with slug '{options['app']}' in organization '{aws_account.organization.name}'.")

        cluster_name = f"devopshero-{env_slug}-cluster"
        service_name = f"doh-{env_slug}-{app.slug}"
        container_name = doh_app_shell._resolve_ecs_container_name(
            app=app, requested_container=options.get("container"),
        )

        ecs_client = session.client("ecs")
        list_resp = ecs_client.list_tasks(cluster=cluster_name, serviceName=service_name, desiredStatus="RUNNING")
        task_arns = list_resp.get("taskArns") or []
        if not task_arns:
            raise CommandError(
                f"No RUNNING tasks for service '{service_name}' in cluster '{cluster_name}'."
            )

        task_arn = task_arns[0]
        task_id = task_arn.split("/")[-1]

        # Wait only briefly; this command targets already-healthy tasks.
        doh_app_shell._wait_for_task_running(
            ecs_client=ecs_client, cluster=cluster_name, task_arn=task_arn, stdout=self.stderr,
        )
        doh_app_shell._wait_for_exec_agent(
            ecs_client=ecs_client, cluster=cluster_name, task_arn=task_arn,
            container_name=container_name, stdout=self.stderr,
        )

        run_as = options.get("run_as")
        if run_as is None:
            run_as = _task_container_user(
                ecs_client=ecs_client, cluster=cluster_name, task_arn=task_arn, container_name=container_name,
            )
        if run_as == "root":
            run_as = None  # Skip sudo — ECS Exec already enters as root.

        env_vars = self._parse_env_vars(options["env_vars"])

        wrapper = _build_wrapper_script(
            user_script_b64=base64.b64encode(script_text.encode("utf-8")).decode("ascii"),
            run_as=run_as,
            timeout=options.get("timeout"),
            cwd=options.get("cwd"),
            env_vars=env_vars,
        )

        raw_output = _run_execute_command_capture(
            session=session, cluster=cluster_name, task_id=task_id,
            container_name=container_name, wrapper_script=wrapper,
        )

        # Escape hatch when a probe's output looks wrong — inspect what SSM
        # actually returned before the sentinel parser split it.
        if os.environ.get("HUMR_APP_EXEC_DEBUG"):
            self.stderr.write("=== RAW OUTPUT ===")
            self.stderr.write(raw_output)
            self.stderr.write("=== END RAW ===")

        exit_code, stdout, stderr, stdout_truncated, stderr_truncated = _parse_wrapped_output(raw=raw_output)

        self._emit_output(
            output_format=options["format"],
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

        if not options["ignore_exit"] and exit_code is not None and exit_code != 0:
            sys.exit(exit_code)

    def _read_script(self, script_file: str | None) -> str:
        """Read the bash script from --script-file or stdin."""
        if script_file is not None:
            with open(script_file, "r", encoding="utf-8") as f:
                return f.read()
        if sys.stdin.isatty():
            raise CommandError(
                "No script provided. Pipe a script on stdin or pass --script-file. "
                "For an interactive shell, use `doh_app_shell` instead."
            )
        return sys.stdin.read()

    def _parse_env_vars(self, raw_pairs: list[str]) -> dict[str, str]:
        """Parse repeated --set KEY=VALUE flags into a dict."""
        result: dict[str, str] = {}
        for pair in raw_pairs:
            if "=" not in pair:
                raise CommandError(f"--set value must be KEY=VALUE (got {pair!r}).")
            key, _, value = pair.partition("=")
            result[key] = value
        return result

    def _emit_output(
        self,
        output_format: str,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        stdout_truncated: bool,
        stderr_truncated: bool,
    ) -> None:
        """Write the result to the command's stdout in the requested format."""
        if output_format == "json":
            payload = {
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }
            self.stdout.write(json.dumps(payload, indent=2))
            return

        # text format
        if stdout:
            self.stdout.write(stdout)
            if not stdout.endswith("\n"):
                self.stdout.write("")
        if stderr:
            self.stderr.write("--- stderr ---")
            self.stderr.write(stderr)
        self.stderr.write(f"--- exit_code: {exit_code}")
