"""
Seed 10 test groups with attributes for local/testing use.

Each group gets a random number of attributes (2–5) for more realistic variety.

Usage:
    uv run manage.py seed_test_groups
    uv run manage.py seed_test_groups --org=meridian-systems
    uv run manage.py seed_test_groups --reset   # drop test groups and recreate with random attributes
"""

import random

from django.core.management.base import BaseCommand, CommandError

from devopshero_app.models import Group, GroupAttribute, Organization


# 10 groups: name, description, and pool of (key, value) attributes (2–5 are chosen at random per group)
TEST_GROUPS = [
    {
        "name": "Engineering",
        "description": "Software and platform engineering",
        "attributes": [
            ("department", "engineering"),
            ("clearance", "internal"),
            ("cost_center", "CC-100"),
            ("region", "us-east"),
            ("tier", "standard"),
        ],
    },
    {
        "name": "Data Science",
        "description": "Analytics and ML teams",
        "attributes": [
            ("department", "data-science"),
            ("clearance", "internal"),
            ("cost_center", "CC-101"),
            ("region", "us-east"),
            ("tier", "premium"),
        ],
    },
    {
        "name": "Security",
        "description": "Security and compliance",
        "attributes": [
            ("department", "security"),
            ("clearance", "elevated"),
            ("cost_center", "CC-200"),
            ("region", "us-east"),
            ("tier", "elevated"),
        ],
    },
    {
        "name": "DevOps",
        "description": "Infrastructure and SRE",
        "attributes": [
            ("department", "devops"),
            ("clearance", "elevated"),
            ("cost_center", "CC-102"),
            ("region", "us-west"),
            ("tier", "elevated"),
        ],
    },
    {
        "name": "Product",
        "description": "Product management",
        "attributes": [
            ("department", "product"),
            ("clearance", "internal"),
            ("cost_center", "CC-300"),
            ("region", "us-east"),
            ("tier", "standard"),
        ],
    },
    {
        "name": "QA",
        "description": "Quality assurance",
        "attributes": [
            ("department", "qa"),
            ("clearance", "internal"),
            ("cost_center", "CC-103"),
            ("region", "eu-west"),
            ("tier", "standard"),
        ],
    },
    {
        "name": "Finance",
        "description": "Finance and billing",
        "attributes": [
            ("department", "finance"),
            ("clearance", "restricted"),
            ("cost_center", "CC-400"),
            ("region", "us-east"),
            ("tier", "restricted"),
        ],
    },
    {
        "name": "Contractors",
        "description": "External contractors",
        "attributes": [
            ("department", "external"),
            ("clearance", "contractor"),
            ("cost_center", "CC-999"),
            ("region", "any"),
            ("tier", "contractor"),
        ],
    },
    {
        "name": "Admins",
        "description": "System administrators",
        "attributes": [
            ("department", "it"),
            ("clearance", "admin"),
            ("cost_center", "CC-201"),
            ("region", "any"),
            ("tier", "admin"),
        ],
    },
    {
        "name": "Auditors",
        "description": "Read-only audit access",
        "attributes": [
            ("department", "compliance"),
            ("clearance", "audit"),
            ("cost_center", "CC-500"),
            ("region", "any"),
            ("tier", "audit"),
        ],
    },
]


class Command(BaseCommand):
    help = "Seed 10 test groups with attributes for local/testing"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--org",
            type=str,
            default=None,
            help="Organization slug. If omitted, uses the first organization.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing test groups for the org before creating (recreates with new random attributes).",
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

        if options.get("reset"):
            test_names = [g["name"] for g in TEST_GROUPS]
            to_delete = Group.objects.filter(organization=org, name__in=test_names)
            count = to_delete.count()
            to_delete.delete()
            self.stdout.write(f"  Deleted {count} test group(s).")

        created = 0
        for data in TEST_GROUPS:
            group, was_created = Group.objects.get_or_create(
                organization=org,
                name=data["name"],
                defaults={"description": data["description"]},
            )
            if was_created:
                created += 1
                pool = data["attributes"]
                count = random.randint(2, min(5, len(pool)))
                chosen = random.sample(pool, count)
                for key, value in chosen:
                    GroupAttribute.objects.get_or_create(
                        group=group,
                        key=key,
                        value=value,
                    )
                self.stdout.write(f"  Created group: {data['name']} ({len(chosen)} attributes)")
            else:
                self.stdout.write(f"  Skipped (exists): {data['name']}")

        self.stdout.write(self.style.SUCCESS(f"Done. Created {created} new groups."))
