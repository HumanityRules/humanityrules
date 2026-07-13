"""Archive platform skills that disappeared from the current HumR image.

Hermes copies image-bundled skills into its persistent skills directory and
records their names in ``.bundled_manifest``. When a bundled skill disappears
from the image, Hermes removes its manifest record but leaves its persistent
directory in place, including any user modifications. Hermes loads skills by
scanning those directories, not by consulting the manifest, so the retired
skill remains available.

This runs before Hermes removes that ownership record. A name present in the
manifest but absent from the current image means HumR retired that skill, so
its persistent directory is moved to ``.archive/humr-retired``. Skills never
recorded in the manifest are user-owned and remain untouched. Hermes then
performs its normal sync and removes the obsolete manifest records.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


BUNDLED_SKILLS_DIR = Path("/opt/hermes/agent/skills")
HERMES_MANIFEST_FILENAME = ".bundled_manifest"
HUMR_ARCHIVE_DIR = Path(".archive/humr-retired")
IGNORED_DIR_NAMES = frozenset({".archive", ".git", ".hub", "assets", "references", "scripts", "templates"})


@dataclass(frozen=True)
class SkillRecord:
    directory: Path
    names: frozenset[str]


def _skill_name(skill_path: Path) -> str:
    """Read the frontmatter name, falling back to the directory name."""
    fallback = skill_path.parent.name
    try:
        lines = skill_path.read_text(encoding="utf-8").splitlines()
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
        frontmatter = yaml.safe_load("\n".join(lines[1:end])) if lines and lines[0].strip() == "---" else None
    except (OSError, UnicodeDecodeError, StopIteration, yaml.YAMLError):
        return fallback
    name = frontmatter.get("name") if isinstance(frontmatter, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else fallback


def discover_skills(skills_dir: Path, required: bool) -> tuple[SkillRecord, ...]:
    """Discover active skill packages without importing Hermes."""
    if not skills_dir.is_dir():
        if required:
            raise RuntimeError(f"skills directory does not exist: {skills_dir}")
        return ()
    records = []
    for skill_path in sorted(skills_dir.rglob("SKILL.md")):
        relative = skill_path.relative_to(skills_dir)
        if any(part in IGNORED_DIR_NAMES for part in relative.parts[:-1]):
            continue
        records.append(
            SkillRecord(
                directory=skill_path.parent,
                names=frozenset({_skill_name(skill_path=skill_path), skill_path.parent.name}),
            )
        )
    return tuple(records)


def read_manifest_names(manifest_path: Path) -> set[str]:
    """Read names from Hermes's name:hash bundled manifest."""
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    except OSError as exc:
        raise RuntimeError(f"cannot read Hermes skill manifest {manifest_path}: {exc}") from exc
    return {line.partition(":")[0].strip() for line in lines if line.partition(":")[0].strip()}


def archive_skill(skill: SkillRecord, skills_dir: Path, archive_dir: Path) -> Path:
    """Move one retired skill under Hermes's excluded archive directory."""
    source = skill.directory.resolve()
    try:
        relative = source.relative_to(skills_dir.resolve())
    except ValueError as exc:
        raise RuntimeError(f"refusing to archive skill outside {skills_dir}: {skill.directory}") from exc
    destination = archive_dir / relative
    index = 2
    while destination.exists():
        destination = (archive_dir / relative).with_name(f"{relative.name}.{index}")
        index += 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src=str(source), dst=str(destination))
    return destination


def reconcile_platform_skills(skills_dir: Path, bundled_skills_dir: Path, manifest_path: Path, archive_dir: Path) -> tuple[Path, ...]:
    """Archive manifest-owned skills absent from the current image."""
    bundled_names = {name for skill in discover_skills(skills_dir=bundled_skills_dir, required=True) for name in skill.names}
    retired_names = read_manifest_names(manifest_path=manifest_path) - bundled_names
    archived = []
    for skill in discover_skills(skills_dir=skills_dir, required=False):
        if skill.names & retired_names:
            archived.append(archive_skill(skill=skill, skills_dir=skills_dir, archive_dir=archive_dir))
    return tuple(archived)


def main() -> int:
    """Reconcile skills before Hermes clears stale manifest entries."""
    hermes_home = os.environ.get("HERMES_HOME")
    if not hermes_home:
        raise RuntimeError("HERMES_HOME must be set")
    skills_dir = Path(hermes_home).expanduser() / "skills"
    archived = reconcile_platform_skills(
        skills_dir=skills_dir,
        bundled_skills_dir=BUNDLED_SKILLS_DIR,
        manifest_path=skills_dir / HERMES_MANIFEST_FILENAME,
        archive_dir=skills_dir / HUMR_ARCHIVE_DIR,
    )
    for archived_path in archived:
        print(f"[skill-reconcile] archived retired bundled skill at {archived_path}")
    print(f"[skill-reconcile] archived={len(archived)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[skill-reconcile] failed: {exc}", file=sys.stderr)
        sys.exit(1)
