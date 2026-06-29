"""Org-admin UI for sharing integration credentials — the unified Provider Keys tab.

Org admins land here (gated by ``base.require_org_admin``) to share integration
credentials with their members through one dialog that covers both kinds:

- **paste a key** — vault providers (OpenRouter / OpenAI / Anthropic / Tavily):
  paste a key that is live-validated and stored.
- **connect a login** — device-flow OAuth providers (Codex / Nous): the control
  plane drives the provider's device handshake (a shared credential has no per-user
  broker to run it); the admin's browser self-polls a code/verification-link dialog
  until approved, then the refresh token is stored on the share row.

"Who receives it" is everyone / a workspace / a user (per-org, ABAC) or — for the
platform-owner org only — **All customers** (the global ``PlatformSharedCredential``).
The add/edit endpoints dispatch by provider kind; both paths route writes through
``shared_credential_store``, which owns the table choice + the platform-owner gate.
Device-login in-flight state lives in ``request.session`` (the house pattern for CP
OAuth state), so identity + the gate are re-derived from the live request each poll,
never trusted from the session. See ``docs/platform_shared_credentials_design.md``.
"""

import logging
import time

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, QueryDict
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from humanityrules_app.models import (
    IntegrationSharedCredential,
    IntegrationUserCredential,
    Organization,
    OrganizationMembership,
    PlatformSharedCredential,
    Workspace,
)
from humanityrules_app.views.integrations import (
    platform_owner,
    provider_common,
    provider_registry,
    shared_credential_store,
)

from .. import base

logger = logging.getLogger(__name__)

SHARED_KEYS_URL = "/integrations/org/provider-keys/"
SESSION_KEY = "shared_device_login"
MODAL_TEMPLATE = "humanityrules_app/integrations/shared_credential_modal.html"


def provider_kind(provider: str) -> str:
    """Return 'key' (paste a vault key), 'login' (device-flow OAuth), or '' (not shareable)."""
    spec = provider_registry.get(provider=provider)
    if spec is None or not hasattr(spec.module, "refresh_outcome_from_shared"):
        return ""
    if hasattr(spec.module, "validate_shared_key"):
        return "key"
    if spec.kind == provider_registry.ProviderKind.OAUTH and hasattr(spec.module, "device_authorize") and hasattr(spec.module, "device_poll"):
        return "login"
    return ""


def all_provider_choices() -> list[dict]:
    """Return [{value, label, kind}] for every shareable provider (paste-key + connect-login)."""
    choices = []
    for provider in provider_registry.REGISTRY:
        kind = provider_kind(provider=provider)
        if kind:
            choices.append({"value": str(provider), "label": IntegrationUserCredential.Provider(provider).label, "kind": kind})
    return choices


def paste_provider_choices() -> list[dict]:
    """Return [{value, label}] for the vault providers that support a pasted shared key."""
    return [{"value": c["value"], "label": c["label"]} for c in all_provider_choices() if c["kind"] == "key"]


def provider_label(provider: str) -> str:
    """Human label for a provider slug, falling back to the raw slug."""
    try:
        return IntegrationUserCredential.Provider(provider).label
    except ValueError:
        return provider


def selected_label(options: list[dict], value: str, placeholder: str) -> str:
    """Label of the option whose id matches `value`, else the placeholder."""
    for option in options:
        if option["id"] == value:
            return option["name"]
    return placeholder


def target_options(org: Organization) -> tuple[list[dict], list[dict]]:
    """Return (workspace_options, member_options) for the share-with dropdowns."""
    workspaces = Workspace.objects.filter(organization=org).order_by("name")
    members = OrganizationMembership.objects.filter(organization=org).select_related("user").order_by("user__username")
    workspace_options = [{"id": str(ws.id), "name": ws.name} for ws in workspaces]
    member_options = [{"id": str(m.user.id), "name": m.user.email or m.user.username} for m in members]
    return workspace_options, member_options


