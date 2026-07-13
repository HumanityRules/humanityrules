"""Tests for HumR-owned Hermes skill retirement reconciliation."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest


def _hermes_agent_dir() -> pathlib.Path:
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    return repo_root / "template_repos" / "hermes_agent"


def _load_reconciler_module() -> types.ModuleType:
    script_path = _hermes_agent_dir() / "humr_runtime" / "reconcile_platform_skills.py"
    spec = importlib.util.spec_from_file_location(name="reconcile_platform_skills_under_test", location=str(script_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules["reconcile_platform_skills_under_test"] = module
    spec.loader.exec_module(module)
    return module


reconciler = _load_reconciler_module()


def _write_skill(skills_dir: pathlib.Path, relative_dir: str, name: str, body: str) -> pathlib.Path:
    skill_dir = skills_dir / relative_dir
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return skill_dir


def _reconcile(root: pathlib.Path) -> tuple[pathlib.Path, ...]:
    skills_dir = root / "home" / "skills"
    return reconciler.reconcile_platform_skills(
        skills_dir=skills_dir,
        bundled_skills_dir=root / "image" / "skills",
        manifest_path=skills_dir / ".bundled_manifest",
        archive_dir=skills_dir / ".archive" / "humr-retired",
    )


class TestHermesSkillReconciliation(unittest.TestCase):

    def test_archives_manifest_skill_removed_from_image_and_preserves_user_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            bundled_dir = root / "image" / "skills"
            skills_dir = root / "home" / "skills"
            _write_skill(skills_dir=bundled_dir, relative_dir="github/github-issues", name="github-issues", body="current")
            retired = _write_skill(
                skills_dir=skills_dir,
                relative_dir="data-science/jupyter-live-kernel",
                name="renamed-by-user",
                body="user-modified content",
            )
            user_skill = _write_skill(
                skills_dir=skills_dir,
                relative_dir="personal/my-skill",
                name="my-skill",
                body="user-created content",
            )
            (skills_dir / ".bundled_manifest").write_text(
                "github-issues:current-hash\njupyter-live-kernel:old-hash\n",
                encoding="utf-8",
            )

            archived_paths = _reconcile(root=root)

            archived = skills_dir / ".archive" / "humr-retired" / "data-science" / "jupyter-live-kernel"
            self.assertEqual(archived_paths, (archived,))
            self.assertFalse(retired.exists())
            self.assertTrue((archived / "SKILL.md").is_file())
            self.assertTrue(user_skill.exists())
            self.assertEqual(_reconcile(root=root), ())

    def test_current_image_skill_is_not_archived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            bundled_dir = root / "image" / "skills"
            skills_dir = root / "home" / "skills"
            _write_skill(skills_dir=bundled_dir, relative_dir="github/github-auth", name="github-auth", body="current")
            active = _write_skill(skills_dir=skills_dir, relative_dir="github/github-auth", name="github-auth", body="modified")
            (skills_dir / ".bundled_manifest").write_text("github-auth:old-hash\n", encoding="utf-8")

            self.assertEqual(_reconcile(root=root), ())
            self.assertTrue(active.exists())

    def test_missing_manifest_archives_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            bundled_dir = root / "image" / "skills"
            skills_dir = root / "home" / "skills"
            _write_skill(skills_dir=bundled_dir, relative_dir="research/arxiv", name="arxiv", body="current")
            user_skill = _write_skill(skills_dir=skills_dir, relative_dir="personal/my-skill", name="my-skill", body="user")

            self.assertEqual(_reconcile(root=root), ())
            self.assertTrue(user_skill.exists())

    def test_missing_image_skill_tree_fails_without_archiving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skills_dir = root / "home" / "skills"
            active = _write_skill(
                skills_dir=skills_dir,
                relative_dir="data-science/jupyter-live-kernel",
                name="jupyter-live-kernel",
                body="must remain when image source is unavailable",
            )
            (skills_dir / ".bundled_manifest").write_text("jupyter-live-kernel:old-hash\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "skills directory does not exist"):
                _reconcile(root=root)

            self.assertTrue(active.exists())

    def test_webui_reconciles_before_any_hermes_process_starts(self) -> None:
        script = (_hermes_agent_dir() / "humr_runtime" / "webui.sh").read_text(encoding="utf-8")

        reconcile_index = script.index("    reconcile_platform_skills\n")
        gateway_index = script.index("    bootstrap_gateway_process\n")
        webui_index = script.index("    bootstrap_webui_process\n")

        self.assertLess(reconcile_index, gateway_index)
        self.assertLess(reconcile_index, webui_index)


if __name__ == "__main__":
    unittest.main()
