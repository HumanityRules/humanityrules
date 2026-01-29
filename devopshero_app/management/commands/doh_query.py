"""
Management command for ad-hoc model queries.

Designed for shell-safe queries that avoid quoting issues when run via prod_manage.sh.

Usage:
    ./prod_manage.sh doh_query <Model> [field1 field2 ...] [--filter key=value] [--limit N]

Examples:
    ./prod_manage.sh doh_query Repository
    ./prod_manage.sh doh_query Repository full_name default_branch
    ./prod_manage.sh doh_query Repository full_name --filter full_name__icontains=ai-detector
    ./prod_manage.sh doh_query App name slug app_type --limit 5
    ./prod_manage.sh doh_query Deployment status --filter status=failed --limit 10
    ./prod_manage.sh doh_query Message --describe  # Show available fields
    ./prod_manage.sh doh_query Message role content --order created_at --desc  # Descending order
"""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Query model data without shell quoting issues"

    def add_arguments(self, parser):
        parser.add_argument("model", help="Model name (e.g., Repository, App, Deployment)")
        parser.add_argument("fields", nargs="*", help="Fields to display (default: all)")
        parser.add_argument("--filter", "-f", action="append", dest="filters", help="Filter as key=value (can repeat)")
        parser.add_argument("--limit", "-l", type=int, default=50, help="Max rows to return (default: 50)")
        parser.add_argument("--order", "-o", help="Field to order by")
        parser.add_argument("--desc", action="store_true", help="Order descending (use with --order)")
        parser.add_argument("--describe", action="store_true", help="Show available fields and exit")

    def handle(self, *args, **options):
        model_name = options["model"]
        fields = options["fields"]
        filters = options["filters"] or []
        limit = options["limit"]
        order_by = options.get("order")
        desc = options.get("desc", False)
        describe = options.get("describe", False)

        # Find the model
        try:
            model = apps.get_model("devopshero_app", model_name)
        except LookupError:
            available = [m.__name__ for m in apps.get_app_config("devopshero_app").get_models()]
            raise CommandError(f"Model '{model_name}' not found. Available: {', '.join(sorted(available))}")

        # Handle --describe: show fields and exit
        if describe:
            all_fields = [f.name for f in model._meta.get_fields()]
            self.stdout.write(f"Model: {model_name}")
            self.stdout.write(f"Fields: {', '.join(sorted(all_fields))}")
            return

        # Build queryset
        queryset = model.objects.all()

        # Apply filters
        filter_kwargs = {}
        for f in filters:
            if "=" not in f:
                raise CommandError(f"Invalid filter '{f}'. Use key=value format.")
            key, value = f.split("=", 1)
            # Handle special values
            if value.lower() == "true":
                value = True
            elif value.lower() == "false":
                value = False
            elif value.lower() == "none":
                value = None
            filter_kwargs[key] = value

        if filter_kwargs:
            queryset = queryset.filter(**filter_kwargs)

        # Apply ordering
        if order_by:
            if desc:
                order_by = f"-{order_by}"
            queryset = queryset.order_by(order_by)

        # Apply limit
        queryset = queryset[:limit]

        # Determine fields to display
        if not fields:
            # Default: show id/pk plus a few common fields
            all_field_names = [f.name for f in model._meta.get_fields() if hasattr(f, "column")]
            # Prioritize common useful fields
            priority = ["id", "name", "slug", "full_name", "status", "created_at"]
            fields = [f for f in priority if f in all_field_names]
            # Add remaining fields up to a reasonable count
            for f in all_field_names:
                if f not in fields and len(fields) < 8:
                    fields.append(f)

        # Validate fields exist
        valid_fields = {f.name for f in model._meta.get_fields()}
        for field in fields:
            if field not in valid_fields:
                raise CommandError(f"Field '{field}' not found on {model_name}. Available: {', '.join(sorted(valid_fields))}")

        # Output results
        count = 0
        for obj in queryset:
            values = []
            for field in fields:
                val = getattr(obj, field, None)
                # Handle foreign keys - show the string representation
                if hasattr(val, "pk"):
                    val = str(val)
                values.append(str(val) if val is not None else "None")
            self.stdout.write(" | ".join(values))
            count += 1

        # Summary
        self.stdout.write(self.style.SUCCESS(f"\n({count} rows)"))