def changed_response() -> HttpResponse:
    """Empty 200 that closes the modal and tells the list to refresh (HX-Trigger)."""
    response = HttpResponse("")
    response["HX-Trigger"] = "sharedKeysChanged"
    return response


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
    can_share_platform = platform_owner.is_platform_owner_org(organization=org)

    context = base.get_app_shell_context(request=request, current_page="integrations")
    context["active_tab"] = "provider-keys"
    context["shared_key_rows"] = shared_credential_store.list_display_rows(organization=org, include_platform=can_share_platform)
    return render(request, "humanityrules_app/integrations/shared_keys.html", context=context)


@login_required
def integrations_org_shared_keys_add(request: HttpRequest) -> HttpResponse:
    """Render the add dialog (GET) and create the credential (POST), dispatching by provider kind."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    org = request.user.current_organization
    if request.method != "POST":
        return _render_form(request=request, org=org, credential=None, values=None, errors={})

    provider = (request.POST.get("provider") or "").strip()
    kind = provider_kind(provider=provider)
    if kind == "key":
        _credential, errors = _save(request=request, org=org, credential=None)
        if errors is not None:
            return _render_form(request=request, org=org, credential=None, values=request.POST, errors=errors)
        return changed_response()
    if kind == "login":
        return _start_login(request=request, org=org, provider=provider, credential=None)
    return _render_form(request=request, org=org, credential=None, values=request.POST, errors={"provider": "Choose a supported provider."})


@login_required
def integrations_org_shared_keys_edit(request: HttpRequest, credential_id: str) -> HttpResponse:
    """Render the edit dialog (GET) and update the credential (POST), dispatching by row kind."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    org = request.user.current_organization
    can_share_platform = platform_owner.is_platform_owner_org(organization=org)
    credential = shared_credential_store.get_share(organization=org, credential_id=credential_id, include_platform=can_share_platform)
    if credential is None:
        raise Http404("Shared credential not found.")

    if request.method != "POST":
        return _render_form(request=request, org=org, credential=credential, values=None, errors={})

    if provider_kind(provider=credential.provider) == "login":
        _credential, errors = _save_login_target(request=request, org=org, credential=credential)
    else:
        _credential, errors = _save(request=request, org=org, credential=credential)
    if errors is not None:
        return _render_form(request=request, org=org, credential=credential, values=request.POST, errors=errors)
    return changed_response()


@login_required
@require_POST
def integrations_org_shared_keys_delete(request: HttpRequest, credential_id: str) -> HttpResponse:
    """Delete one shared credential (org or platform) and, via cascade/signal, its seeded tags."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    org = request.user.current_organization
    can_share_platform = platform_owner.is_platform_owner_org(organization=org)
    shared_credential_store.delete_share(organization=org, credential_id=credential_id, include_platform=can_share_platform)
    return changed_response()


@login_required
def integrations_org_shared_login_poll(request: HttpRequest) -> HttpResponse:
    """Run one device-poll attempt; render the code dialog (pending), close (done), or show the error."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    session_state = request.session.get(SESSION_KEY)
    if not isinstance(session_state, dict):
        return _render_error(request=request, message="This login session has expired. Start over.")
    if time.time() > session_state.get("expires_at", 0):
        request.session.pop(SESSION_KEY, None)
        return _render_error(request=request, message="The login timed out before it was approved. Start over.")

    spec = _connect_spec(provider=session_state.get("provider", ""))
    if spec is None:
        request.session.pop(SESSION_KEY, None)
        return _render_error(request=request, message="This login provider can no longer be connected.")

    result = spec.module.device_poll(opaque=session_state.get("opaque", {}))
    if result.status == provider_common.DEVICE_PENDING:
        return _render_code(request=request)
    if result.status == provider_common.DEVICE_FAILED:
        request.session.pop(SESSION_KEY, None)
        logger.error("shared device login failed provider=%s: %s", session_state.get("provider"), result.error)
        return _render_error(request=request, message=result.error)

    store_error = _store_completed(request=request, session_state=session_state, result=result)
    request.session.pop(SESSION_KEY, None)
    if store_error is not None:
        return _render_error(request=request, message=store_error)
    return changed_response()


