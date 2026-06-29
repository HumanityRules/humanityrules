"""Route a shared credential to the right table — the one place that knows two tables exist.

The unified Provider Keys UI offers four "share with" scopes: everyone / a
workspace / a user (the per-org, ABAC-evaluated ``IntegrationSharedCredential``)
and **All customers** (the global, ABAC-free ``PlatformSharedCredential``). Both
the paste-key flow (``org_shared_keys``) and the connect-login flow
(both in ``org_shared_keys``) write through here so the table choice, the
platform-owner gate, duplicate detection, and the list/get/delete lookups live in
exactly one place rather than being duplicated per flow.

The ``scope=platform`` gate is enforced via ``allow_platform`` passed by the
caller (computed from ``platform_owner.is_platform_owner_org``) — a global
credential must never be created by a non-owner org, UI hiding notwithstanding.
See ``docs/platform_shared_credentials_design.md``.
"""

import dataclasses
import logging

from django.db import IntegrityError

from humanityrules_app.models import (
    IntegrationSharedCredential,
    IntegrationUserCredential,
    Organization,
    OrganizationMembership,
    PlatformSharedCredential,
    User,
    Workspace,
)
from humanityrules_app.views.integrations import provider_registry

logger = logging.getLogger(__name__)

# UI-only "scope" value for the global tier. It is not an
# IntegrationSharedCredential.Scope member — it routes to the other table.
PLATFORM_SCOPE = "platform"


@dataclasses.dataclass(frozen=True)
class ShareTarget:
    """A validated "who receives this share" selection, resolved to model objects.

    ``is_platform`` routes to ``PlatformSharedCredential`` (global, no scope/target);
    otherwise ``scope`` (an ``IntegrationSharedCredential.Scope``) plus
    ``target_user`` / ``target_workspace`` route to the per-org table.
    """

    is_platform: bool
    scope: str
    target_user: User | None
    target_workspace: Workspace | None


def resolve_share_target(
    *,
    organization: Organization,
    scope: str,
    target_user_id: str,
    target_workspace_id: str,
    allow_platform: bool,
) -> tuple[ShareTarget | None, dict]:
    """Validate the posted scope/target into a ShareTarget, or return field errors.

    ``allow_platform`` is the platform-owner gate result: a ``scope=platform`` write
    by a non-owner org fails closed here (defense in depth — the UI also hides it).
    """
    if scope == PLATFORM_SCOPE:
        if not allow_platform:
            logger.error("platform share rejected for non-owner org=%s", organization.slug)
            return None, {"scope": "Your organization cannot create platform-wide shares."}
        return ShareTarget(is_platform=True, scope="", target_user=None, target_workspace=None), {}

    if scope == IntegrationSharedCredential.Scope.USER:
        membership = (
            OrganizationMembership.objects.filter(organization=organization, user_id=target_user_id).select_related("user").first()
            if target_user_id else None
        )
        if membership is None:
            return None, {"target_user": "Choose a member of this organization."}
        return ShareTarget(is_platform=False, scope=scope, target_user=membership.user, target_workspace=None), {}

    if scope == IntegrationSharedCredential.Scope.WORKSPACE:
        target_workspace = (
            Workspace.objects.filter(organization=organization, id=target_workspace_id).first()
            if target_workspace_id else None
        )
        if target_workspace is None:
            return None, {"target_workspace": "Choose a workspace."}
        return ShareTarget(is_platform=False, scope=scope, target_user=None, target_workspace=target_workspace), {}

    if scope == IntegrationSharedCredential.Scope.EVERYONE:
        return ShareTarget(is_platform=False, scope=scope, target_user=None, target_workspace=None), {}

    return None, {"scope": "Choose who receives this credential."}


def upsert_share(
    *,
    organization: Organization,
    provider: str,
    target: ShareTarget,
    credentials: dict,
    metadata: dict,
    created_by: User,
    existing: IntegrationSharedCredential | PlatformSharedCredential | None,
) -> tuple[IntegrationSharedCredential | PlatformSharedCredential | None, str | None]:
    """Create or update the share row in the table the *target* routes to.

    On create the table is chosen by ``target.is_platform``. On edit the row keeps
    its table — changing tier (platform <-> org) on edit is rejected, since that
    would move the row between tables; delete and recreate instead. Returns
    ``(row, None)`` or ``(None, non-field error message)`` for a duplicate / tier change.
    """
    if existing is not None and isinstance(existing, PlatformSharedCredential) != target.is_platform:
        return None, "Changing a share between your organization and all customers isn't supported — delete it and create a new one."

    if existing is not None and credentials != existing.credentials:
        # An OAuth reconnect rotates the shared refresh_token, so the cached access
        # token now belongs to the old account. Drop it: run_shared_refresh_exchange
        # serves token_cache before re-reading refresh_token, so a stale cache would
        # fan the wrong account's token out to every broker until it lapses (~1h).
        existing.token_cache = {}

    if target.is_platform:
        return _upsert_platform(provider=provider, credentials=credentials, metadata=metadata, created_by=created_by, existing=existing)
    return _upsert_org(organization=organization, provider=provider, target=target, credentials=credentials, metadata=metadata, created_by=created_by, existing=existing)


