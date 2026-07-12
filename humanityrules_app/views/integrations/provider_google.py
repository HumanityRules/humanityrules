"""Google Workspace per-user integration: OAuth connect + scope picker + token refresh.

Connect (browser redirect dance): the authenticated HUMR user starts at
`/integrations/user/google/start/?rd=<URL>&app_slug=<slug>&products=<sel>`
(where `rd` points at the Hermes WebUI in a customer env and `products` is the
scope selection the WebUI modal built, e.g. `gmail:write,calendar:read`),
consents at Google, and lands back at `/integrations/user/google/callback/`.
The callback persists the refresh_token in HUMR's DB as an
IntegrationUserCredential row; no long-lived Google credentials cross into the
customer env.

Capability model: each product is granted at one of three levels — off, read,
write. The WebUI card's Configure modal edits levels; the CP owns the
product→scope mapping and the granted-scope→level projection (the WebUI never
interprets raw Google scopes). Google's own granular-consent checkboxes may
grant a subset of what we request, so the stored/projected truth is always the
`scope` string Google returns, not what we asked for.

Expanding levels rides incremental auth (`include_granted_scopes=true`).
Narrowing cannot: Google only accumulates grants, and revocation is
PROJECT-GLOBAL — revoking one row's refresh_token kills the whole account
grant, including grants held by other HUMR apps/envs connected to the same
Google account. Narrowing therefore goes through a CSRF-protected
confirmation POST (`/integrations/user/google/narrow/`) that strictly revokes
the old grant, deletes the row, and re-consents WITHOUT
include_granted_scopes. The GET start view never revokes (prefetchers and
link scanners hit GETs).

Refresh (`refresh_outcome`): exchanges the stored refresh_token with Google
using HUMR's OAuth client_secret and returns a broker-shaped outcome dict
whose `metadata.google_grants` carries the granted-capability projection the
WebUI card renders. The refresh_token and HUMR's client_secret never cross
the customer/HUMR boundary; if Google has revoked the refresh_token the row
is deleted and the outcome flips to `absent` so the WebUI prompts a reconnect.
"""

import hashlib
import logging
import secrets
import time
from urllib.parse import urlencode, urlparse

import httpx
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from humanityrules_app.models import Environment, IntegrationConfig, IntegrationUserCredential, User
from humanityrules_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


GOOGLE_BASE_SCOPES = ["openid", "email"]

# Per-product scope sets by capability level. `read`/`write` are the FULL
# scope lists requested at that level (write is not a delta on read).
# `write_markers` classifies a GRANTED scope string back into a level: any
# marker present → write. Markers include broader scopes we never request
# (e.g. full `auth/calendar`) because Google merges grants project-wide, so a
# grant obtained through another app can show up in our token response.
GOOGLE_PRODUCTS = {
    "gmail": {
        "label": "Gmail",
        "read": ["https://www.googleapis.com/auth/gmail.readonly"],
        "write": ["https://www.googleapis.com/auth/gmail.modify"],
        "write_markers": ["https://www.googleapis.com/auth/gmail.modify", "https://mail.google.com/"],
    },
    "calendar": {
        "label": "Calendar",
        "read": ["https://www.googleapis.com/auth/calendar.readonly"],
        # Write = read everything + create/edit events. Deliberately NOT full
        # `auth/calendar`, which also allows sharing and deleting calendars.
        "write": [
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/calendar.events",
        ],
        "write_markers": ["https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar"],
    },
    "drive": {
        "label": "Drive",
        "read": ["https://www.googleapis.com/auth/drive.readonly"],
        "write": ["https://www.googleapis.com/auth/drive"],
        "write_markers": ["https://www.googleapis.com/auth/drive"],
    },
    "contacts": {
        "label": "Contacts",
        "read": ["https://www.googleapis.com/auth/contacts.readonly"],
        "write": ["https://www.googleapis.com/auth/contacts"],
        "write_markers": ["https://www.googleapis.com/auth/contacts"],
    },
    "sheets": {
        "label": "Sheets",
        "read": ["https://www.googleapis.com/auth/spreadsheets.readonly"],
        "write": ["https://www.googleapis.com/auth/spreadsheets"],
        "write_markers": ["https://www.googleapis.com/auth/spreadsheets"],
    },
    "docs": {
        "label": "Docs",
        "read": ["https://www.googleapis.com/auth/documents.readonly"],
        "write": ["https://www.googleapis.com/auth/documents"],
        "write_markers": ["https://www.googleapis.com/auth/documents"],
    },
}