@login_required
@require_POST
def integrations_org_shared_login_cancel(request: HttpRequest) -> HttpResponse:
    """Abandon an in-flight device login (clears the session); the client closes the modal."""
    request.session.pop(SESSION_KEY, None)
    return HttpResponse("")


@login_required
@require_POST
def integrations_org_shared_login_reconnect(request: HttpRequest, credential_id: str) -> HttpResponse:
    """Start a device flow to replace the refresh-token of an existing shared login."""
    forbidden = base.require_org_admin(request)
    if forbidden:
        return forbidden

    org = request.user.current_organization
    can_share_platform = platform_owner.is_platform_owner_org(organization=org)
    credential = shared_credential_store.get_share(organization=org, credential_id=credential_id, include_platform=can_share_platform)
    if credential is None:
        raise Http404("Shared login not found.")
    if provider_kind(provider=credential.provider) != "login":
        raise Http404("This credential is not a connectable login.")
    return _start_login(request=request, org=org, provider=credential.provider, credential=credential)


def _connect_spec(provider: str) -> provider_registry.ProviderSpec | None:
    """Return the spec for a device-flow OAuth provider that supports shared logins, or None."""
    spec = provider_registry.get_of_kind(provider=provider, kind=provider_registry.ProviderKind.OAUTH)
    if spec is None or not hasattr(spec.module, "device_authorize") or not hasattr(spec.module, "device_poll") or not hasattr(spec.module, "refresh_outcome_from_shared"):
        return None
    return spec


def _start_login(request: HttpRequest, org: Organization, provider: str, credential: IntegrationSharedCredential | PlatformSharedCredential | None) -> HttpResponse:
    """Validate the share target and begin the device flow; render the code dialog (or form errors)."""
    spec = _connect_spec(provider=provider)
    if spec is None:
        return _render_form(request=request, org=org, credential=credential, values=request.POST, errors={"provider": "Choose a supported login provider."})

    allow_platform = platform_owner.is_platform_owner_org(organization=org)
    if credential is not None:
        # Reconnect: keep the row's existing target, just replace the refresh token.
        ui_scope, target_user_id, target_workspace_id = _existing_target_fields(credential=credential)
        edit_credential_id = str(credential.id)
    else:
        ui_scope = (request.POST.get("scope") or "").strip()
        target_user_id = (request.POST.get("target_user") or "").strip()
        target_workspace_id = (request.POST.get("target_workspace") or "").strip()
        edit_credential_id = ""
        _target, errors = shared_credential_store.resolve_share_target(
            organization=org, scope=ui_scope, target_user_id=target_user_id,
            target_workspace_id=target_workspace_id, allow_platform=allow_platform,
        )
        if errors:
            return _render_form(request=request, org=org, credential=None, values=request.POST, errors=errors)

    authorization, error = spec.module.device_authorize()
    if error is not None:
        logger.error("shared device login start failed provider=%s: %s", provider, error)
        return _render_form(request=request, org=org, credential=credential, values=request.POST, errors={"non_field": error})

    request.session[SESSION_KEY] = {
        "provider": str(provider),
        "ui_scope": ui_scope,
        "target_user_id": target_user_id,
        "target_workspace_id": target_workspace_id,
        "edit_credential_id": edit_credential_id,
        "opaque": authorization.opaque,
        "user_code": authorization.user_code,
        "verification_uri": authorization.verification_uri,
        "interval": authorization.interval,
        "expires_at": time.time() + authorization.expires_in,
    }
    return _render_code(request=request)


