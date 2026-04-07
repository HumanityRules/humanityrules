"""
Seed test users with realistic names and assign them to groups (e.g. 100-person company).

Users get org membership and 1–4 group memberships at random. Use --reset to remove and re-seed.
Seed users are identified by their email domain for --reset.

Usage:
    uv run manage.py seed_test_users
    uv run manage.py seed_test_users --count=100 --org=meridian-systems
    uv run manage.py seed_test_users --email-domain=meridiansystems.com --org=meridian-systems
    uv run manage.py seed_test_users --reset
"""

import random

from django.core.management.base import BaseCommand, CommandError

from django.db.models import Q

from devopshero_app.models import (
    Group,
    GroupMembership,
    Organization,
    OrganizationMembership,
    User,
)

DEFAULT_EMAIL_DOMAIN = "humanityrules.io"
# Legacy: old seed command used this username prefix
SEED_USERNAME_PREFIX_LEGACY = "seed_user_"

FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda",
    "David", "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
    "Thomas", "Sarah", "Charles", "Karen", "Christopher", "Lisa", "Daniel", "Nancy",
    "Matthew", "Betty", "Anthony", "Margaret", "Mark", "Sandra", "Donald", "Ashley",
    "Steven", "Kimberly", "Paul", "Emily", "Andrew", "Donna", "Joshua", "Michelle",
    "Kenneth", "Carol", "Kevin", "Amanda", "Brian", "Dorothy", "George", "Melissa",
    "Timothy", "Deborah", "Ronald", "Stephanie", "Edward", "Rebecca", "Jason", "Sharon",
    "Jeffrey", "Laura", "Ryan", "Cynthia", "Jacob", "Kathleen", "Gary", "Amy",
    "Nicholas", "Angela", "Eric", "Shirley", "Jonathan", "Anna", "Stephen", "Brenda",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
    "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill",
    "Flores", "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell",
    "Mitchell", "Carter", "Roberts", "Chen", "Kim", "Patel", "Singh", "Murphy",
]


class Command(BaseCommand):
    help = "Seed test users (e.g. 100) with org membership and random group assignments"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--org",
            type=str,
            default=None,
            help="Organization slug. If omitted, uses the first organization.",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=100,
            help="Number of users to create (default 100).",
        )
        parser.add_argument(
            "--email-domain",
            type=str,
            default=DEFAULT_EMAIL_DOMAIN,
            help=f"Email domain for generated users (default: {DEFAULT_EMAIL_DOMAIN}).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing seed users for the org before creating.",
        )

    def handle(self, *args, **options) -> None:
        org_slug = options.get("org")
        if org_slug:
            try:
                org = Organization.objects.get(slug=org_slug)
            except Organization.DoesNotExist:
                raise CommandError(f"Organization with slug '{org_slug}' not found.")
        else:
            org = Organization.objects.order_by("created_at").first()
            if org is None:
                raise CommandError("No organization found. Create one or pass --org=slug.")

        count = options.get("count", 100)
        email_domain = options.get("email_domain", DEFAULT_EMAIL_DOMAIN)
        if count < 1 or count > 1000:
            raise CommandError("--count must be between 1 and 1000.")

        if options.get("reset"):
            self._delete_seed_users(org=org, email_domain=email_domain)

        groups = list(Group.objects.filter(organization=org))
        if not groups:
            raise CommandError(
                f"No groups found for org '{org.slug}'. Run seed_test_groups first."
            )

        def _base_username(first: str, last: str) -> str:
            return f"{first.lower()}.{last.lower()}"

        seen_usernames: set[str] = set()
        name_pairs = [
            (random.choice(FIRST_NAMES), random.choice(LAST_NAMES))
            for _ in range(count)
        ]

        created = 0
        for i, (first, last) in enumerate(name_pairs):
            base = _base_username(first, last)
            username = base
            suffix = 2
            while username in seen_usernames or User.objects.filter(username=username).exists():
                username = f"{base}.{suffix}"
                suffix += 1
            seen_usernames.add(username)
            email = f"{username}@{email_domain}"

            user = User.objects.create_user(
                username=username,
                email=email,
                password="testpass",
                first_name=first,
                last_name=last,
                current_organization=org,
            )
            created += 1
            if i < 2:
                role = OrganizationMembership.Role.ADMIN
            elif i < 7:
                role = OrganizationMembership.Role.VIEWER
            else:
                role = OrganizationMembership.Role.MEMBER
            OrganizationMembership.objects.create(
                user=user,
                organization=org,
                role=role,
            )
            num_groups = random.randint(1, min(4, len(groups)))
            chosen = random.sample(groups, num_groups)
            for group in chosen:
                GroupMembership.objects.get_or_create(group=group, user=user)

        total = User.objects.filter(
            current_organization=org,
            email__iendswith=f"@{email_domain}",
        ).count()
        self.stdout.write(
            self.style.SUCCESS(f"Done. Created {created} new users (total @{email_domain} in org: {total}).")
        )

    def _delete_seed_users(self, org: Organization, email_domain: str) -> None:
        q = Q(current_organization=org) & (
            Q(email__iendswith=f"@{email_domain}")
            | Q(username__startswith=SEED_USERNAME_PREFIX_LEGACY)
        )
        to_delete = User.objects.filter(q)
        count = to_delete.count()
        to_delete.delete()
        self.stdout.write(f"  Deleted {count} seed user(s).")
