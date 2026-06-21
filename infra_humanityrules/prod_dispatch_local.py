"""
Local-exec dispatcher invoked by prod_manage.sh.

Some operator commands fundamentally need to run on the operator's local
machine (Docker daemon for `docker build`, interactive stdin for shells, large
local source trees) but they want prod's Django DB as the source of truth for
AWS-account + Environment metadata.

This dispatcher:

1. Parses --account, --env (and optional --org) from the original command.
2. Calls `./prod_manage.sh humr_query ... --format json` to fetch the four AWS
   values from prod's DB (account_id, external_id, region, slug).
3. Re-invokes the same Django management command LOCALLY via `uv run manage.py`,
   replacing --account/--env/--org with the equivalent raw-mode args
   --aws-account-id/--aws-external-id/--aws-region/--env-slug.

Result: the operator types `./prod_manage.sh <cmd> --account ... --env ...`
uniformly; this dispatcher hides the local-vs-ECS execution split.

Add commands to LOCAL_EXEC_COMMANDS in prod_manage.sh as new ones need this
treatment.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PROD_MANAGE_SH = SCRIPT_DIR / "prod_manage.sh"


def main() -> None:
    """Entry point: parse argv, resolve target via prod, exec local manage.py."""
    if len(sys.argv) < 2:
        print("Usage: prod_dispatch_local.py <command> [args...]", file=sys.stderr)
        sys.exit(2)

    command = sys.argv[1]
    rest = sys.argv[2:]

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--account", required=True)
    parser.add_argument("--env", required=True)
    parser.add_argument("--org", default=None)
    try:
        known, passthrough = parser.parse_known_args(rest)
    except SystemExit:
        print(
            "[prod_dispatch] error: this command requires --account and --env "
            "to resolve the target via prod's DB.",
            file=sys.stderr,
        )
        sys.exit(2)

    print(
        f"[prod_dispatch] {command}: resolving --account={known.account!r} "
        f"--env={known.env!r} via prod_manage.sh humr_query ...",
        file=sys.stderr,
    )
    target = _resolve_target(account=known.account, org=known.org, env=known.env)

    print(
        f"[prod_dispatch] resolved → account_id={target['aws_account_id']} "
        f"region={target['aws_region']} env_slug={target['env_slug']!r}",
        file=sys.stderr,
    )

    cmd = [
        "uv", "run", "manage.py", command,
        "--aws-account-id", target["aws_account_id"],
        "--aws-external-id", target["external_id"],
        "--aws-region", target["aws_region"],
        "--env-slug", target["env_slug"],
        *passthrough,
    ]

    print(f"[prod_dispatch] exec (cwd={REPO_ROOT}): {' '.join(cmd)}", file=sys.stderr)
    os.chdir(REPO_ROOT)
    os.execvp(cmd[0], cmd)


def _resolve_target(account: str, org: str | None, env: str) -> dict:
    """Fetch the four AWS target values from prod's DB via prod_manage.sh humr_query."""
    if account.isdigit() and len(account) == 12:
        acct_filters = [f"aws_account_id={account}"]
    else:
        acct_filters = [f"name={account}"]
    if org:
        acct_filters.append(f"organization__slug={org}")

    acct_rows = _humr_query(
        model="AWSAccount",
        fields=["aws_account_id", "external_id", "name"],
        filters=acct_filters,
    )
    if len(acct_rows) == 0:
        raise SystemExit(f"[prod_dispatch] no AWSAccount in prod matching {acct_filters}")
    if len(acct_rows) > 1:
        names = ", ".join(r["name"] for r in acct_rows)
        raise SystemExit(
            f"[prod_dispatch] multiple AWSAccounts in prod matching {acct_filters}: {names}. "
            "Use --org to disambiguate or pass the 12-digit account ID."
        )
    aws_account_id = acct_rows[0]["aws_account_id"]
    external_id = acct_rows[0]["external_id"]

    env_rows = _humr_query(
        model="Environment",
        fields=["slug", "aws_region"],
        filters=[f"aws_account__aws_account_id={aws_account_id}", f"slug={env}"],
    )
    if not env_rows:
        raise SystemExit(
            f"[prod_dispatch] no Environment in prod with slug={env!r} for account {aws_account_id}."
        )

    return {
        "aws_account_id": aws_account_id,
        "external_id": external_id,
        "aws_region": env_rows[0]["aws_region"],
        "env_slug": env_rows[0]["slug"],
    }


def _humr_query(model: str, fields: list[str], filters: list[str]) -> list[dict]:
    """Run prod_manage.sh humr_query --format json and parse the embedded JSON line."""
    cmd = [str(PROD_MANAGE_SH), "humr_query", model, *fields, "--format", "json"]
    for f in filters:
        cmd.extend(["--filter", f])

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(
            f"[prod_dispatch] prod_manage.sh exited {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            return json.loads(stripped)

    raise SystemExit(
        "[prod_dispatch] could not find JSON output in prod_manage.sh response.\n"
        f"Full stdout:\n{result.stdout}"
    )


if __name__ == "__main__":
    main()