def _store_completed(request: HttpRequest, session_state: dict, result: provider_common.DevicePollResult) -> str | None:
    """Persist an approved device login on the routed share row; return an error message or None."""
    org = request.user.current_organization
    allow_platform = platform_owner.is_platform_owner_org(organization=org)
    target, errors = shared_credential_store.resolve_share_target(
        organization=org, scope=session_state.get("ui_scope", ""),
        target_user_id=session_state.get("target_user_id", ""),
        target_workspace_id=session_state.get("target_workspace_id", ""),
        allow_platform=allow_platform,
    )
    if errors:
        return "The selected target is no longer valid. Start over."

    edit_credential_id = session_state.get("edit_credential_id", "")
    existing = (
        shared_credential_store.get_share(organization=org, credential_id=edit_credential_id, include_platform=allow_platform)
        if edit_credential_id else None
    )
    metadata = {**(existing.metadata if existing is not None else {}), "connected_at": timezone.now().isoformat(), **result.row_metadata}
    _row, dup_error = shared_credential_store.upsert_share(
        organization=org, provider=session_state.get("provider", ""), target=target,
        credentials={"refresh_token": result.refresh_token}, metadata=metadata,
        created_by=request.user, existing=existing,
    )
    if dup_error is not None:
        return dup_error
    logger.info(
        "shared login connected org=%s provider=%s platform=%s by=%s",
        org.slug, session_state.get("provider"), target.is_platform, request.user.username,
    )
    return None


def _existing_target_fields(credential: IntegrationSharedCredential | PlatformSharedCredential) -> tuple[str, str, str]:
    """Return (ui_scope, target_user_id, target_workspace_id) describing a row's current target."""
    if isinstance(credential, PlatformSharedCredential):
        return shared_credential_store.PLATFORM_SCOPE, "", ""
    return credential.scope, str(credential.target_user_id or ""), str(credential.target_workspace_id or "")


def _save(request: HttpRequest, org: Organization, credential: IntegrationSharedCredential | PlatformSharedCredential | None) -> tuple[object | None, dict | None]:
    """Validate the posted paste-key form and create/update the credential; return (credential, errors)."""
    is_edit = credential is not None
    scope = (request.POST.get("scope") or "").strip()
    api_key = (request.POST.get("api_key") or "").strip()
    target_workspace_id = (request.POST.get("target_workspace") or "").strip()
    target_user_id = (request.POST.get("target_user") or "").strip()
    # Provider is fixed once chosen — uniqueness is keyed on it. Add picks it; edit keeps it.
    provider = credential.provider if is_edit else (request.POST.get("provider") or "").strip()

    spec = provider_registry.get(provider=provider)
    validate_shared_key = getattr(spec.module, "validate_shared_key", None) if spec is not None else None
    if validate_shared_key is None:
        return None, {"provider": "Choose a supported provider."}

    allow_platform = platform_owner.is_platform_owner_org(organization=org)
    target, errors = shared_credential_store.resolve_share_target(
        organization=org, scope=scope, target_user_id=target_user_id,
        target_workspace_id=target_workspace_id, allow_platform=allow_platform,
    )
    if errors:
        return None, errors

    credentials_payload = credential.credentials if is_edit else {}
    metadata = credential.metadata if is_edit else {}
    if api_key:
        validated_metadata, key_error = validate_shared_key(api_key=api_key)
        if key_error is not None:
            return None, {"api_key": key_error}
        credentials_payload = {"api_key": api_key}
        metadata = validated_metadata
    elif not is_edit:
        return None, {"api_key": "Paste an API key."}

    row, dup_error = shared_credential_store.upsert_share(
        organization=org, provider=provider, target=target, credentials=credentials_payload,
        metadata=metadata, created_by=request.user, existing=credential,
    )
    if dup_error is not None:
        return None, {"non_field": dup_error}

    logger.info(
        "shared key %s org=%s provider=%s platform=%s by=%s",
        "updated" if is_edit else "created", org.slug, provider, target.is_platform, request.user.username,
    )
    return row, None


