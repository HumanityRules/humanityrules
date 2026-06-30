#!/opt/hermes/webui/venv/bin/python3
"""Seed bundled example webapps into an agent.

Invoked from webui.sh *before* the webapps process-compose daemon starts (like
the __admin bootstrap), so registration goes through
`webapps create --bootstrap-enabled`, which writes the YAML entry + Caddy route
directly without an RPC to the not-yet-running daemon.

The catalog is image-owned, baked at /opt/humr/webapps/examples/<slug>/ with an
example.json manifest per app. /opt/humr is re-synced from the image on every
boot (persistent-root-runner IMAGE_OWNED_DIRS), so the catalog always reflects
the deployed image; the installed copy under /workspace/webapps/projects/
(persistent) is what the user sees and edits.

The per-install marker at /workspace/webapps/.seeded/<slug> records both that the
app was installed AND its update policy — the marker's contents ARE the policy.
Keeping the policy here (per install, runtime) rather than in the manifest (per
image, build time) puts the freeze/refresh choice where the deciding fact lives:
whether *this* user has invested edits in *their* copy.

  - no marker     First install: copy source -> projects/<slug>, register, and
                  write the marker with the default policy ("refresh").
  - "refresh"     (default) On every deploy, overwrite the install from the baked
                  source, so improvements to a bundled example always land. A user
                  who deleted the app is not resurrected (a marker with no install
                  means they removed it, not that it's a first install).
  - "freeze"      Leave the install alone — preserve the user's edits. The user
                  (or the agent on their behalf) opts in by writing "freeze" into
                  the marker; writing "refresh" or deleting the marker resumes
                  updates.

Best-effort: a failure on one example is logged and skipped, never failing boot.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "/opt/humr/runtime/webapps")
from webapps_lib import SLUG_PATTERN, is_internal_slug  # noqa: E402

EXAMPLES_DIR = Path(os.environ.get("HUMR_WEBAPPS_EXAMPLES_DIR", "/opt/humr/webapps/examples"))
PROJECTS_DIR = Path("/workspace/webapps/projects")
SEEDED_DIR = Path("/workspace/webapps/.seeded")
WEBAPPS_CLI = "/opt/humr/bin/webapps"
PUBLIC_HOSTNAME_ENV = "HUMR_PUBLIC_HOSTNAME"

POLICY_REFRESH = "refresh"
POLICY_FREEZE = "freeze"
VALID_POLICIES = (POLICY_REFRESH, POLICY_FREEZE)
# Written into a fresh install's marker, and the fallback when a marker holds an
# unrecognized value. Refresh by default so improvements to a bundled example
# land on the next deploy; the user (or agent) opts a given install out by
# writing "freeze" into its marker.
DEFAULT_POLICY = POLICY_REFRESH

# decide_action outcomes.
SEED = "seed"
REFRESH = "refresh"
SKIP = "skip"

# Catalog metadata + build junk that must not land in the user's project copy.
COPY_IGNORE = shutil.ignore_patterns("example.json", "__pycache__", "*.pyc", ".DS_Store")


@dataclass(frozen=True)
class Example:
    slug: str
    command: str
    autostart: bool
    source_dir: Path


def log(msg: str) -> None:
    print(f"[seed-examples] {msg}", flush=True)


def load_manifest(example_dir: Path) -> Example | None:
    manifest = example_dir / "example.json"
    if not manifest.is_file():
        log(f"{example_dir.name}: no example.json; skipping")
        return None
    data = json.loads(manifest.read_text())

    slug = str(data.get("slug", "")).strip()
    if slug != example_dir.name:
        log(f"{example_dir.name}: manifest slug {slug!r} must match directory name; skipping")
        return None
    if is_internal_slug(slug) or not SLUG_PATTERN.match(slug):
        log(f"{slug!r}: not a valid user slug; skipping")
        return None

    command = str(data.get("command", "")).strip()
    if not command:
        log(f"{slug!r}: manifest has no command; skipping")
        return None

    return Example(
        slug=slug,
        command=command,
        autostart=bool(data.get("autostart", True)),
        source_dir=example_dir,
    )


def read_policy(slug: str) -> str | None:
    """The install's update policy from its marker, or None if not yet installed."""
    marker = SEEDED_DIR / slug
    if not marker.is_file():
        return None
    content = marker.read_text().strip().lower()
    if not content:
        return DEFAULT_POLICY
    if content in VALID_POLICIES:
        return content
    log(f"{slug}: unrecognized policy {content!r} in marker; treating as {DEFAULT_POLICY}")
    return DEFAULT_POLICY


def write_marker(slug: str, policy: str) -> None:
    SEEDED_DIR.mkdir(parents=True, exist_ok=True)
    (SEEDED_DIR / slug).write_text(f"{policy}\n")


def decide_action(policy: str | None, install_exists: bool) -> str:
    """Pure policy decision. See the module docstring for the rules."""
    if policy is None:
        return SEED
    if policy == POLICY_REFRESH and install_exists:
        return REFRESH
    return SKIP


def install_source(example: Example, dest: Path) -> None:
    """Copy the baked source into the project dir, clobbering any prior copy."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(example.source_dir, dest, ignore=COPY_IGNORE)


def register(example: Example, dest: Path) -> None:
    """Register the app with the webapps CLI, idempotently.

    --if-missing makes this a no-op when the entry already exists (the refresh
    path only overwrites source files; the persisted YAML entry stays). The
    command therefore stays as first registered, so a manifest command change
    needs a delete + reseed rather than a refresh. --bootstrap-enabled writes an
    enabled entry + route without a daemon RPC so the supervisor brings it up;
    omitting it registers the app stopped.
    """
    cmd = [
        WEBAPPS_CLI, "create", example.slug,
        "--if-missing",
        "--command", example.command,
        "--cwd", str(dest),
    ]
    if example.autostart:
        cmd.append("--bootstrap-enabled")
    subprocess.run(cmd, check=True)


def seed_one(example: Example) -> None:
    dest = PROJECTS_DIR / example.slug
    policy = read_policy(example.slug)
    action = decide_action(policy, dest.exists())

    if action == SKIP:
        log(f"{example.slug}: skip (policy={policy})")
        return

    log(f"{example.slug}: {'install' if action == SEED else 'refresh'}")
    install_source(example, dest)
    register(example, dest)
    if action == SEED:
        write_marker(example.slug, DEFAULT_POLICY)


def main() -> None:
    if not os.environ.get(PUBLIC_HOSTNAME_ENV):
        log(f"{PUBLIC_HOSTNAME_ENV} unset; skipping (subdomain routing unavailable).")
        return
    if not EXAMPLES_DIR.is_dir():
        log(f"no catalog at {EXAMPLES_DIR}; nothing to seed.")
        return

    for example_dir in sorted(EXAMPLES_DIR.iterdir()):
        if not example_dir.is_dir():
            continue
        try:
            example = load_manifest(example_dir)
            if example is not None:
                seed_one(example)
        except Exception as exc:  # best-effort: one bad example never fails boot
            log(f"{example_dir.name}: seeding failed ({exc}); skipping")


if __name__ == "__main__":
    main()
