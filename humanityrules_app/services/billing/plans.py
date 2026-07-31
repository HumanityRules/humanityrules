"""Plan entitlements in code: the registry, the per-org overrides, and the one gate API.

Plans are a config registry rather than DB rows. With no customers there is
nothing to grandfather, and ledger entries stamp ``PLAN_VERSION`` so history
stays explainable when DB-versioned plans eventually arrive.

``Organization.plan_overrides`` is the admin escape hatch for every exception —
comped credits, extended trials, extra agents, capability grants. Overrides
replace a field wholly; there are no per-field merge semantics. Keys are
validated against this schema on save, so a typo is a save error rather than a
silently dead entitlement. ``price_usd_month`` is not overridable: Stripe
charges what the subscription says regardless, so an override there would only
lie about the money.

``effective_plan`` is the only way to read entitlements. Nothing else looks at
the raw plan field or the overrides dict, which is what keeps every gate — the
deploy path's platform capabilities, the agent cap, the credit grant — agreeing
about one organization.

This module deliberately imports no models at runtime: ``Organization`` calls
into it from ``save``, and validation has to stay importable from there.
"""

import dataclasses
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from humanityrules_app import models

# Stamped into grant metadata at write time, so an old entry still explains
# which numbers it was written against after the registry below moves on.
PLAN_VERSION = "v1"

TRIAL = "trial"
OPERATOR = "operator"
TEAM = "team"
ENTERPRISE = "enterprise"

# The one field payment owns. Overriding it would claim a price Stripe never charges.
PRICE_FIELD = "price_usd_month"


@dataclass(frozen=True)
class PlanConfig:
    """One plan's entitlements: what it costs and what it lets an organization do."""

    price_usd_month: Decimal | None
    monthly_credit_grant: int
    always_on: bool
    trial_runtime_days: int | None
    max_agents: int
    customer_cloud: bool
    bedrock_enabled: bool
    on_demand_allowed: bool

    def platform_capability_slugs(self) -> list[str]:
        """The infra slugs this plan's capability booleans grant, for the deploy path."""
        return [
            slug for field_name, slug in CAPABILITY_SLUG_BY_FIELD.items()
            if getattr(self, field_name)
        ]


# Maps PlanConfig capability fields to the infra slugs deploy understands
# (AppConfig.platform_capabilities → ECS task-role grants → HUMR_PLATFORM_CAPABILITIES).
# Fixed here so every capability has a PlanConfig field — no silent extras.
CAPABILITY_SLUG_BY_FIELD = {
    "bedrock_enabled": "bedrock-runtime",
}

# Operator and Trial are the self-serve plans and their numbers are the product.
# Team and Enterprise are sales-led: these are the starting points a deal adjusts
# through plan_overrides, not quoted prices.
PLANS: dict[str, PlanConfig] = {
    TRIAL: PlanConfig(
        price_usd_month=None,
        monthly_credit_grant=500,
        always_on=False,
        trial_runtime_days=7,
        max_agents=1,
        customer_cloud=False,
        bedrock_enabled=False,
        on_demand_allowed=False,
    ),
    OPERATOR: PlanConfig(
        price_usd_month=Decimal("39"),
        monthly_credit_grant=2000,
        always_on=True,
        trial_runtime_days=None,
        max_agents=1,
        customer_cloud=False,
        bedrock_enabled=False,
        on_demand_allowed=False,
    ),
    TEAM: PlanConfig(
        price_usd_month=None,
        monthly_credit_grant=20000,
        always_on=True,
        trial_runtime_days=None,
        max_agents=25,
        customer_cloud=True,
        bedrock_enabled=False,
        on_demand_allowed=False,
    ),
    ENTERPRISE: PlanConfig(
        price_usd_month=None,
        monthly_credit_grant=100000,
        always_on=True,
        trial_runtime_days=None,
        max_agents=250,
        customer_cloud=True,
        bedrock_enabled=True,
        on_demand_allowed=False,
    ),
}

# Overridable PlanConfig fields and the types each accepts. Validation rejects
# bools for int fields first: in Python, bool subclasses int, so True would
# otherwise look like a valid credit grant.
_OVERRIDE_TYPES: dict[str, tuple[type, ...]] = {
    "monthly_credit_grant": (int,),
    "always_on": (bool,),
    "trial_runtime_days": (int, type(None)),
    "max_agents": (int,),
    "customer_cloud": (bool,),
    "bedrock_enabled": (bool,),
    "on_demand_allowed": (bool,),
}

_PLAN_FIELDS = {field.name for field in dataclasses.fields(PlanConfig)}
if set(_OVERRIDE_TYPES) | {PRICE_FIELD} != _PLAN_FIELDS:
    raise RuntimeError(
        f"plan override schema {sorted(set(_OVERRIDE_TYPES) | {PRICE_FIELD})} has drifted from "
        f"PlanConfig's fields {sorted(_PLAN_FIELDS)}"
    )


class PlanOverrideError(ValueError):
    """An override names a field no plan has, or carries a value of the wrong type."""


def validate_overrides(overrides: object) -> None:
    """Raise PlanOverrideError unless every key names a plan field and every value fits its type."""
    if not isinstance(overrides, dict):
        raise PlanOverrideError(f"plan_overrides must be a JSON object, got {type(overrides).__name__}")

    for key, value in overrides.items():
        if key == PRICE_FIELD:
            raise PlanOverrideError(
                f"{PRICE_FIELD!r} is not overridable: Stripe charges what the subscription says. "
                "Change the plan or the subscription instead."
            )
        accepted = _OVERRIDE_TYPES.get(key)
        if accepted is None:
            raise PlanOverrideError(
                f"{key!r} is not a plan entitlement; valid keys are {sorted(_OVERRIDE_TYPES)}"
            )
        if bool in accepted:
            if not isinstance(value, bool):
                raise PlanOverrideError(f"plan override {key!r} must be a boolean, got {value!r}")
            continue
        if isinstance(value, bool) or not isinstance(value, accepted):
            expected = " or ".join(accepted_type.__name__ for accepted_type in accepted)
            raise PlanOverrideError(f"plan override {key!r} must be {expected}, got {value!r}")


def effective_plan(organization: "models.Organization") -> PlanConfig:
    """The organization's entitlements: its plan's config with its overrides applied."""
    base = PLANS.get(organization.plan)
    if base is None:
        raise PlanOverrideError(
            f"organization {organization.slug!r} is on unknown plan {organization.plan!r}; "
            f"known plans are {sorted(PLANS)}"
        )
    overrides = organization.plan_overrides or {}
    validate_overrides(overrides=overrides)
    return dataclasses.replace(base, **overrides)
