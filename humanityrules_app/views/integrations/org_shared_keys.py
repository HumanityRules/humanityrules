"""Org-admin UI for provisioning and sharing integration provider keys.

Org admins land here (gated by ``base.require_org_admin``) to CRUD
``IntegrationSharedCredential`` rows: pick a vault provider, paste a key that is
live-validated against the provider, and choose who receives it — everyone, a
workspace, or a single user. Saving a row seeds the ABAC ResourceTags the broker
matches against (post_save signal); the engine / resolver / broker wiring is
unchanged. Only the vault providers exposing ``validate_shared_key`` +
``refresh_outcome_from_shared`` (OpenRouter, OpenAI, Anthropic) are offered.

This is the org-admin-gated replacement for the superuser-only Django admin
surface (``IntegrationSharedCredentialAdmin``).
"""

import logging

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from humanityrules_app.models import (
    IntegrationSharedCredential,
    IntegrationUserCredential,
    Organization,
    OrganizationMembership,
    Workspace,
)
from humanityrules_app.views.integrations import provider_registry

from .. import base

logger = logging.getLogger(__name__)

SHARED_KEYS_URL = "/integrations/org/provider-keys/"


def _shareable_provider_choices() -> list[dict]:
    """Return [{value, label}] for the vault providers that support org sharing."""
    choices = []
    for provider, spec in provider_registry.REGISTRY.items():
        if hasattr(spec.module, "validate_shared_key") and hasattr(spec.module, "refresh_outcome_from_shared"):
            choices.append({"value": str(provider), "label": IntegrationUserCredential.Provider(provider).label})
    return choices


def _provider_label(provider: str) -> str:
    """Human label for a provider slug, falling back to the raw slug."""
    try:
        return IntegrationUserCredential.Provider(provider).label
    except ValueError:
        return provider


def _row_view(credential: IntegrationSharedCredential) -> dict:
    """Build the display row for one shared credential (never exposes the secret)."""
    if credential.scope == IntegrationSharedCredential.Scope.USER:
        target = credential.target_user.username if credential.target_user is not None else "—"
    elif credential.scope == IntegrationSharedCredential.Scope.WORKSPACE:
        target = credential.target_workspace.name if credential.target_workspace is not None else "—"
    else:
        target = "Everyone"
    return {
        "id": str(credential.id),
        "provider_label": _provider_label(provider=credential.provider),
        "scope": credential.scope,
        "target": target,
        "configured": bool(credential.credentials.get("api_key")),
        "validated_at": credential.metadata.get("validated_at"),
        "created_by": credential.created_by.username if credential.created_by is not None else "—",
    }


@login_required
def integrations_org_shared_keys(request: HttpRequest) -> HttpResponse:
    """Render the Provider Keys tab listing the org's shared integration credentials."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="integrations")
        context["content_url"] = SHARED_KEYS_URL
        return render(request, "humanityrules_app/app_shell.html", context=context)

    org = request.user.current_organization
    credentials = (
        IntegrationSharedCredential.objects.filter(organization=org)
        .select_related("target_user", "target_workspace", "created_by")
        .order_by("provider", "scope")
    )

    context = base.get_app_shell_context(request=request, current_page="integrations")
    context["active_tab"] = "provider-keys"
    context["shared_key_rows"] = [_row_view(credential=credential) for credential in credentials]
    return render(request, "humanityrules_app/integrations/shared_keys.html", context=context)


@login_required
def integrations_org_shared_keys_add(request: HttpRequest) -> HttpResponse:
    """Render the add-shared-key modal (GET) and create the credential (POST)."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    org = request.user.current_organization
    if request.method == "POST":
        _credential, errors = _save(request=request, org=org, credential=None)
        if errors is not None:
            return _render_form_modal(request=request, org=org, credential=None, errors=errors)
        return _changed_response()
    return _render_form_modal(request=request, org=org, credential=None, errors={})


@login_required
def integrations_org_shared_keys_edit(request: HttpRequest, credential_id: str) -> HttpResponse:
    """Render the edit-shared-key modal (GET) and update the credential (POST)."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    org = request.user.current_organization
    credential = IntegrationSharedCredential.objects.filter(organization=org, id=credential_id).first()
    if credential is None:
        raise Http404("Shared credential not found.")

    if request.method == "POST":
        _credential, errors = _save(request=request, org=org, credential=credential)
        if errors is not None:
            return _render_form_modal(request=request, org=org, credential=credential, errors=errors)
        return _changed_response()
    return _render_form_modal(request=request, org=org, credential=credential, errors={})


@login_required
@require_POST
def integrations_org_shared_keys_delete(request: HttpRequest, credential_id: str) -> HttpResponse:
    """Delete one shared credential (and, via cascade/signal, its seeded tags)."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    org = request.user.current_organization
    IntegrationSharedCredential.objects.filter(organization=org, id=credential_id).delete()
    return _changed_response()


def _changed_response() -> HttpResponse:
    """Empty 200 that closes the modal and tells the list to refresh (HX-Trigger)."""
    response = HttpResponse("")
    response["HX-Trigger"] = "sharedKeysChanged"
    return response