LEVEL_OFF = "off"
LEVEL_READ = "read"
LEVEL_WRITE = "write"
_LEVEL_RANK = {LEVEL_OFF: 0, LEVEL_READ: 1, LEVEL_WRITE: 2}

# Cap the state-keyed session flow map so an abandoned-tab pileup can't grow
# the session unboundedly. 5 comfortably covers real multi-tab use.
_MAX_PENDING_FLOWS = 5

# Google's refresh-token exchange is a single small POST. Healthy P99 is
# well under 1s; setting the ceiling at 5s means the broker's batch refresh
# (which runs all providers in parallel) is naturally bounded by the slowest
# single exchange, no separate batch deadline needed. If upstream is taking
# longer than 5s, surfacing `transient` (cache-preserving) beats hanging a
# user request on a refresh that's about to fail anyway.
GOOGLE_TOKEN_EXCHANGE_TIMEOUT_SECONDS = 5

GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# Disconnect keeps a blanked tombstone row (see provider_disconnect): its
# `revoked_at_epoch` marker is what lets the callback reject pending OAuth
# flows whose consent predates the revocation. Deleting instead would erase
# the marker and let such a flow silently recreate the credential.
TOMBSTONE_ON_DISCONNECT = True


def parse_products_param(raw: str | None) -> dict[str, str] | None:
    """Parse `gmail:write,calendar:read` into a full product→level map, or None when invalid.

    An ABSENT param (None) falls back to the legacy selection: every product at
    read. A present-but-invalid value returns None so callers reject with 400 —
    invalid input must never widen requested access. A selection with every
    product off is invalid too (that's a Disconnect, not a connect).
    """
    if raw is None:
        return {product: LEVEL_READ for product in GOOGLE_PRODUCTS}
    selection = {product: LEVEL_OFF for product in GOOGLE_PRODUCTS}
    seen: set[str] = set()
    for entry in raw.split(","):
        product, sep, level = entry.strip().partition(":")
        if not sep or product not in GOOGLE_PRODUCTS or level not in (LEVEL_READ, LEVEL_WRITE) or product in seen:
            return None
        seen.add(product)
        selection[product] = level
    if all(level == LEVEL_OFF for level in selection.values()):
        return None
    return selection


def serialize_selection(selection: dict[str, str]) -> str:
    """Serialize a product→level map back to the `products` param form (off entries dropped)."""
    return ",".join(f"{product}:{level}" for product, level in selection.items() if level != LEVEL_OFF)


def scopes_for_selection(selection: dict[str, str]) -> list[str]:
    """Build the ordered, deduplicated scope list for a product→level selection."""
    scopes = list(GOOGLE_BASE_SCOPES)
    for product, level in selection.items():
        if level == LEVEL_OFF:
            continue
        for scope in GOOGLE_PRODUCTS[product][level]:
            if scope not in scopes:
                scopes.append(scope)
    return scopes


def implied_levels(levels: dict[str, str]) -> dict[str, str]:
    """Floor Docs/Sheets at Drive's level.

    Google's Drive scopes are valid authorization for the Docs and Sheets
    APIs (drive.readonly reads document content; drive writes it), so a card
    claiming "Docs: off" while Drive is granted would be a lie. Every level
    comparison and projection runs on this closure.
    """
    implied = dict(levels)
    drive_rank = _LEVEL_RANK[implied["drive"]]
    for product in ("docs", "sheets"):
        if _LEVEL_RANK[implied[product]] < drive_rank:
            implied[product] = implied["drive"]
    return implied


def levels_from_scope_string(scope: str) -> dict[str, str]:
    """Project a granted Google scope string onto the product→level capability map.

    Unknown scopes are ignored (Google canonicalizes, merges project-wide
    grants, and may add scopes we never requested); a write marker wins over a
    read scope when both are present (incremental auth leaves both behind).
    """
    granted = set(scope.split())
    levels: dict[str, str] = {}
    for product, spec in GOOGLE_PRODUCTS.items():
        if granted.intersection(spec["write_markers"]):
            levels[product] = LEVEL_WRITE
        elif granted.intersection(spec["read"]):
            levels[product] = LEVEL_READ
        else:
            levels[product] = LEVEL_OFF
    return implied_levels(levels=levels)