def _save_login_target(request: HttpRequest, org: Organization, credential: IntegrationSharedCredential | PlatformSharedCredential) -> tuple[object | None, dict | None]:
    """Re-target an existing connected login (change who receives it) without reconnecting."""
    scope = (request.POST.get("scope") or "").strip()
    target_user_id = (request.POST.get("target_user") or "").strip()
    target_workspace_id = (request.POST.get("target_workspace") or "").strip()
    allow_platform = platform_owner.is_platform_owner_org(organization=org)
    target, errors = shared_credential_store.resolve_share_target(
        organization=org, scope=scope, target_user_id=target_user_id,
        target_workspace_id=target_workspace_id, allow_platform=allow_platform,
    )
    if errors:
        return None, errors

    row, dup_error = shared_credential_store.upsert_share(
        organization=org, provider=credential.provider, target=target, credentials=credential.credentials,
        metadata=credential.metadata, created_by=request.user, existing=credential,
    )
    if dup_error is not None:
        return None, {"non_field": dup_error}
    logger.info("shared login re-targeted org=%s provider=%s by=%s", org.slug, credential.provider, request.user.username)
    return row, None


def _render_form(request: HttpRequest, org: Organization, credential: IntegrationSharedCredential | PlatformSharedCredential | None, values: QueryDict | None, errors: dict) -> HttpResponse:
    """Render the unified dialog's form stage (provider + adaptive secret region + share-with)."""
    is_edit = credential is not None
    is_platform_row = isinstance(credential, PlatformSharedCredential)
    if values is not None:
        echoed = {
            "provider": (values.get("provider") or "").strip(),
            "scope": (values.get("scope") or "").strip(),
            "target_workspace": (values.get("target_workspace") or "").strip(),
            "target_user": (values.get("target_user") or "").strip(),
        }
    elif is_edit:
        ui_scope, target_user_id, target_workspace_id = _existing_target_fields(credential=credential)
        echoed = {"provider": credential.provider, "scope": ui_scope, "target_workspace": target_workspace_id, "target_user": target_user_id}
    else:
        echoed = {"provider": "", "scope": "", "target_workspace": "", "target_user": ""}

    effective_provider = credential.provider if is_edit else echoed["provider"]
    workspace_options, member_options = target_options(org=org)
    provider_options = [{"id": c["value"], "name": c["label"], "kind": c["kind"]} for c in all_provider_choices()]

    context = {
        "stage": "form",
        "is_edit": is_edit,
        "is_platform_row": is_platform_row,
        "credential_id": str(credential.id) if is_edit else "",
        "can_share_platform": platform_owner.is_platform_owner_org(organization=org),
        "provider_options": provider_options,
        "provider_selected_label": selected_label(options=provider_options, value=echoed["provider"], placeholder="Select…"),
        "provider_label": provider_label(provider=credential.provider) if is_edit else "",
        "provider_kind": provider_kind(provider=effective_provider),
        "key_configured": bool(credential.credentials.get("api_key")) if is_edit else False,
        "login_connected": bool(credential.credentials.get("refresh_token")) if is_edit else False,
        "workspace_options": workspace_options,
        "workspace_selected_label": selected_label(options=workspace_options, value=echoed["target_workspace"], placeholder="Select a workspace…"),
        "member_options": member_options,
        "member_selected_label": selected_label(options=member_options, value=echoed["target_user"], placeholder="Select a user…"),
        "values": echoed,
        "errors": errors,
    }
    return render(request, MODAL_TEMPLATE, context=context)


def _render_code(request: HttpRequest) -> HttpResponse:
    """Render the unified dialog's code stage (user code + verification link + self-poll)."""
    session_state = request.session.get(SESSION_KEY) or {}
    context = {
        "stage": "code",
        "user_code": session_state.get("user_code", ""),
        "verification_uri": session_state.get("verification_uri", ""),
        "poll_interval": max(2, int(session_state.get("interval", 3))),
        "provider_label": provider_label(provider=session_state.get("provider", "")),
    }
    return render(request, MODAL_TEMPLATE, context=context)


def _render_error(request: HttpRequest, message: str) -> HttpResponse:
    """Render the unified dialog's error stage (terminal; no polling)."""
    return render(request, MODAL_TEMPLATE, context={"stage": "error", "error_message": message})
