"""Unit tests for shared content-addressed template images.

Covers the tree hash (determinism, content/rename sensitivity, symlink handling),
the ECR name derivation, and the ensure_template_image build-on-miss flow with
ecr_utils / stack deploy mocked out. The advisory lock is a no-op on SQLite, so
the Postgres locking path is not exercised here.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from humanityrules_app.services.infra_customer import template_images


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class TreeHashTests(SimpleTestCase):

    def test_identical_trees_hash_the_same(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for root in (Path(a), Path(b)):
                _write(root / "Dockerfile", b"FROM scratch\n")
                _write(root / "app" / "main.py", b"print('hi')\n")

            self.assertEqual(template_images.tree_hash(Path(a)), template_images.tree_hash(Path(b)))

    def test_hash_is_stable_across_repeated_calls(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root / "Dockerfile", b"FROM scratch\n")

            self.assertEqual(template_images.tree_hash(root), template_images.tree_hash(root))

    def test_content_change_changes_the_hash(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "app" / "main.py"
            _write(target, b"print('hi')\n")
            before = template_images.tree_hash(root)

            target.write_bytes(b"print('bye')\n")
            after = template_images.tree_hash(root)

        self.assertNotEqual(before, after)

    def test_rename_changes_the_hash_even_with_identical_content(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root / "a.py", b"same\n")
            before = template_images.tree_hash(root)

            (root / "a.py").rename(root / "b.py")
            after = template_images.tree_hash(root)

        self.assertNotEqual(before, after)

    def test_executable_bit_changes_the_hash(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            script = root / "run.sh"
            _write(script, b"#!/bin/sh\n")
            before = template_images.tree_hash(root)

            script.chmod(0o755)
            after = template_images.tree_hash(root)

        self.assertNotEqual(before, after)

    def test_symlink_is_hashed_by_target_without_following_it(self) -> None:
        # A dangling symlink must not raise (is_file() would follow and choke).
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root / "real.txt", b"data\n")
            (root / "link").symlink_to("does-not-exist")

            first = template_images.tree_hash(root)
            self.assertEqual(first, template_images.tree_hash(root))

    def test_symlink_target_change_changes_the_hash(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            link = root / "link"
            link.symlink_to("target-a")
            before = template_images.tree_hash(root)

            link.unlink()
            link.symlink_to("target-b")
            after = template_images.tree_hash(root)

        self.assertNotEqual(before, after)

    def test_hash_is_truncated_hex(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root / "Dockerfile", b"FROM scratch\n")
            digest = template_images.tree_hash(root)

        self.assertEqual(len(digest), template_images.TREE_HASH_LENGTH)
        int(digest, 16)  # raises if not hex


class NameDerivationTests(SimpleTestCase):

    def test_image_name_maps_underscores_to_dashes(self) -> None:
        self.assertEqual(template_images.image_name("hermes_agent"), "hermes-agent")
        self.assertEqual(template_images.image_name("policy_proxy"), "policy-proxy")

    def test_ecr_repo_name_is_per_env(self) -> None:
        self.assertEqual(
            template_images.ecr_repo_name(env_slug="prod", template_path="hermes_agent"),
            "humr/prod/hermes-agent",
        )

    def test_ecr_stack_name_is_per_repo(self) -> None:
        self.assertEqual(
            template_images.ecr_stack_name(env_slug="prod", template_path="policy_proxy"),
            "humr-prod-policy-proxy-ecr",
        )


class EnsureTemplateImageTests(SimpleTestCase):
    """The build-on-miss flow with ecr_utils and the ECR stack deploy mocked out."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repos_dir = Path(self._tmp.name)
        (self.repos_dir / "hermes_agent").mkdir()
        (self.repos_dir / "hermes_agent" / "Dockerfile").write_bytes(b"FROM scratch\n")
        self.session = MagicMock()
        self.addCleanup(self._tmp.cleanup)

    def _ensure(self) -> str:
        return template_images.ensure_template_image(
            session=self.session,
            account_id="123456789012",
            region="us-east-1",
            env_slug="staging",
            template_path="hermes_agent",
            build_id="attempt-1",
        )

    def test_existing_tag_skips_stack_deploy_and_build(self) -> None:
        with override_settings(TEMPLATE_REPOS_DIR=self.repos_dir):
            expected = template_images.tree_hash(self.repos_dir / "hermes_agent")
            with (
                patch.object(template_images.ecr_utils, "image_tag_exists", return_value=True) as tag_exists,
                patch.object(template_images, "_deploy_ecr_stack") as deploy_stack,
                patch.object(template_images.ecr_utils, "build_and_push_docker_image") as build,
            ):
                tag = self._ensure()

        self.assertEqual(tag, expected)
        tag_exists.assert_called_once()
        deploy_stack.assert_not_called()
        build.assert_not_called()

    def test_missing_tag_deploys_stack_and_builds(self) -> None:
        with override_settings(TEMPLATE_REPOS_DIR=self.repos_dir):
            expected = template_images.tree_hash(self.repos_dir / "hermes_agent")
            with (
                patch.object(template_images.ecr_utils, "image_tag_exists", return_value=False),
                patch.object(template_images, "_deploy_ecr_stack") as deploy_stack,
                patch.object(
                    template_images.ecr_utils, "build_and_push_docker_image",
                    return_value="123456789012.dkr.ecr.us-east-1.amazonaws.com/humr/staging/hermes-agent:tag",
                ) as build,
            ):
                tag = self._ensure()

        self.assertEqual(tag, expected)
        deploy_stack.assert_called_once()
        build.assert_called_once()
        self.assertEqual(build.call_args.kwargs["build_id"], "attempt-1")
        self.assertEqual(build.call_args.kwargs["image_tag"], expected)
        self.assertEqual(build.call_args.kwargs["ecr_repo_name"], "humr/staging/hermes-agent")

    def test_build_failure_raises_template_image_build_error(self) -> None:
        with override_settings(TEMPLATE_REPOS_DIR=self.repos_dir):
            with (
                patch.object(template_images.ecr_utils, "image_tag_exists", return_value=False),
                patch.object(template_images, "_deploy_ecr_stack"),
                patch.object(template_images.ecr_utils, "build_and_push_docker_image", return_value=None),
            ):
                with self.assertRaises(template_images.TemplateImageBuildError):
                    self._ensure()

    def test_missing_tree_raises_template_image_build_error(self) -> None:
        with override_settings(TEMPLATE_REPOS_DIR=self.repos_dir):
            with self.assertRaises(template_images.TemplateImageBuildError):
                template_images.ensure_template_image(
                    session=self.session,
                    account_id="123456789012",
                    region="us-east-1",
                    env_slug="staging",
                    template_path="does_not_exist",
                    build_id="attempt-1",
                )
