#!/usr/bin/env python3
"""Apply DOH's hermes-agent patches to an installed hermes-agent tree.

Runs on every container boot (from entrypoint.sh) against the EFS-backed
hermes-agent copy in $HERMES_DIR/hermes-agent. This lets a new image ship
updated patches that apply to already-deployed EFS volumes.

Steps, in order:

  1. Copy everything under ``overlay/`` into the target tree (DOH-owned files
     that don't exist upstream). No overlay files are shipped today; the
     mechanism stays in place so future DOH-owned additions can drop in
     without restructuring the pipeline.

  2. Apply each ``NN-*.patch`` in numeric order with ``patch -p1 -N --forward``
     relative to the target tree. ``-N`` (forward) means already-applied
     patches are a no-op, not an error — this is what gives us idempotency
     across boots and across upstream versions that already contain the fix.

Exit codes:
  0 — all patches applied or already present
  1 — a patch failed to apply (upstream anchor drift, corrupt tree, etc.)

Usage:
  python3 apply.py <hermes-agent-dir>
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


PATCHES_DIR = Path(__file__).resolve().parent
OVERLAY_DIR = PATCHES_DIR / "overlay"


def log(msg: str) -> None:
    print(f"[patches] {msg}", flush=True)


def copy_overlay(target: Path) -> None:
    if not OVERLAY_DIR.is_dir():
        return
    for src in OVERLAY_DIR.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(OVERLAY_DIR)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        log(f"overlay: {rel}")


def apply_patch(target: Path, patch_file: Path) -> bool:
    """Apply one patch. Returns True on success (including already-applied)."""

    # Dry-run first so we can distinguish "already applied" from real failure
    # without touching files. ``patch -N --dry-run`` exits non-zero for both;
    # we inspect stdout to tell them apart.
    dry = subprocess.run(
        ["patch", "-p1", "-N", "--dry-run", "-i", str(patch_file)],
        cwd=target,
        capture_output=True,
        text=True,
    )
    if dry.returncode == 0:
        real = subprocess.run(
            ["patch", "-p1", "-N", "-i", str(patch_file)],
            cwd=target,
            capture_output=True,
            text=True,
        )
        if real.returncode == 0:
            log(f"applied: {patch_file.name}")
            return True
        log(f"FAILED (real apply): {patch_file.name}")
        log(real.stdout.strip())
        log(real.stderr.strip())
        return False

    # Non-zero dry-run with -N means either (a) already applied — GNU patch
    # emits "Ignoring previously applied (or reversed) patch" and exits 1 even
    # though nothing is wrong — or (b) a real mismatch, signalled by "hunks
    # FAILED". Distinguish by looking for the FAILED marker.
    combined = (dry.stdout or "") + (dry.stderr or "")
    if "FAILED" not in combined and (
        "previously applied" in combined.lower()
        or "already applied" in combined.lower()
    ):
        log(f"already applied: {patch_file.name}")
        return True

    log(f"FAILED (dry-run): {patch_file.name}")
    log(combined.strip())
    return False


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: apply.py <hermes-agent-dir>", file=sys.stderr)
        return 2

    target = Path(argv[1]).resolve()
    if not target.is_dir():
        print(f"target not a directory: {target}", file=sys.stderr)
        return 2

    log(f"target: {target}")
    copy_overlay(target)

    patches = sorted(p for p in PATCHES_DIR.glob("*.patch"))
    if not patches:
        log("no patches found")
        return 0

    failed = [p for p in patches if not apply_patch(target, p)]
    if failed:
        log(f"{len(failed)} patch(es) failed: {[p.name for p in failed]}")
        return 1

    log(f"all {len(patches)} patch(es) OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
