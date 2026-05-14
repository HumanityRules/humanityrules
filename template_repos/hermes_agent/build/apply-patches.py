#!/usr/bin/env python3
"""Apply a directory of patch files to a target tree."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def log(message: str) -> None:
    print(f"[apply-patches] {message}", flush=True)


def apply_patch(patch_file: Path, target_dir: Path) -> bool:
    """Apply one patch file to the target tree."""

    log(f"applying {patch_file.name} to {target_dir}")
    result = subprocess.run(
        ["patch", "-p1", "-F", "0", "-d", str(target_dir), "-i", str(patch_file)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        if result.stdout.strip():
            log(result.stdout.strip())
        return True

    log(f"FAILED: {patch_file.name}")
    if result.stdout.strip():
        log(result.stdout.strip())
    if result.stderr.strip():
        log(result.stderr.strip())
    return False


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: apply-patches.py <patch-dir> <target-dir>", file=sys.stderr)
        return 2

    patch_dir = Path(argv[1]).resolve()
    target_dir = Path(argv[2]).resolve()

    if not patch_dir.is_dir():
        print(f"patch directory not found: {patch_dir}", file=sys.stderr)
        return 2
    if not target_dir.is_dir():
        print(f"target directory not found: {target_dir}", file=sys.stderr)
        return 2

    patches = sorted(patch_dir.glob("*.patch"))
    if not patches:
        log(f"no patches found in {patch_dir}")
        return 0

    failed = [patch_file for patch_file in patches if not apply_patch(patch_file=patch_file, target_dir=target_dir)]
    if failed:
        log(f"{len(failed)} patch(es) failed: {[patch_file.name for patch_file in failed]}")
        return 1

    log(f"all {len(patches)} patch(es) OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