def is_narrowing(requested: dict[str, str], granted: dict[str, str]) -> bool:
    """True when any product's requested level is below its granted level.

    A plain scope set-difference misclassifies read→write upgrades as
    narrowing (readonly drops out of the requested set); comparing capability
    ranks per product does not. Both sides are compared on the implied
    closure: dropping docs:read while keeping drive:read removes no actual
    capability, so it must not trigger the destructive narrow path.
    """
    requested_implied = implied_levels(levels=requested)
    granted_implied = implied_levels(levels=granted)
    return any(
        _LEVEL_RANK[requested_implied[product]] < _LEVEL_RANK[granted_implied[product]]
        for product in GOOGLE_PRODUCTS
    )


def grants_outcome_metadata(integration: IntegrationUserCredential) -> dict:
    """Build the `metadata.google_grants` projection the WebUI card renders."""
    scope = integration.config.get("scope", "")
    return {
        "google_grants": {
            "products": levels_from_scope_string(scope=scope),
            "raw_scopes": scope.split(),
            "google_email": integration.metadata.get("google_email", ""),
        }
    }


def _pick_redirect_uri(request: HttpRequest, configured: list[str]) -> str:
    """Return the registered redirect URI whose host matches *request*'s host.

    Google validates the `redirect_uri` parameter exactly — it must be byte-identical
    to one of the OAuth client's registered URIs, AND it must be byte-identical at
    token-exchange time to what we sent on /authorize. Both the start view (browser)
    and the callback view (browser round-trip from Google) hit the same host, so
    we can recompute the choice on each step by looking at request.get_host().
    """
    request_host = request.get_host().lower()
    for uri in configured:
        if urlparse(uri).netloc.lower() == request_host:
            return uri
    raise ValueError(
        f"No redirect_uri configured for host {request_host!r}. Available: {configured!r}"
    )


def _stash_flow(request: HttpRequest, state: str, payload: dict) -> None:
    """Store a pending OAuth flow under its `state`, pruning the oldest beyond the cap.

    State-keyed (rather than one payload slot) so two tabs don't invalidate
    each other's pending flow.
    """
    flows = dict(request.session.get("google_oauth_flows", {}))
    flows[state] = {**payload, "created_at": time.time()}
    while len(flows) > _MAX_PENDING_FLOWS:
        oldest = min(flows, key=lambda key: flows[key]["created_at"])
        del flows[oldest]
    request.session["google_oauth_flows"] = flows


def _pop_flow(request: HttpRequest, state: str) -> dict | None:
    """Remove and return the pending flow for `state`, leaving other tabs' flows intact."""
    flows = dict(request.session.get("google_oauth_flows", {}))
    payload = flows.pop(state, None)
    request.session["google_oauth_flows"] = flows
    return payload


def _resolve_start_context(request: HttpRequest, params: dict) -> tuple[dict | None, HttpResponse | None]:
    """Shared validation for the start GET and narrow POST: rd, app, config, selection.

    Returns (context, None) on success or (None, error_response). Context keys:
    rd, env, app_slug, google_cfg, selection, requested_scopes.
    """
    rd = params.get("rd", "")
    env = provider_common.resolve_env_by_rd(rd=rd, user=request.user)
    if env is None:
        return None, HttpResponseBadRequest("Invalid or unknown rd")
    app_slug = provider_common.resolve_owned_app_slug(
        app_slug=params.get("app_slug", ""),
        env=env,
        owner_username=request.user.username,
    )
    if app_slug is None:
        return None, HttpResponseBadRequest("Invalid or unauthorized app_slug")

    try:
        google_cfg = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.GOOGLE)
    except IntegrationConfig.DoesNotExist:
        logger.error("google integration start failed: IntegrationConfig(provider=google) missing")
        return None, HttpResponseBadRequest(
            "Google integration not configured. Run: uv run manage.py setup_google_oauth_client --file <json>"
        )

    selection = parse_products_param(raw=params.get("products"))
    if selection is None:
        return None, HttpResponseBadRequest("Invalid products selection")

    return {
        "rd": rd,
        "env": env,
        "app_slug": app_slug,
        "google_cfg": google_cfg,
        "selection": selection,
        "requested_scopes": scopes_for_selection(selection=selection),
    }, None


def _token_fingerprint(refresh_token: str) -> str:
    """SHA-256 of a refresh_token: lets the callback pin its no-refresh-token fallback to the exact credential version seen at /start."""
    return hashlib.sha256(refresh_token.encode()).hexdigest()


