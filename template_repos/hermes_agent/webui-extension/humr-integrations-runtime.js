// The extension is split across the humr-integrations-*.js scripts listed in
// manifest.json (load order matters). This first-loaded file owns their shared
// namespace and helpers.
// Broker calls go same-origin via Caddy's /__humr_broker/* route.
//
(() => {
  'use strict';

  const INTEGRATIONS_URL = '/__humr_broker/integrations';
  const state = { current: null };

  // Stable render-lifecycle collaborator shared by the split integration
  // scripts. The page controller supplies its callbacks once it has loaded.
  let _rerender = null;
  let _refreshAndRender = null;
  const page = {
    configure({ rerender, refreshAndRender }) {
      if (typeof rerender !== 'function' || typeof refreshAndRender !== 'function') {
        throw new Error('[humr-integrations] page.configure() needs render lifecycle functions.');
      }
      _rerender = rerender;
      _refreshAndRender = refreshAndRender;
    },
    rerender() {
      if (!_rerender) throw new Error('[humr-integrations] page is not configured.');
      return _rerender();
    },
    refreshAndRender() {
      if (!_refreshAndRender) throw new Error('[humr-integrations] page is not configured.');
      return _refreshAndRender();
    },
  };

  // ── cardActions ───────────────────────────────────────────────────────

  // Provider behavior stays on cardSpec; cardActions owns pending state and the
  // shared refresh afterward. Connect and Configure use the same action.
  // `changed` refreshes, `cancelled` does not, and `navigating` keeps the pending
  // state because the page is about to unload.
  const _connecting = new Set();
  const _disconnecting = new Set();

  async function refreshAfterChange(cardSpec) {
    await page.refreshAndRender();
    if (cardSpec.affectsModelPicker) await refreshModelDropdowns();
  }

  const cardActions = {
    createConnectOutcome() {
      let resolveOutcome;
      const promise = new Promise((resolve) => { resolveOutcome = resolve; });
      let settled = false;
      return {
        promise,
        finish(outcome) {
          if (settled) return;
          settled = true;
          resolveOutcome({ outcome });
        },
      };
    },
    isConnecting(cardSpec) {
      return _connecting.has(cardSpec.key);
    },
    async connect(cardSpec) {
      if (cardActions.isConnecting(cardSpec)) return;
      _connecting.add(cardSpec.key);
      page.rerender();
      let result = null;
      try {
        result = await cardSpec.connect();
        if (result && result.outcome === 'changed') await refreshAfterChange(cardSpec);
      } catch (err) {
        alert(err.message || 'Connect failed. Please try again.');
      } finally {
        if (!result || result.outcome !== 'navigating') {
          _connecting.delete(cardSpec.key);
          page.rerender();
        }
      }
      return result;
    },
    isDisconnecting(cardSpec) {
      return _disconnecting.has(cardSpec.key);
    },
    async disconnect(cardSpec) {
      if (cardActions.isDisconnecting(cardSpec)) return;
      _disconnecting.add(cardSpec.key);
      page.rerender();
      try {
        await cardSpec.disconnect();
        await refreshAfterChange(cardSpec);
      } catch (err) {
        alert(err.message || 'Disconnect failed. Please try again.');
      } finally {
        _disconnecting.delete(cardSpec.key);
        page.rerender();
      }
    },
  };

  // ── cardSpecs ─────────────────────────────────────────────────────────

  // Kind factories build complete cardSpecs. Optional kind+slug factories add
  // provider overrides for cases such as Google and Slack.
  const _cardSpecFactories = new Map();

  function cardSpecSelectorKey(selector) {
    return selector.kind + '\u0000' + (selector.slug || '');
  }

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

    resolve(integration) {
      if (!integration || !integration.kind) return null;
      
      const baseFactory = _cardSpecFactories.get(cardSpecSelectorKey({ kind: integration.kind }));
      if (!baseFactory) return null;

      const cardSpec = baseFactory(integration);
      if (!cardSpec) return null;

      const overrideFactory = _cardSpecFactories.get(cardSpecSelectorKey({ kind: integration.kind, slug: integration.slug }));
      const overrides = overrideFactory ? overrideFactory(integration) : null;
      
      const metadata = integration.metadata || {};
      let provisionLabel = null;
      // Platform is the lowest-priority shared credential source, so if it
      // stamped the outcome no organization or personal credential applied.
      if (metadata.platform_shared) provisionLabel = 'Provided by Humanity Rules';
      else if (metadata.org_shared) provisionLabel = 'Provided by your organization';

      return {
        key: integration.kind + ':' + integration.slug,
        label: integration.label || integration.slug,
        logoUrl: integration.logo_url || null,
        status: integration.status,
        isConnected: integration.status === 'connected',
        affectsModelPicker: !!integration.affects_model_picker,
        canConfigure: false,
        provisionLabel,
        ...cardSpec,
        ...(overrides || {}),
      };
    },
  };

  // ── util ──────────────────────────────────────────────────────────────

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

  async function throwForErrorResponse(response, fallbackMessage) {
    if (response.ok) return;
    let message = fallbackMessage;
    try { message = (await response.json()).error || message; } catch (_) { /* ignore */ }
    throw new Error(message);
  }

  // ── broker ────────────────────────────────────────────────────────────

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

  function tlsInterceptPath(slug, action) {
    return '/__humr_broker/integrations/tls_intercept/' + encodeURIComponent(slug) + '/' + action;
  }

  function mcpPath(slug, action) {
    return '/__humr_broker/integrations/mcp/' + encodeURIComponent(slug) + '/' + action;
  }

  // TLS-intercept providers expose /integrations/user/<slug>/start/ for Connect
  // (top-level navigation to HUMR). Disconnect goes through the broker.
  function buildTlsConnectUrl(slug) {
    const catalog = state.current;
    if (!catalog || !catalog.humr_control_plane_url) {
      throw new Error('[humr-integrations] integration catalog is unavailable.');
    }
    const returnTo = window.location.origin + window.location.pathname;
    const rd = encodeURIComponent(returnTo);
    return catalog.humr_control_plane_url.replace(/\/$/, '') + '/integrations/user/' + slug + '/start/?rd=' + rd + '&app_slug=' + encodeURIComponent(catalog.app_slug || '');
  }

  function buildMcpConnectUrl(slug) {
    const returnTo = encodeURIComponent(window.location.origin + window.location.pathname);
    const origin = encodeURIComponent(window.location.origin);
    return mcpPath(slug, 'oauth/start') + '?return_to=' + returnTo + '&origin=' + origin;
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

  // ── webui ─────────────────────────────────────────────────────────────

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

  async function waitForModels(timeoutMs) {
    const start = Date.now();
    while (true) {
      try {
        const response = await fetch('/api/models', {
          cache: 'no-store',
          credentials: 'include',
        });
        const models = response.ok ? await response.json() : null;
        if (models && Array.isArray(models.groups)) return true;
      } catch (_) { /* retry while processes restart */ }
      if (Date.now() - start >= timeoutMs) return false;
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
  }

  async function refreshModelDropdowns() {
    try {
      await waitForWebui(15000);
      if (typeof window._refreshModelDropdownsAfterProviderChange !== 'function') return;
      // Static assets can be available before the restarted model backend is.
      if (!await waitForModels(15000)) return;
      // Hermes refreshes the composer, Settings, slash-command cache, and model
      // badges. Its hook starts asynchronously, so wait for the promise it owns.
      window._refreshModelDropdownsAfterProviderChange();
      const ready = window._modelDropdownReady;
      if (ready && typeof ready.then === 'function') await ready;
    } catch (_) { /* best-effort */ }
  }

  // ── modals ────────────────────────────────────────────────────────────

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

  // ── oauthSentinel ─────────────────────────────────────────────────────

  // Error oauth-sentinel codes the CP appends to `rd` when an OAuth flow dies
  // after the consent redirect (cancelled at Google, exchange failure, missing
  // refresh token). Only codes registered by providers are consumed — an
  // unknown ?error= belongs to someone else and is left alone.

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

  window.HumrIntegrations = {
    loadedExtensionScripts: new Set(),
    state,
    page,
    cardActions,
    cardSpecs,
    util: {
      elem,
      formatDate,
      statusLabelFor,
      byCategoryThenLabel,
      throwForErrorResponse,
    },
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
      refreshModelDropdowns,
    },
    modals: { showTransitionModal, showOauthErrorModal },
    oauthSentinel: { consumeOAuthSentinel, registerOauthErrors, oauthErrorMessage },
  };
  window.HumrIntegrations.loadedExtensionScripts.add('runtime');
})();
