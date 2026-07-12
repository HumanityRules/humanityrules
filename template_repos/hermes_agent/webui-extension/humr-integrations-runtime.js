// The extension is split across the humr-integrations-*.js scripts listed in
// manifest.json (load order matters). This first-loaded runtime bootstraps the
// shared window.HumrIntegrations namespace: state, registries, broker client
// helpers, WebUI helpers, modals, and oauth sentinels. 
// Broker calls go same-origin via Caddy's /__humr_broker/* route.
//
(() => {
  'use strict';

  const INTEGRATIONS_URL = '/__humr_broker/integrations';
  const state = { current: null, disconnecting: new Set() };

  // ── Registries ────────────────────────────────────────────────────────

  // Card behavior is registered once per broker kind, with optional
  // kind+slug specializations for integrations whose UI differs from the kind
  // default (Google's scope picker, Slack's custom vault form). Resolution
  // prefers the exact specialization and otherwise falls back to the kind
  // factory.
  const _cardSpecFactories = new Map();

  function cardSpecSelectorKey(selector) {
    return selector.kind + '\u0000' + (selector.slug || '');
  }

  // Registry of cardSpec factories. Each factory contributes provider- or
  // mechanism-specific details and actions; resolve() binds those to the
  // integration's shared presentation data and returns one complete cardSpec.
  const cardSpecs = {
    register(selector, factory) {
      if (!selector || !selector.kind || typeof factory !== 'function') {
        throw new Error('[humr-integrations] cardSpecs.register() needs a kind and factory.');
      }
      const key = cardSpecSelectorKey(selector);
      if (_cardSpecFactories.has(key)) {
        const suffix = selector.slug ? ':' + selector.slug : '';
        console.warn('[humr-integrations] Replacing card specification for ' + selector.kind + suffix + '.');
      }
      _cardSpecFactories.set(key, factory);
    },
  
    resolve(integration, page) {
      if (!integration || !integration.kind) return null;
      const exact = integration.slug
        ? _cardSpecFactories.get(cardSpecSelectorKey({ kind: integration.kind, slug: integration.slug }))
        : null;
      const fallback = _cardSpecFactories.get(cardSpecSelectorKey({ kind: integration.kind }));
      const factory = exact || fallback;
      if (!factory) return null;

      const cardSpec = factory(integration, page);
      if (!cardSpec) return null;
      const metadata = integration.metadata || {};
      let provisionLabel = null;
      // Platform is the lowest-priority shared credential source, so if it
      // stamped the outcome no organization or personal credential applied.
      if (metadata.platform_shared) provisionLabel = 'Provided by Humanity Rules';
      else if (metadata.org_shared) provisionLabel = 'Provided by your organization';

      // Factories supply mechanism/provider-specific details and actions. The
      // registry binds those to the common presentation data so consumers deal
      // with one resolved cardSpec rather than integration + page + behavior.
      return {
        key: integrationKey(integration),
        label: integration.label || integration.slug,
        logoUrl: integration.logo_url || null,
        status: integration.status,
        isConnected: integration.status === 'connected',
        provisionLabel,
        ...cardSpec,
      };
    },
  };

  const _oauthErrorMessages = new Map();

  function registerOauthErrors(map) {
    for (const code in map) {
      if (_oauthErrorMessages.has(code)) console.warn('[humr-integrations] Replacing OAuth error message for ' + code + '.');
      _oauthErrorMessages.set(code, map[code]);
    }
  }

  function oauthErrorMessage(code) {
    return _oauthErrorMessages.get(code);
  }

  function integrationKey(integration) {
    return String(integration.kind || 'unknown') + ':' + String(integration.slug || 'unknown');
  }

  function elem(tag, props, children) {
    const el = document.createElement(tag);
    if (props) {
      for (const k in props) {
        if (k === 'style' && typeof props[k] === 'object') Object.assign(el.style, props[k]);
        else if (k === 'dataset' && typeof props[k] === 'object') Object.assign(el.dataset, props[k]);
        else if (k.startsWith('on') && typeof props[k] === 'function') el.addEventListener(k.slice(2), props[k]);
        else if (k === 'disabled') el.disabled = !!props[k];
        else el.setAttribute(k, props[k]);
      }
    }
    if (children) {
      for (const c of children) {
        if (c == null) continue;
        el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
      }
    }
    return el;
  }

  function formatDate(iso) {
    if (!iso) return '';
    try { return new Date(iso).toLocaleString(); } catch (_) { return iso; }
  }

  async function fetchIntegrations() {
    try {
      const response = await fetch(INTEGRATIONS_URL, { cache: 'no-store' });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  async function refreshAll() {
    return await fetch('/__humr_broker/integrations/refresh_all', {
      method: 'POST',
      cache: 'no-store',
    });
  }

  // TLS-intercept providers expose /integrations/user/<slug>/start/ for Connect
  // (top-level navigation to HUMR). Disconnect goes through the broker.
  function buildTlsConnectUrl(catalog, slug, returnTo) {
    const rd = encodeURIComponent(returnTo);
    return catalog.humr_control_plane_url.replace(/\/$/, '') + '/integrations/user/' + slug + '/start/?rd=' + rd + '&app_slug=' + encodeURIComponent(catalog.app_slug || '');
  }

  function tlsInterceptPath(slug, action) {
    return '/__humr_broker/integrations/tls_intercept/' + encodeURIComponent(slug) + '/' + action;
  }

  function mcpPath(slug, action) {
    return '/__humr_broker/integrations/mcp/' + encodeURIComponent(slug) + '/' + action;
  }

  function buildMcpConnectUrl(slug) {
    const returnTo = encodeURIComponent(window.location.origin + window.location.pathname);
    const origin = encodeURIComponent(window.location.origin);
    return mcpPath(slug, 'oauth/start') + '?return_to=' + returnTo + '&origin=' + origin;
  }

  function statusLabelFor(status) {
    switch (status) {
      case 'connected': return 'Connected';
      case 'not_connected': return 'Not connected';
      case 'token_expired': return 'Token expired';
      case 'transient_error': return 'Checking…';
      case 'starting': return 'Starting…';
      default: return '—';
    }
  }

  // Provider logos are served from the HumR extension bundle.
  // Connecting/disconnecting a provider whose env the broker manages (e.g.
  // GitHub) restarts system.webui, so any <img> requested during that reboot
  // window fails and the browser never retries it on its own — leaving blank
  // logos. The connect-return flow already waits for the WebUI before
  // rendering, but the in-page Disconnect button re-renders during the reboot
  // with no such gate. Self-heal: on error, re-request with a cache-busting
  // param (so the browser doesn't serve the failed entry) on a capped backoff
  // until the WebUI is back. Covers every reboot trigger.
  function logoImg(url) {
    const MAX_RETRIES = 10;
    let tries = 0;
    const img = elem('img', {
      class: 'humr-integration-logo',
      src: url,
      alt: '',
      decoding: 'async',
    });
    img.addEventListener('error', () => {
      if (tries >= MAX_RETRIES) return;
      tries += 1;
      const delay = Math.min(3000, 300 * Math.pow(2, tries - 1));
      setTimeout(() => {
        const sep = url.indexOf('?') === -1 ? '?' : '&';
        img.src = url + sep + '_retry=' + tries;
      }, delay);
    });
    return img;
  }

  // Resolve once every logo currently rendered in the list has finished
  // loading (or errored out), capped by `timeoutMs`. Used before triggering a
  // WebUI restart: once the logos are in the browser cache, the later
  // re-render reuses them, so the restart can't blank them.
  function waitForLogos(timeoutMs) {
    const list = document.getElementById('humrIntegrationList');
    const imgs = list ? Array.from(list.querySelectorAll('.humr-integration-logo')) : [];
    const pending = imgs.filter((img) => !img.complete);
    if (pending.length === 0) return Promise.resolve();
    return new Promise((resolve) => {
      let remaining = pending.length;
      const done = () => { remaining -= 1; if (remaining === 0) resolve(); };
      pending.forEach((img) => {
        img.addEventListener('load', done, { once: true });
        img.addEventListener('error', done, { once: true });
      });
      setTimeout(resolve, timeoutMs);
    });
  }

  // Resolve once the WebUI's static handler is serving. We poll a known
  // WebUI-served asset (our own stylesheet) until it loads. Cache-busted so the
  // browser can't answer from a stale entry; capped by `timeoutMs`.
  function waitForWebui(timeoutMs) {
    const probeUrl = '/extensions/humr/humr-integrations.css';
    const start = Date.now();
    return new Promise((resolve) => {
      const attempt = () => {
        fetch(probeUrl + '?_probe=' + Date.now(), { method: 'GET', cache: 'no-store' })
          .then((r) => {
            if (r.ok) { resolve(); return; }
            throw new Error('not ready');
          })
          .catch(() => {
            if (Date.now() - start >= timeoutMs) { resolve(); return; }
            setTimeout(attempt, 400);
          });
      };
      attempt();
    });
  }

  // Order within a status group: model providers first, then connectors, then
  // alphabetical by label. (Cards are split into connected / not-connected
  // grids upstream of this, so status isn't a key here.)
  function byCategoryThenLabel(a, b) {
    const aRank = a.category === 'model_provider' ? 0 : 1;
    const bRank = b.category === 'model_provider' ? 0 : 1;
    if (aRank !== bRank) return aRank - bRank;
    const aLabel = (a.label || a.slug || '').toLowerCase();
    const bLabel = (b.label || b.slug || '').toLowerCase();
    return aLabel.localeCompare(bLabel);
  }

  function modelProviderForOption(option) {
    if (!option) return '';
    if (option.dataset && option.dataset.provider) return option.dataset.provider;
    const group = option.parentElement;
    if (group && group.dataset && group.dataset.provider) return group.dataset.provider;
    return '';
  }

  function applyModelsToSelect(select, modelsData) {
    if (!select || !modelsData || !Array.isArray(modelsData.groups)) return false;
    const previousOption = select.options[select.selectedIndex] || null;
    const previousValue = select.value || '';
    const previousProvider = modelProviderForOption(previousOption);
    const fragment = document.createDocumentFragment();
    for (const group of modelsData.groups) {
      const groupModels = Array.isArray(group.models) ? group.models : [];
      if (!groupModels.length) continue;
      const optgroup = document.createElement('optgroup');
      optgroup.label = group.provider || group.provider_id || 'Models';
      if (group.provider_id) optgroup.dataset.provider = group.provider_id;
      for (const model of groupModels) {
        if (!model || !model.id) continue;
        const option = document.createElement('option');
        option.value = model.id;
        option.textContent = model.label || model.id;
        if (group.provider_id) option.dataset.provider = group.provider_id;
        optgroup.appendChild(option);
      }
      if (optgroup.children.length) fragment.appendChild(optgroup);
    }
    if (!fragment.childNodes.length) return false;
    select.innerHTML = '';
    select.appendChild(fragment);

    let selectedOption = null;
    for (const option of Array.from(select.options)) {
      if (previousValue && option.value === previousValue && (!previousProvider || modelProviderForOption(option) === previousProvider)) {
        selectedOption = option;
        break;
      }
      if (!selectedOption && previousValue && option.value === previousValue) selectedOption = option;
    }
    if (!selectedOption && modelsData.default_model) {
      selectedOption = Array.from(select.options).find((option) => option.value === modelsData.default_model) || null;
    }
    if (!selectedOption) selectedOption = select.options[0] || null;
    if (selectedOption) selectedOption.selected = true;
    return true;
  }

  function applyModelsToKnownDropdowns(modelsData) {
    if (!modelsData) return false;
    if ('active_provider' in modelsData) window._activeProvider = modelsData.active_provider || null;
    if ('default_model' in modelsData) window._defaultModel = modelsData.default_model || null;
    if ('configured_model_badges' in modelsData) window._configuredModelBadges = modelsData.configured_model_badges || {};
    const refreshedComposer = applyModelsToSelect(document.getElementById('modelSelect'), modelsData);
    const refreshedSettings = applyModelsToSelect(document.getElementById('settingsModel'), modelsData);
    try {
      if (typeof syncModelChip === 'function') syncModelChip();
      const dropdown = document.getElementById('composerModelDropdown');
      if (dropdown && dropdown.classList.contains('open') && typeof renderModelDropdown === 'function') {
        renderModelDropdown();
        if (typeof _positionModelDropdown === 'function') _positionModelDropdown();
      }
    } catch (_) { /* best-effort */ }
    return refreshedComposer || refreshedSettings;
  }

  async function refreshModelDropdownsIfProviderAffectsPicker(integration) {
    if (!integration || !integration.affects_model_picker) return;
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const fetchModelsData = async () => {
      const response = await fetch('/api/models', {
        cache: 'no-store',
        credentials: 'include',
      });
      if (!response.ok) throw new Error('models unavailable');
      return response.json();
    };
    const waitForModelsData = async (timeoutMs) => {
      const start = Date.now();
      while (Date.now() - start < timeoutMs) {
        try {
          return await fetchModelsData();
        } catch (_) { /* retry until timeout */ }
        await wait(400);
      }
      try {
        return await fetchModelsData();
      } catch (_) {
        return null;
      }
    };
    const runBestEffort = async (fn) => {
      if (typeof fn !== 'function') return false;
      try {
        const result = fn();
        if (result && typeof result.then === 'function') await result;
        return true;
      } catch (_) {
        return false;
      }
    };
    try {
      await waitForWebui(15000);
      const modelsData = await waitForModelsData(15000);
      if (typeof window._invalidateSlashModelCache === 'function') {
        window._invalidateSlashModelCache();
      }
      if (typeof window._refreshModelDropdownsAfterProviderChange === 'function') {
        await runBestEffort(window._refreshModelDropdownsAfterProviderChange);
      }
      window._modelDropdownReady = null;
      await runBestEffort(window._ensureModelDropdownReady);
      if (typeof populateModelDropdown === 'function') await runBestEffort(populateModelDropdown);
      applyModelsToKnownDropdowns(modelsData);
    } catch (_) { /* best-effort */ }
  }

  // Floating "in progress" dialog shown after an oauth-sentinel return, while
  // the broker primes its cache (and any managed-env WebUI restart settles).
  // status_items() reads cache-only, so the first render after a connect would
  // otherwise paint a stale "not connected" card until the invalidate
  // round-trip lands. The dialog signals work-in-progress over the still-
  // visible page and blocks interaction; init() removes it once the real
  // render completes and logos have reloaded. No Cancel: the round-trip is
  // short and there's nothing to abort.
  function showTransitionModal(opts) {
    // On the connect-return path the catalog (with each provider's properly-
    // cased label) isn't loaded yet, so the default title stays provider-
    // agnostic to avoid mis-casing a brand name (e.g. "Github"). Callers that
    // already know the wording (e.g. Refresh-all) pass an explicit title/body.
    const title = (typeof opts.title === 'string')
      ? opts.title
      : ((opts.transition === 'disconnected' ? 'Disconnecting' : 'Finishing connection') + '…');
    const body = (typeof opts.body === 'string')
      ? opts.body
      : 'Syncing status with Humanity Rules. This will only take a moment.';
    // Transparent backdrop (humr-transition-backdrop): the dialog floats over
    // the page, which stays visible behind it, and blocks interaction. We do
    // NOT re-render the page while the dialog is up, so nothing behind it
    // changes (no stale cards, no blank logos) until the WebUI is back.
    const backdrop = elem('div', { class: 'humr-modal-backdrop humr-transition-backdrop' });
    const modal = elem('div', { class: 'humr-modal humr-transition-modal' }, [
      elem('div', { class: 'humr-transition-spinner' }),
      elem('div', { class: 'humr-modal-title' }, [title]),
      elem('div', { class: 'humr-modal-body' }, [body]),
    ]);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    return backdrop;
  }

  // Tell the broker to drop one provider's cached TLS-intercept token after a
  // known connect/disconnect/config change (vault save, oauth-sentinel return).
  // The explicit-Refresh-all path goes through /__humr_broker/integrations/refresh_all
  // instead, which fans out catalog reload + all-providers TLS invalidate.
  // Per-provider: POST .../tls_intercept/{slug}/invalidate.
  //
  // For env-backed providers the broker also rewrites the managed profile env
  // block and restarts whichever process-compose entries the provider declares.
  // Both failure modes (network error reaching the broker, or 5xx from the
  // broker) propagate to the caller. Cache-hint callers (oauth-sentinel return)
  // catch and ignore: the proxy's 401-evict path recovers stale tokens on the
  // first real call.
  async function invalidateTlsCache(providerSlug) {
    const url = tlsInterceptPath(providerSlug, 'invalidate');
    const response = await fetch(url, { method: 'POST' });
    if (response.ok) return;
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* ignore */ }
    throw new Error(
      payload.error ||
        'Saved, but applying the credentials failed. Redeploy this Hermes app to apply them.',
    );
  }

  // Error oauth-sentinel codes the CP appends to `rd` when an OAuth flow dies
  // after the consent redirect (cancelled at Google, exchange failure, missing
  // refresh token). Only codes registered by providers are consumed — an
  // unknown ?error= belongs to someone else and is left alone.

  // Drop any ?connected=/?disconnected=/?error= oauth sentinel once we've
  // acted on it, so a reload doesn't replay the refresh (or re-show the
  // error). A failed narrow arrives as disconnected+error TOGETHER: the state
  // really changed (grant revoked, row gone) AND the user needs to hear why,
  // so the transition oauth sentinel carries the error code along.
  function consumeOAuthSentinel() {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get('connected');
    const disconnected = params.get('disconnected');
    const errorCode = params.get('error');
    const knownError = errorCode && oauthErrorMessage(errorCode) ? errorCode : null;
    if (!connected && !disconnected && !knownError) return null;
    params.delete('connected');
    params.delete('disconnected');
    if (knownError) params.delete('error');
    const qs = params.toString();
    const newUrl = window.location.pathname + (qs ? '?' + qs : '') + window.location.hash;
    window.history.replaceState({}, '', newUrl);
    if (!connected && !disconnected) {
      return { transition: 'error', code: knownError };
    }
    return {
      transition: connected ? 'connected' : 'disconnected',
      provider: connected || disconnected,
      errorCode: knownError,
    };
  }

  function showOauthErrorModal(message) {
    const backdrop = elem('div', { class: 'humr-modal-backdrop' });
    const modal = elem('div', { class: 'humr-modal' }, [
      elem('div', { class: 'humr-modal-title' }, ['Connection not completed']),
      elem('div', { class: 'humr-modal-body' }, [message]),
      elem('div', { class: 'humr-modal-actions' }, [
        elem('button', {
          class: 'humr-integration-btn humr-integration-btn-primary',
          type: 'button',
          onclick: () => backdrop.remove(),
        }, ['OK']),
      ]),
    ]);
    backdrop.appendChild(modal);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) backdrop.remove(); });
    document.body.appendChild(backdrop);
  }

  window.HumrIntegrations = {
    loadedExtensionScripts: new Set(),
    state,
    util: { integrationKey, elem, formatDate, statusLabelFor, byCategoryThenLabel },
    broker: {
      fetchIntegrations,
      refreshAll,
      tlsInterceptPath,
      mcpPath,
      buildTlsConnectUrl,
      buildMcpConnectUrl,
      invalidateTlsCache,
    },
    webui: {
      logoImg,
      waitForLogos,
      waitForWebui,
      refreshModelDropdownsIfProviderAffectsPicker,
    },
    modals: { showTransitionModal, showOauthErrorModal },
    oauthSentinel: { consumeOAuthSentinel, registerOauthErrors, oauthErrorMessage },
    flows: {},
    cardSpecs,
  };
  window.HumrIntegrations.loadedExtensionScripts.add('runtime');
})();