def _redirect_to_consent(request: HttpRequest, context: dict, mode: str, prior_row: IntegrationUserCredential | None) -> HttpResponse:
    """Stash the pending flow and 302 to Google's consent screen.

    `mode` is `connect` (initial/expansion; incremental auth ON) or `narrow`
    (post-revocation fresh grant; incremental auth OFF so the new grant is
    limited to what we request — with the old grant revoked, merging is both
    unwanted and meaningless). `prior_row` (connect mode only) is captured as
    id + token fingerprint so the callback can preserve the still-live token
    if Google omits a new one — and ONLY that exact token: a concurrent flow
    may replace the row meanwhile, and preserving its token under our scopes
    and identity would stitch two grants together.
    """
    web = context["google_cfg"].config
    try:
        redirect_uri = _pick_redirect_uri(request=request, configured=web["redirect_uris"])
    except ValueError as exc:
        logger.error("google oauth start failed: %s", exc)
        return HttpResponseBadRequest(
            "Google OAuth client has no redirect_uri registered for this host."
        )

    prior_token = prior_row.credentials.get("refresh_token", "") if prior_row is not None else ""
    state = secrets.token_urlsafe(32)
    _stash_flow(request=request, state=state, payload={
        "rd": context["rd"],
        "env_id": str(context["env"].id),
        "app_slug": context["app_slug"],
        "owner_username": request.user.username,
        "mode": mode,
        "selection": context["selection"],
        "requested_scopes": context["requested_scopes"],
        "prior_row_id": str(prior_row.id) if prior_row is not None else "",
        "prior_token_sha256": _token_fingerprint(refresh_token=prior_token) if prior_token else "",
    })

    params = {
        "client_id": web["client_id"],
        "response_type": "code",
        "scope": " ".join(context["requested_scopes"]),
        "redirect_uri": redirect_uri,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    if mode == "connect":
        params["include_granted_scopes"] = "true"
    return redirect(f"{web['auth_uri']}?{urlencode(params)}")


def _existing_row(user: User, env: Environment, app_slug: str) -> IntegrationUserCredential | None:
    """Fetch the user's Google credential row for this env+app, if any."""
    return IntegrationUserCredential.objects.filter(
        owner_user=user,
        environment=env,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.GOOGLE,
    ).first()


@login_required
def integrations_user_google_start(request: HttpRequest) -> HttpResponse:
    """Validate params; redirect to Google consent, or confirm first when narrowing.

    Narrowing is never executed from this GET — revocation is irreversible and
    GETs get prefetched. The confirmation page POSTs to the narrow view.
    """
    context, error = _resolve_start_context(request=request, params=request.GET)
    if error is not None:
        return error

    row = _existing_row(user=request.user, env=context["env"], app_slug=context["app_slug"])
    if row is not None:
        granted = levels_from_scope_string(scope=row.config.get("scope", ""))
        if is_narrowing(requested=context["selection"], granted=granted):
            requested_implied = implied_levels(levels=context["selection"])
            reduced = [
                GOOGLE_PRODUCTS[product]["label"]
                for product in GOOGLE_PRODUCTS
                if _LEVEL_RANK[requested_implied[product]] < _LEVEL_RANK[granted[product]]
            ]
            return render(request, "humanityrules_app/integrations/google_narrow_confirm.html", context={
                "reduced_labels": reduced,
                "rd": context["rd"],
                "app_slug": context["app_slug"],
                "products": serialize_selection(selection=context["selection"]),
                "google_email": row.metadata.get("google_email", ""),
            })

    return _redirect_to_consent(request=request, context=context, mode="connect", prior_row=row)


@login_required
@require_POST
def integrations_user_google_narrow(request: HttpRequest) -> HttpResponse:
    """Execute a confirmed scope narrowing: strict revoke, delete row, fresh consent.

    Revocation at Google is project-global (it kills the account's grant for
    our OAuth client everywhere, including other HUMR apps), so it only runs
    here — behind login + CSRF + an explicit confirmation POST — and only
    proceeds to consent when Google confirmed the revoke. Everything that can
    fail without side effects (params, config, redirect_uri) is checked
    BEFORE revoking; a stale confirmation whose row no longer needs narrowing
    is rerouted through the ordinary connect flow instead.
    """
    context, error = _resolve_start_context(request=request, params=request.POST)
    if error is not None:
        return error
    # Preflight the consent redirect: failing on redirect_uri AFTER revoking
    # would strand the user disconnected on a config error.
    try:
        _pick_redirect_uri(request=request, configured=context["google_cfg"].config["redirect_uris"])
    except ValueError as exc:
        logger.error("google narrow preflight failed: %s", exc)
        return HttpResponseBadRequest(
            "Google OAuth client has no redirect_uri registered for this host."
        )

    # The row lock serializes this revoke+delete against a concurrent OAuth
    # callback's update_or_create on the same row: the callback waits until
    # we commit, so it can't slip a fresh token in between the revoke and the
    # delete (which would orphan it — revocation is project-global).
    with transaction.atomic():
        row = (
            IntegrationUserCredential.objects.select_for_update()
            .filter(
                owner_user=request.user,
                environment=context["env"],
                app_slug=context["app_slug"],
                provider=IntegrationUserCredential.Provider.GOOGLE,
            )
            .first()
        )
        refresh_token = row.credentials.get("refresh_token", "") if row is not None else ""
        granted = levels_from_scope_string(scope=row.config.get("scope", "")) if row is not None else {}
        still_narrowing = (
            row is not None
            and bool(refresh_token)
            and is_narrowing(requested=context["selection"], granted=granted)
        )
        if not still_narrowing:
            # Stale confirmation (row gone, or already narrower): nothing to
            # revoke — this is now an ordinary connect/expansion.
            return _redirect_to_consent(request=request, context=context, mode="connect", prior_row=row)

        if not _revoke_strict(refresh_token=refresh_token):
            return HttpResponseBadRequest(
                "Google did not confirm the revocation; nothing was changed. Try again."
            )
        # Tombstone rather than delete: the kept row's `revoked_at_epoch` is
        # the durable generation marker that lets the callback reject ANY
        # flow started before this revocation — including an initial connect
        # from another session, which carries no prior_row_id and could
        # otherwise resurrect the revoked grant via update_or_create.
        # Empty credentials read as a silent `absent` on refresh; the narrow's
        # own callback overwrites the tombstone on success.
        row.credentials = {}
        row.config = {"scope": ""}
        row.metadata = {**row.metadata, "revoked_at_epoch": time.time()}
        row.save(update_fields=["credentials", "config", "metadata", "updated_at"])

    # Every pending flow for this target predates the revocation, so its
    # consent (and any token it would store) is now void — drop them so a
    # forgotten consent tab can't complete later and resurrect the credential
    # this narrow just destroyed. The callback's generation guard covers flows
    # from other sessions.
    flows = {
        state: pending
        for state, pending in dict(request.session.get("google_oauth_flows", {})).items()
        if not (pending.get("env_id") == str(context["env"].id) and pending.get("app_slug") == context["app_slug"])
    }
    request.session["google_oauth_flows"] = flows

    return _redirect_to_consent(request=request, context=context, mode="narrow", prior_row=None)


def _exchange_google_code(web: dict, code: str, redirect_uri: str) -> dict:
    """POST to Google's token endpoint and return the JSON body.

    *redirect_uri* must be byte-identical to what was sent on the /authorize step;
    Google rejects mismatches with invalid_grant.
    """
    response = httpx.post(
        web["token_uri"],
        data={
            "client_id": web["client_id"],
            "client_secret": web["client_secret"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _redirect_flow_failure(rd: str, mode: str, error_code: str) -> HttpResponse:
    """Send the browser back to the WebUI with the right sentinel for a dead flow.

    A failed `connect` flow changed nothing — a plain error sentinel suffices.
    A failed `narrow` flow already revoked the grant and deleted the row, so
    the card's cached "connected" state is now wrong: send the `disconnected`
    transition (the WebUI invalidates the broker cache on it) alongside a
    narrow-specific error so the modal says what actually happened.
    """
    if mode == "narrow":
        return redirect(provider_common.append_query(
            url=rd, extra={"disconnected": "google", "error": "google_narrow_incomplete"},
        ))
    return redirect(provider_common.append_query(url=rd, extra={"error": error_code}))


@login_required
def integrations_user_google_callback(request: HttpRequest) -> HttpResponse:
    """Exchange Google's auth code, persist refresh_token + grants, 302 back to `rd`.

    Failures after state validation redirect to `rd` with an `error` sentinel
    (the WebUI shows it on the card) rather than a bare CP error page — by
    then `rd` is trusted (validated at /start against the user's own envs).
    """
    state = request.GET.get("state", "")
    payload = _pop_flow(request=request, state=state) if state else None
    if not isinstance(payload, dict):
        payload = None
    google_error = request.GET.get("error", "")

    if payload is None:
        if google_error:
            logger.error("google oauth callback error=%s (unknown state)", google_error)
            return HttpResponseBadRequest(f"Google OAuth error: {google_error}")
        return HttpResponseBadRequest("Missing code or unknown state")

    rd = payload.get("rd", "")
    env_id = payload.get("env_id", "")
    app_slug = payload.get("app_slug", "")
    owner_username = payload.get("owner_username", "")
    mode = payload.get("mode", "")
    if not rd or not env_id or not app_slug or not owner_username or mode not in ("connect", "narrow"):
        return HttpResponseBadRequest("Corrupt session payload")

    # Defense in depth: the authenticated user must own the session payload.
    # Prevents a cross-user race from writing the row under the wrong owner.
    # Deliberately a bare 400 (not an rd redirect): with mismatched identities
    # nothing about the payload should steer this user's browser. This runs
    # BEFORE the provider-error branch so a Google error can't bypass it.
    if owner_username != request.user.username:
        logger.error(
            "google callback user mismatch session_user=%s request_user=%s",
            owner_username, request.user.username,
        )
        return HttpResponseBadRequest("User mismatch")

    # From here on the payload is trusted — every terminal failure must go
    # through the mode-aware sentinel: after a narrow the grant is already
    # revoked and the row tombstoned, so a bare CP 400 would leave the WebUI's
    # cached card claiming "connected" about a credential that no longer exists.
    if google_error:
        logger.error("google oauth callback error=%s", google_error)
        return _redirect_flow_failure(rd=rd, mode=mode, error_code="google_denied")

    code = request.GET.get("code", "")
    if not code:
        logger.error("google callback failed: missing code env_id=%s user=%s", env_id, request.user.username)
        return _redirect_flow_failure(rd=rd, mode=mode, error_code="google_exchange_failed")

    try:
        google_cfg = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.GOOGLE)
    except IntegrationConfig.DoesNotExist:
        logger.error("google callback failed: IntegrationConfig(provider=google) missing")
        return _redirect_flow_failure(rd=rd, mode=mode, error_code="google_exchange_failed")

    # Org-scoped: membership may have changed mid-flow; an env the user can no
    # longer reach must not accept the credential.
    env = Environment.objects.filter(
        id=env_id, aws_account__organization__memberships__user=request.user,
    ).first()
    if env is None:
        logger.error("google callback failed: env_id=%s not found for user=%s", env_id, request.user.username)
        return _redirect_flow_failure(rd=rd, mode=mode, error_code="google_exchange_failed")
    if provider_common.resolve_owned_app_slug(app_slug=app_slug, env=env, owner_username=request.user.username) is None:
        logger.error("google callback failed: app ownership lost app=%s user=%s", app_slug, request.user.username)
        return _redirect_flow_failure(rd=rd, mode=mode, error_code="google_exchange_failed")

    try:
        redirect_uri = _pick_redirect_uri(
            request=request, configured=google_cfg.config["redirect_uris"],
        )
    except ValueError as exc:
        logger.error("google callback failed: %s", exc)
        return _redirect_flow_failure(rd=rd, mode=mode, error_code="google_exchange_failed")

    try:
        token_response = _exchange_google_code(
            web=google_cfg.config, code=code, redirect_uri=redirect_uri,
        )
    except Exception as exc:
        logger.error("google token exchange failed: %s", exc)
        return _redirect_flow_failure(rd=rd, mode=mode, error_code="google_exchange_failed")

    # The id_token arrives over the direct CP↔Google TLS exchange, so its
    # claims are trustworthy without local signature verification.
    id_claims = provider_common.decode_jwt_payload(token=token_response.get("id_token", ""))
    row_config = {"scope": token_response.get("scope", "")}
    row_metadata = {
        "connected_at": timezone.now().isoformat(),
        "google_email": str(id_claims.get("email", "")),
        "google_sub": str(id_claims.get("sub", "")),
    }
    refresh_token = token_response.get("refresh_token", "")

    # Generation guard + write, one locked transaction (the narrow POST and
    # disconnect serialize on the same row lock, so the guard's read can't go
    # stale before the write commits). A flow must not complete if the target
    # credential was destructively changed after the flow started — its
    # consent predates that revocation, and completing it would resurrect a
    # grant the user just destroyed, possibly with broader pre-narrow scopes
    # (include_granted_scopes was on when it started). Two signals:
    #   - a revocation tombstone (narrow or disconnect) newer than the flow —
    #     covers ANY earlier flow, including initial connects from other
    #     sessions and pending narrows completing after a disconnect;
    #   - a captured prior row whose pk no longer matches (delete+recreate).
    with transaction.atomic():
        # Lock ALL of this user's Google rows (pk-ordered, so concurrent
        # callbacks can't deadlock), not just this flow's target: Google
        # revocation is ACCOUNT-global, so a narrow/disconnect on any other
        # HUMR app connected to the same Google account also voids this
        # flow's consent. Rows whose recorded google_sub differs from this
        # consent's account are exempt; rows with no recorded sub count
        # conservatively.
        account_rows = list(
            IntegrationUserCredential.objects.select_for_update()
            .filter(owner_user=request.user, provider=IntegrationUserCredential.Provider.GOOGLE)
            .order_by("pk")
        )
        current_row = next(
            (r for r in account_rows if r.environment_id == env.id and r.app_slug == app_slug),
            None,
        )
        consent_sub = str(id_claims.get("sub", ""))
        account_epochs = [
            r.metadata.get("revoked_at_epoch")
            for r in account_rows
            if not consent_sub
            or not r.metadata.get("google_sub")
            or str(r.metadata.get("google_sub")) == consent_sub
        ]
        revoked_at = max(
            (epoch for epoch in account_epochs if isinstance(epoch, (int, float))),
            default=None,
        )
        flow_created_at = float(payload.get("created_at") or 0)
        revoked_since_start = revoked_at is not None and revoked_at > flow_created_at
        prior_row_gone = mode == "connect" and bool(payload.get("prior_row_id")) and (
            current_row is None or str(current_row.id) != payload["prior_row_id"]
        )
        if revoked_since_start or prior_row_gone:
            logger.error(
                "google callback failed: stale flow (revoked=%s prior_gone=%s) env=%s user=%s app=%s mode=%s",
                revoked_since_start, prior_row_gone, env.slug, request.user.username, app_slug, mode,
            )
            if revoked_since_start:
                # The code exchange above already recreated a grant at Google
                # that the user's narrow/disconnect promised dead — kill it.
                # Project-global revocation may also fell a concurrent fresh
                # grant; that self-heals into a VISIBLE disconnect on the next
                # refresh, whereas a lingering hidden grant never would.
                _revoke_exchanged_grant(token_response=token_response)
            return _redirect_flow_failure(rd=rd, mode=mode, error_code="google_stale_flow")

        # `revoked_at_epoch` is a permanent high-water mark: carry it onto
        # every rewrite so a later-completing flow that started BEFORE the
        # last revocation still trips the guard above. (A flow started after
        # it passes regardless — the guard compares against its created_at.)
        if isinstance(revoked_at, (int, float)):
            row_metadata = {**row_metadata, "revoked_at_epoch": revoked_at}

        if refresh_token:
            IntegrationUserCredential.objects.update_or_create(
                owner_user=request.user,
                environment=env,
                app_slug=app_slug,
                provider=IntegrationUserCredential.Provider.GOOGLE,
                defaults={
                    "credentials": {"refresh_token": refresh_token},
                    "config": row_config,
                    "metadata": row_metadata,
                    "last_refreshed_at": None,
                },
            )
        else:
            # Google issues a new refresh_token on `prompt=consent` grants,
            # but does not guarantee it. On an expansion re-consent the prior
            # row's token is still valid (nothing was revoked), so preserve
            # it — but ONLY the exact token fingerprinted at /start: a
            # concurrent flow may have replaced the row, and adopting its
            # token under this flow's scopes and identity would stitch two
            # different grants together. `current_row` is locked, so the
            # fingerprint check and the save are atomic.
            prior_fingerprint = payload.get("prior_token_sha256", "")
            prior_token = current_row.credentials.get("refresh_token", "") if current_row is not None else ""
            prior_sub = str(current_row.metadata.get("google_sub", "")) if current_row is not None else ""
            preservable = (
                mode == "connect"
                and current_row is not None
                and str(current_row.id) == payload.get("prior_row_id", "")
                and bool(prior_token)
                and bool(prior_fingerprint)
                and _token_fingerprint(refresh_token=prior_token) == prior_fingerprint
                # The preserved token must belong to the SAME Google account as
                # this consent — an account switch would otherwise stitch the
                # old account's token to the new account's identity and scopes.
                and bool(consent_sub)
                and prior_sub == consent_sub
            )
            if not preservable:
                logger.error(
                    "google token exchange returned no refresh_token and no preservable prior row env=%s user=%s mode=%s",
                    env.slug, owner_username, mode,
                )
                return _redirect_flow_failure(rd=rd, mode=mode, error_code="google_no_refresh_token")
            current_row.config = row_config
            current_row.metadata = row_metadata
            current_row.last_refreshed_at = None
            current_row.save(update_fields=["config", "metadata", "last_refreshed_at", "updated_at"])

    logger.info(
        "google integration stored env=%s owner=%s app=%s mode=%s scope=%s",
        env.slug, owner_username, app_slug, mode, token_response.get("scope", ""),
    )

    return redirect(provider_common.append_query(url=rd, extra={"connected": "google"}))


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for one (env, owner, app).

    Returns a `provider_common` outcome dict where `outcome` is
    `has_token | absent | transient`. The `absent` (disconnected) path is
    intentionally silent — the broker asks every Refresh-all/bootstrap, and
    most providers are typically disconnected, so logging that as an error
    would be log spam.
    """
    try:
        google_cfg = IntegrationConfig.objects.get(provider=IntegrationConfig.Provider.GOOGLE)
    except IntegrationConfig.DoesNotExist:
        logger.error("google token refresh failed: IntegrationConfig(provider=google) missing")
        return provider_common.transient_outcome()

    def build_secrets(access_token: str, response: dict) -> provider_common.RefreshSecrets:
        # Google's refresh response reports the access token's effective
        # scope — the authoritative grant, which project-wide merging or a
        # partial consent elsewhere can change after connect time. Persist it
        # so the card's grant projection (and later narrowing decisions)
        # track reality rather than the connect-time snapshot.
        scope = str(response.get("scope") or "")
        return provider_common.RefreshSecrets(
            secrets={"access_token": access_token},
            expires_in=int(response.get("expires_in", 0)),
            row_metadata={},
            row_config={"scope": scope} if scope else {},
        )

    return provider_common.run_refresh_exchange(
        provider=IntegrationUserCredential.Provider.GOOGLE,
        logger=logger,
        environment=environment,
        owner_user=owner_user,
        app_slug=app_slug,
        exchange=lambda refresh_token: _exchange_refresh_token(web=google_cfg.config, refresh_token=refresh_token),
        build_secrets=build_secrets,
        outcome_metadata=grants_outcome_metadata,
        tombstone_on_revoke=True,
    )


def _exchange_refresh_token(web: dict, refresh_token: str) -> provider_common.ExchangeResult:
    """POST to Google's token endpoint with grant_type=refresh_token.

    Google's refresh-token rejection returns 400 `invalid_grant`; treat that as
    revoked (the stored token is unusable, caller deletes the row). Any other
    non-200 / network / non-JSON failure is transient.
    """
    return provider_common.exchange_refresh_token(
        send=lambda: httpx.post(
            web["token_uri"],
            data={
                "client_id": web["client_id"],
                "client_secret": web["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=GOOGLE_TOKEN_EXCHANGE_TIMEOUT_SECONDS,
        ),
        revoking_statuses=frozenset({400}),
        revoking_error_codes=frozenset({"invalid_grant"}),
        error_code_of=lambda body: str(body.get("error") or ""),
    )


def revoke(refresh_token: str) -> None:
    """Best-effort revoke at Google's oauth2 endpoint.

    Failure doesn't block the local deletion — the row removal is the
    load-bearing step. Revoke just accelerates Google-side cleanup so a
    subsequent refresh would 410 instead of silently succeeding in an edge
    case where we somehow missed the local delete.
    """
    try:
        httpx.post(
            GOOGLE_REVOKE_URL,
            data={"token": refresh_token},
            timeout=10,
        )
    except Exception as exc:
        logger.error("google revoke best-effort failed: %s", exc)


def _revoke_exchanged_grant(token_response: dict) -> None:
    """Best-effort revoke of a grant we exchanged but refused to store.

    A stale flow's code exchange recreates the authorization at Google even
    though HUMR rejects it locally; without this the consent would linger,
    invisible, with its pre-revocation scopes. Google's /revoke accepts either
    token type and revokes the underlying grant.
    """
    token = token_response.get("refresh_token") or token_response.get("access_token") or ""
    if token:
        revoke(refresh_token=token)


def _revoke_strict(refresh_token: str) -> bool:
    """Revoke at Google and return True only when Google confirmed it (HTTP 200).

    The narrow flow's security promise ("your grant was reduced") depends on
    the old grant actually dying, so unlike `revoke` this checks the response.
    """
    try:
        response = httpx.post(
            GOOGLE_REVOKE_URL,
            data={"token": refresh_token},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        logger.error("google strict revoke failed: %s", exc)
        return False
    if response.status_code != 200:
        logger.error("google strict revoke rejected: http %s", response.status_code)
        return False
    return True
