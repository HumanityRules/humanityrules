"""
Build and push a pre-built Docker image into a customer's per-env ECR.

AppTemplate containers with image_source="prebuilt" reference an image at
doh/{env_slug}/{ecr_repo}:{version}. This command is the generic operator
entry point that creates the ECR repo (if missing), builds a Dockerfile from
--source-dir, and pushes the result into that repo.

Usage:
    uv run manage.py doh_build_prebuilt_image \\
        --account "Humanity Rules Sandbox" \\
        --env default \\
        --source-dir ../sidecar-mcp \\
        --ecr-repo sidecar-mcp \\
        --tag 0.1.0
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from devopshero_app.services.infra_customer import ecr_utils

from ._aws_account_resolver import add_aws_target_args, resolve_aws_target


IMAGE_RETENTION_COUNT = 20


class Command(BaseCommand):
    help = "Build and push a pre-built image into the customer's per-env ECR"

    def add_arguments(self, parser):
        add_aws_target_args(parser=parser, env_default=None)
        parser.add_argument(
            "--source-dir",
            required=True,
            help="Path to the directory containing the Dockerfile (absolute or relative to repo root)",
        )
        parser.add_argument(
            "--ecr-repo",
            required=True,
            help="Repo name within the doh/{env_slug}/ namespace (e.g. 'sidecar-mcp')",
        )
        parser.add_argument(
            "--tag",
            required=True,
            help="Image tag, used as the ECR image tag (e.g. '0.1.0')",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Overwrite the tag if it already exists in ECR (default: fail)",
        )

    def handle(self, *args, **options):
        source_dir = Path(options["source_dir"]).expanduser().resolve()
        if not source_dir.is_dir():
            raise CommandError(f"--source-dir '{source_dir}' is not a directory")
        if not (source_dir / "Dockerfile").is_file():
            raise CommandError(f"No Dockerfile found at '{source_dir / 'Dockerfile'}'")

        ecr_repo_short = options["ecr_repo"]
        version = options["tag"]

        target = resolve_aws_target(options=options)
        session = target.session
        env_slug = target.env_slug
        account_id = target.aws_account_id
        region = target.aws_region

        repository_name = f"doh/{env_slug}/{ecr_repo_short}"
        ecr_client = session.client("ecr")

        self._ensure_repo_exists(
            ecr_client=ecr_client,
            repository_name=repository_name,
            max_image_count=IMAGE_RETENTION_COUNT,
        )

        if not options["overwrite"] and self._tag_exists(
            ecr_client=ecr_client, repository_name=repository_name, tag=version,
        ):
            raise CommandError(
                f"Image {repository_name}:{version} already exists in ECR. "
                f"Re-run with --overwrite to force, or pick a different --tag."
            )

        image_uri = ecr_utils.build_and_push_docker_image(
            session=session,
            account_id=account_id,
            region=region,
            env_slug=env_slug,
            app_name=ecr_repo_short,
            ecr_repo_name=repository_name,
            app_source_path=source_dir,
            image_tag=version,
        )

        if not image_uri:
            raise CommandError("Docker build or push failed; see logs above.")

        self.stdout.write(self.style.SUCCESS(f"Pushed {image_uri}"))

    def _ensure_repo_exists(self, ecr_client, repository_name: str, max_image_count: int) -> None:
        """Create the ECR repo (with lifecycle + scan-on-push) if it doesn't already exist."""
        from botocore.exceptions import ClientError

        try:
            ecr_client.describe_repositories(repositoryNames=[repository_name])
            self.stdout.write(f"ECR repo '{repository_name}' already exists")
            return
        except ClientError as e:
            if e.response["Error"]["Code"] != "RepositoryNotFoundException":
                raise

        self.stdout.write(f"Creating ECR repo '{repository_name}'")
        ecr_client.create_repository(
            repositoryName=repository_name,
            imageScanningConfiguration={"scanOnPush": True},
            tags=[{"Key": "ManagedBy", "Value": "doh_build_prebuilt_image"}],
        )
        ecr_utils.apply_keep_last_n_lifecycle_policy(
            ecr_client=ecr_client,
            repository_name=repository_name,
            max_image_count=max_image_count,
        )

    def _tag_exists(self, ecr_client, repository_name: str, tag: str) -> bool:
        """Return True if an image with the given tag already exists in the repo."""
        from botocore.exceptions import ClientError

        try:
            resp = ecr_client.describe_images(
                repositoryName=repository_name,
                imageIds=[{"imageTag": tag}],
            )
            return bool(resp.get("imageDetails"))
        except ClientError as e:
            if e.response["Error"]["Code"] in ("ImageNotFoundException", "RepositoryNotFoundException"):
                return False
            raise
