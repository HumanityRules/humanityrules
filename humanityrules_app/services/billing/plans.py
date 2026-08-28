"""Plan entitlements in code: the registry, the per-org overrides, and the one gate API.

Plans are a config registry rather than DB rows. With no customers there is
nothing to grandfather, and ledger entries stamp ``PLAN_VERSION`` so history
stays explainable when DB-versioned plans eventually arrive.

``Organization.plan_overrides`` is the admin escape hatch for every exception —
comped credits, extended trials, extra agents, capability grants. Overrides
replace a field wholly; there are no per-field merge semantics. Keys are
validated against this schema on save, so a typo is a save error rather than a
silently dead entitlement. The price fields are not overridable: Stripe
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

# The fields payment owns. Overriding them would claim prices Stripe never charges.
PRICE_FIELDS = frozenset({"price_usd_month", "price_usd_agent_month"})


@dataclass(frozen=True)
class PlanConfig:
    """One plan's entitlements: what it costs and what it lets an organization do."""

    price_usd_month: Decimal | None
    price_usd_agent_month: Decimal | None
    monthly_credit_grant: int
    always_on: bool
    trial_runtime_days: int | None
    max_agents: int | None  # None means unlimited
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

# The axis between the tiers is who hosts and who pays for models. Trial and
# Operator run on HumR's cloud against HumR-brokered models, so they carry
# credits and their numbers are the product. Team and Enterprise deploy into
# the customer's AWS account where the customer brings their own model
# (Bedrock or any provider they configure), so credits do not apply. Team's
# per-agent price is published but still sales-led; Enterprise is a custom
# deal (SSO, compliance, support) shaped through plan_overrides.
PLANS: dict[str, PlanConfig] = {
    TRIAL: PlanConfig(
        price_usd_month=None,
        price_usd_agent_month=None,
        monthly_credit_grant=500,
        always_on=False,
        trial_runtime_days=7,
        max_agents=1,
        customer_cloud=False,
        bedrock_enabled=False,
        on_demand_allowed=False,
    ),
    OPERATOR: PlanConfig(
        price_usd_month=Decimal("29"),
        price_usd_agent_month=None,
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
        price_usd_agent_month=Decimal("19"),
        monthly_credit_grant=0,
        always_on=True,
        trial_runtime_days=None,
        max_agents=None,
        customer_cloud=True,
        bedrock_enabled=True,
        on_demand_allowed=False,
    ),
    ENTERPRISE: PlanConfig(
        price_usd_month=None,
        price_usd_agent_month=None,
        monthly_credit_grant=0,
        always_on=True,
        trial_runtime_days=None,
        max_agents=None,
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
    "max_agents": (int, type(None)),
    "customer_cloud": (bool,),
    "bedrock_enabled": (bool,),
    "on_demand_allowed": (bool,),
}

_PLAN_FIELDS = {field.name for field in dataclasses.fields(PlanConfig)}
if set(_OVERRIDE_TYPES) | PRICE_FIELDS != _PLAN_FIELDS:
    raise RuntimeError(
        f"plan override schema {sorted(set(_OVERRIDE_TYPES) | PRICE_FIELDS)} has drifted from "
        f"PlanConfig's fields {sorted(_PLAN_FIELDS)}"
    )


class PlanOverrideError(ValueError):
    """An override names a field no plan has, or carries a value of the wrong type."""


def validate_overrides(overrides: object) -> None:
    """Raise PlanOverrideError unless every key names a plan field and every value fits its type."""
    if not isinstance(overrides, dict):
        raise PlanOverrideError(f"plan_overrides must be a JSON object, got {type(overrides).__name__}")

    for key, value in overrides.items():
        if key in PRICE_FIELDS:
            raise PlanOverrideError(
                f"{key!r} is not overridable: Stripe charges what the subscription says. "
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