def _upsert_org(
    *,
    organization: Organization,
    provider: str,
    target: ShareTarget,
    credentials: dict,
    metadata: dict,
    created_by: User,
    existing: IntegrationSharedCredential | None,
) -> tuple[IntegrationSharedCredential | None, str | None]:
    """Create/update a per-org IntegrationSharedCredential, rejecting provider+target duplicates."""
    duplicate = IntegrationSharedCredential.objects.filter(organization=organization, provider=provider, scope=target.scope)
    if target.scope == IntegrationSharedCredential.Scope.USER:
        duplicate = duplicate.filter(target_user=target.target_user)
    elif target.scope == IntegrationSharedCredential.Scope.WORKSPACE:
        duplicate = duplicate.filter(target_workspace=target.target_workspace)
    if existing is not None:
        duplicate = duplicate.exclude(id=existing.id)
    if duplicate.exists():
        return None, "A share for this provider and target already exists."

    try:
        if existing is not None:
            existing.scope = target.scope
            existing.target_user = target.target_user
            existing.target_workspace = target.target_workspace
            existing.credentials = credentials
            existing.metadata = metadata
            existing.save()
            return existing, None
        credential = IntegrationSharedCredential.objects.create(
            organization=organization,
            provider=provider,
            scope=target.scope,
            target_user=target.target_user,
            target_workspace=target.target_workspace,
            credentials=credentials,
            metadata=metadata,
            created_by=created_by,
        )
        return credential, None
    except IntegrityError:
        return None, "A share for this provider and target already exists."


def _upsert_platform(
    *,
    provider: str,
    credentials: dict,
    metadata: dict,
    created_by: User,
    existing: PlatformSharedCredential | None,
) -> tuple[PlatformSharedCredential | None, str | None]:
    """Create/update a global PlatformSharedCredential, rejecting a second enabled row per provider."""
    duplicate = PlatformSharedCredential.objects.filter(provider=provider, enabled=True)
    if existing is not None:
        duplicate = duplicate.exclude(id=existing.id)
    if duplicate.exists():
        return None, "A platform-wide share for this provider already exists."

    try:
        if existing is not None:
            existing.credentials = credentials
            existing.metadata = metadata
            existing.save()
            return existing, None
        credential = PlatformSharedCredential.objects.create(
            provider=provider,
            credentials=credentials,
            metadata=metadata,
            enabled=True,
            created_by=created_by,
        )
        return credential, None
    except IntegrityError:
        return None, "A platform-wide share for this provider already exists."


def get_share(
    *,
    organization: Organization,
    credential_id: str,
    include_platform: bool,
) -> IntegrationSharedCredential | PlatformSharedCredential | None:
    """Return the share row with *credential_id*, scoped to the org (and platform if owner), or None.

    Looks in the per-org table (filtered to *organization* — the multi-tenant
    boundary) first; only an org allowed to manage platform shares
    (*include_platform*) can reach a ``PlatformSharedCredential`` row.
    """
    org_row = IntegrationSharedCredential.objects.filter(organization=organization, id=credential_id).first()
    if org_row is not None:
        return org_row
    if include_platform:
        return PlatformSharedCredential.objects.filter(id=credential_id).first()
    return None


def delete_share(organization: Organization, credential_id: str, include_platform: bool) -> None:
    """Delete the share row with *credential_id* if the caller may reach it (org-scoped / platform-owner)."""
    deleted, _ = IntegrationSharedCredential.objects.filter(organization=organization, id=credential_id).delete()
    if deleted or not include_platform:
        return
    PlatformSharedCredential.objects.filter(id=credential_id).delete()


def list_display_rows(organization: Organization, include_platform: bool) -> list[dict]:
    """Build the Provider Keys list rows: the org's shares, plus platform shares for the owner org."""
    rows = [
        display_row(credential=credential)
        for credential in (
            IntegrationSharedCredential.objects.filter(organization=organization)
            .select_related("target_user", "target_workspace", "created_by")
            .order_by("provider", "scope")
        )
    ]
    if include_platform:
        rows.extend(
            display_row(credential=credential)
            for credential in (
                PlatformSharedCredential.objects.filter(enabled=True).select_related("created_by").order_by("provider")
            )
        )
    return rows


def display_row(credential: IntegrationSharedCredential | PlatformSharedCredential) -> dict:
    """Build one Provider Keys list row (never exposes the secret)."""
    is_platform = isinstance(credential, PlatformSharedCredential)
    kind = _provider_kind(provider=credential.provider)
    secret_field = "refresh_token" if kind == "login" else "api_key"
    if is_platform:
        scope_label = "All customers"
        target_label = ""
    else:
        scope_label = credential.scope
        target_label = _org_target_label(credential=credential)
    return {
        "id": str(credential.id),
        "is_platform": is_platform,
        "kind": kind,
        "provider_label": _provider_label(provider=credential.provider),
        "scope_label": scope_label,
        "target_label": target_label,
        "configured": bool(credential.credentials.get(secret_field)),
        "confirmed": bool(credential.metadata.get("validated_at") or credential.metadata.get("connected_at")),
        "created_by": credential.created_by.username if credential.created_by is not None else "—",
    }


def _org_target_label(credential: IntegrationSharedCredential) -> str:
    """Human label for a per-org share's target (user / workspace / everyone)."""
    if credential.scope == IntegrationSharedCredential.Scope.USER:
        return credential.target_user.username if credential.target_user is not None else "—"
    if credential.scope == IntegrationSharedCredential.Scope.WORKSPACE:
        return credential.target_workspace.name if credential.target_workspace is not None else "—"
    return "Everyone"


def _provider_kind(provider: str) -> str:
    """Return "login" for device-flow OAuth providers, else "key" (paste a secret)."""
    spec = provider_registry.get(provider=provider)
    if spec is not None and spec.kind == provider_registry.ProviderKind.OAUTH:
        return "login"
    return "key"


def _provider_label(provider: str) -> str:
    """Human label for a provider slug, falling back to the raw slug."""
    try:
        return IntegrationUserCredential.Provider(provider).label
    except ValueError:
        return provider