def _save(request: HttpRequest, org: Organization, credential: IntegrationSharedCredential | None) -> tuple[IntegrationSharedCredential | None, dict | None]:
    """Validate the posted form and create/update the credential; return (credential, errors)."""
    is_edit = credential is not None
    scope = (request.POST.get("scope") or "").strip()
    api_key = (request.POST.get("api_key") or "").strip()
    target_workspace_id = (request.POST.get("target_workspace") or "").strip()
    target_user_id = (request.POST.get("target_user") or "").strip()
    # Provider is fixed once chosen — uniqueness is keyed on it, so editing it
    # would change which row a member resolves to. Add picks it; edit keeps it.
    provider = credential.provider if is_edit else (request.POST.get("provider") or "").strip()

    errors: dict = {}
    spec = provider_registry.get(provider=provider)
    validate_shared_key = getattr(spec.module, "validate_shared_key", None) if spec is not None else None
    if validate_shared_key is None:
        errors["provider"] = "Choose a supported provider."

    target_workspace = None
    target_user = None
    if scope == IntegrationSharedCredential.Scope.USER:
        membership = (
            OrganizationMembership.objects.filter(organization=org, user_id=target_user_id)
            .select_related("user").first()
            if target_user_id else None
        )
        if membership is None:
            errors["target_user"] = "Choose a member of this organization."
        else:
            target_user = membership.user
    elif scope == IntegrationSharedCredential.Scope.WORKSPACE:
        target_workspace = (
            Workspace.objects.filter(organization=org, id=target_workspace_id).first()
            if target_workspace_id else None
        )
        if target_workspace is None:
            errors["target_workspace"] = "Choose a workspace."
    elif scope != IntegrationSharedCredential.Scope.EVERYONE:
        errors["scope"] = "Choose who receives this key."

    credentials_payload = credential.credentials if is_edit else {}
    metadata = credential.metadata if is_edit else {}
    if validate_shared_key is not None:
        if api_key:
            validated_metadata, key_error = validate_shared_key(api_key=api_key)
            if key_error is not None:
                errors["api_key"] = key_error
            else:
                credentials_payload = {"api_key": api_key}
                metadata = validated_metadata
        elif not is_edit:
            errors["api_key"] = "Paste an API key."

    if errors:
        return None, errors

    duplicate = IntegrationSharedCredential.objects.filter(organization=org, provider=provider, scope=scope)
    if scope == IntegrationSharedCredential.Scope.USER:
        duplicate = duplicate.filter(target_user=target_user)
    elif scope == IntegrationSharedCredential.Scope.WORKSPACE:
        duplicate = duplicate.filter(target_workspace=target_workspace)
    if is_edit:
        duplicate = duplicate.exclude(id=credential.id)
    if duplicate.exists():
        return None, {"non_field": "A shared key for this provider and target already exists."}

    try:
        if is_edit:
            credential.scope = scope
            credential.target_user = target_user
            credential.target_workspace = target_workspace
            credential.credentials = credentials_payload
            credential.metadata = metadata
            credential.save()
        else:
            credential = IntegrationSharedCredential.objects.create(
                organization=org,
                provider=provider,
                scope=scope,
                target_user=target_user,
                target_workspace=target_workspace,
                credentials=credentials_payload,
                metadata=metadata,
                created_by=request.user,
            )
    except IntegrityError:
        return None, {"non_field": "A shared key for this provider and target already exists."}

    logger.info(
        "shared credential %s org=%s provider=%s scope=%s by=%s",
        "updated" if is_edit else "created", org.slug, provider, scope, request.user.username,
    )
    return credential, None


def _render_form_modal(request: HttpRequest, org: Organization, credential: IntegrationSharedCredential | None, errors: dict) -> HttpResponse:
    """Render the add/edit modal, echoing posted values back on validation error."""
    if request.method == "POST":
        values = {
            "provider": (request.POST.get("provider") or "").strip(),
            "scope": (request.POST.get("scope") or "").strip(),
            "target_workspace": (request.POST.get("target_workspace") or "").strip(),
            "target_user": (request.POST.get("target_user") or "").strip(),
        }
    elif credential is not None:
        values = {
            "provider": credential.provider,
            "scope": credential.scope,
            "target_workspace": str(credential.target_workspace_id or ""),
            "target_user": str(credential.target_user_id or ""),
        }
    else:
        values = {"provider": "", "scope": "", "target_workspace": "", "target_user": ""}

    workspaces = Workspace.objects.filter(organization=org).order_by("name")
    members = OrganizationMembership.objects.filter(organization=org).select_related("user").order_by("user__username")

    context = {
        "is_edit": credential is not None,
        "credential_id": str(credential.id) if credential is not None else "",
        "provider_choices": _shareable_provider_choices(),
        "provider_label": _provider_label(provider=credential.provider) if credential is not None else "",
        "key_configured": bool(credential.credentials.get("api_key")) if credential is not None else False,
        "workspaces": [{"id": str(ws.id), "name": ws.name} for ws in workspaces],
        "members": [{"id": str(m.user.id), "label": m.user.email or m.user.username} for m in members],
        "values": values,
        "errors": errors,
    }
    return render(request, "humanityrules_app/integrations/shared_key_form_modal.html", context=context)
