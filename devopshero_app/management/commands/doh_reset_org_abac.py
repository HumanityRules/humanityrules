"""
Full factory reset of ABAC Policy rows for one organization.

Deletes all Policy records for the org, re-runs bootstrap_organization (seed
policies), then create_default_app_policy for each app (non-sidecar apps get
Default open-access policies).

Usage:
    uv run manage.py doh_reset_org_abac --org course-hero
    uv run manage.py doh_reset_org_abac --org "Course Hero" --admin-email user@example.com

Production:
    ./prod_manage.sh doh_reset_org_abac --org course-hero
"""

from django.core.management.base import BaseCommand, CommandError

from devopshero_app.models import App, Organization, OrganizationMembership, Policy, User
from devopshero_app.services import abac


def _resolve_organization(org_identifier: str) -> Organization:
    """Resolve an Organization by slug or exact name."""
    org = Organization.objects.filter(slug=org_identifier).first() or Organization.objects.filter(
        name=org_identifier,
    ).first()
    if org is None:
        raise CommandError(
            f"No organization matching {org_identifier!r} found (tried slug and name).",
        )
    return org


def _resolve_admin_user(organization: Organization, admin_email: str | None) -> User:
    """Pick an admin User for bootstrap_organization: explicit email or first org Admin membership."""
    if admin_email is not None:
        user = User.objects.filter(email__iexact=admin_email).first()
        if user is None:
            raise CommandError(f"No user with email {admin_email!r}.")
        if not OrganizationMembership.objects.filter(organization=organization, user=user).exists():
            raise CommandError(
                f"User {admin_email!r} is not a member of organization {organization.slug!r}.",
            )
        return user

    membership = (
        OrganizationMembership.objects.filter(
            organization=organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        .select_related("user")
        .first()
    )
    if membership is None:
        raise CommandError(
            f"No Admin membership found for organization {organization.slug!r}. "
            "Pass --admin-email for a user who belongs to this org.",
        )
    return membership.user


class Command(BaseCommand):
    help = "Full factory reset of ABAC policies for an organization (slug or name)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--org",
            required=True,
            help="Organization slug or name (e.g. course-hero or 'Course Hero')",
        )
        parser.add_argument(
            "--admin-email",
            default=None,
            help="Member of the org to use for bootstrap (default: first org Admin membership)",
        )

    def handle(self, *args, **options) -> None:
        org_identifier: str = options["org"]
        admin_email: str | None = options["admin_email"]

        org = _resolve_organization(org_identifier=org_identifier)
        admin_user = _resolve_admin_user(organization=org, admin_email=admin_email)

        policy_qs = Policy.objects.filter(organization=org)
        policy_count = policy_qs.count()
        deleted_total, _ = policy_qs.delete()
        self.stdout.write(
            f"Deleted {policy_count} policy row(s) ({deleted_total} total object(s) including cascades, if any).",
        )

        abac.bootstrap_organization(organization=org, admin_user=admin_user)
        self.stdout.write(self.style.SUCCESS("Re-ran bootstrap_organization (seed policies)."))

        apps = App.objects.filter(organization=org).select_related("source_template")
        n_apps = apps.count()
        for app in apps:
            abac.create_default_app_policy(app=app)
        self.stdout.write(self.style.SUCCESS(f"Processed {n_apps} app(s) for default app policies."))

        self.stdout.write(
            self.style.SUCCESS(
                f"ABAC factory reset complete for organization {org.slug!r} (admin bootstrap user: {admin_user.email}).",
            ),
        )
