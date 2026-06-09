// DOH WebUI extension. Loaded per Hermes' docs/EXTENSIONS.md via the
// HERMES_WEBUI_EXTENSION_* env vars exported in webui.sh.
//
// Adds an "Integrations" tab to the WebUI's main left sidebar nav, rendering
// per-provider cards from the integrations broker's unified control API. The
// broker is reached same-origin via the Caddy /__doh_broker/* route.
// Connect / Disconnect for TLS-
// intercept providers (Google, GitHub) are top-level navigations to DOH's control
// plane for Connect; Disconnect goes through the broker so the Integrations pane
// stays open. MCP-aggregator providers (Notion) flow entirely through the broker.
(() => {
  'use strict';

  const INTEGRATIONS_URL = '/__doh_broker/integrations';
  const VAULT_NETWORK_ERROR = (
    'Could not reach the DevOps Hero vault. Try again. ' +
    'If this keeps happening, ask an admin to check this Hermes deployment.'
  );
  let _current = null;
  const _disconnecting = new Set();

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

  let _refreshInflight = false;

  async function startRefreshCatalog() {
    if (_refreshInflight) return;
    const btn = document.getElementById('dohIntegrationRefreshBtn');
    const note = document.getElementById('dohIntegrationRefreshNote');
    if (note) { note.style.display = 'none'; note.textContent = ''; }
    _refreshInflight = true;
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Refreshing…';
    }
    try {
      // One round-trip: the broker reloads the MCP catalog AND invalidates the
      // all-providers TLS cache (which refetches from DOH and, for vault
      // providers, restarts the gateway). Cooldown 429 short-circuits before
      // the TLS side runs, so repeated clicks while the cooldown is active
      // can't keep kicking the gateway.
      const response = await fetch('/__doh_broker/integrations/refresh', {
        method: 'POST',
        cache: 'no-store',
      });
      if (response.status === 429) {
        let retry = 30;
        try { retry = (await response.json()).retry_after_seconds || 30; } catch (_) { /* ignore */ }
        if (note) {
          note.textContent = 'Refresh on cooldown. Try again in ' + retry + 's.';
          note.style.display = '';
        }
        return;
      }
      if (!response.ok) {
        let message = 'Refresh failed. Please try again.';
        try { message = (await response.json()).error || message; } catch (_) { /* ignore */ }
        if (note) {
          note.textContent = message;
          note.style.display = '';
        }
        return;
      }
      await refreshAndRender();
    } catch (_) {
      if (note) {
        note.textContent = 'Could not reach the integrations broker.';
        note.style.display = '';
      }
    } finally {
      _refreshInflight = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Refresh';
      }
    }
  }

  // TLS-intercept providers expose /integrations/user/<slug>/start/ for Connect
  // (top-level navigation to DOH). Disconnect goes through the broker.
  function buildTlsConnectUrl(payload, slug, returnTo) {
    const rd = encodeURIComponent(returnTo);
    return payload.doh_control_plane_url.replace(/\/$/, '') + '/integrations/user/' + slug + '/start/?rd=' + rd + '&app_slug=' + encodeURIComponent(payload.app_slug || '');
  }

  function buildMcpConnectUrl(slug) {
    const returnTo = encodeURIComponent(window.location.origin + window.location.pathname);
    const origin = encodeURIComponent(window.location.origin);
    return '/__doh_broker/integrations/' + slug + '/oauth/start?return_to=' + returnTo + '&origin=' + origin;
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

  // Provider logos are served by the WebUI's static handler (/extensions/*.svg).
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
      class: 'doh-integration-logo',
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
    const list = document.getElementById('dohIntegrationList');
    const imgs = list ? Array.from(list.querySelectorAll('.doh-integration-logo')) : [];
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
    const probeUrl = '/extensions/doh-integrations.css';
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

  // Put a Connect button into its in-flight "Connecting…" state and return a
  // revert function. For navigation flows (TLS-OAuth, MCP) the page leaves
  // before revert is ever called, so the label simply persists. For modal
  // flows (vault, Merge) the caller reverts when the user cancels or it errors.
  function markConnecting(btn) {
    if (!btn) return () => {};
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Connecting…';
    return () => {
      btn.disabled = false;
      btn.textContent = original;
    };
  }

  function connectedFirst(a, b) {
    if (a.status === 'connected' && b.status !== 'connected') return -1;
    if (a.status !== 'connected' && b.status === 'connected') return 1;
    const aLabel = (a.label || a.slug || '').toLowerCase();
    const bLabel = (b.label || b.slug || '').toLowerCase();
    return aLabel.localeCompare(bLabel);
  }

  // ── Shared card + disconnect helpers ──────────────────────────────

  // A Disconnect button with the shared in-flight treatment: disabled and
  // labelled "Disconnecting…" while `key` is in the pending set. `key` is the
  // value tracked in _disconnecting — the provider slug for TLS-intercept
  // cards, or the "merge:"/"mcp:"-prefixed provider id for connector cards.
  function disconnectButton(key, onclick) {
    const pending = _disconnecting.has(key);
    const props = { class: 'doh-integration-btn doh-integration-btn-secondary', onclick };
    if (pending) props.disabled = true;
    return elem('button', props, [pending ? 'Disconnecting…' : 'Disconnect']);
  }

  // Run a disconnect with the shared guard/spinner/cleanup dance: no-op if one
  // is already in flight for `key`; otherwise mark pending and re-render (so the
  // button shows "Disconnecting…"), run `perform`, then always clear and
  // re-render. `perform` owns the fetch and any post-disconnect refresh.
  async function runDisconnect(key, perform) {
    if (_disconnecting.has(key)) return;
    _disconnecting.add(key);
    renderPane(_current);
    try {
      await perform();
    } finally {
      _disconnecting.delete(key);
      renderPane(_current);
    }
  }

  // Build the shared card shell: the card div (row layout unless connected), a
  // title row with optional logo + title, and the status pill. `providerKey`
  // becomes the card's data-provider attribute (slug for TLS, prefixed id for
  // connectors). Each renderer fills in its own connected/not-connected body.
  function buildCardScaffold(item, providerKey) {
    const isConnected = item.status === 'connected';
    const cardClass = isConnected ? 'doh-integration-card' : 'doh-integration-card doh-integration-card-row';
    const card = elem('div', { class: cardClass, dataset: { provider: providerKey } });
    const titleRow = elem('div', { class: 'doh-integration-card-title-row' });
    if (item.logo_url) titleRow.appendChild(logoImg(item.logo_url));
    titleRow.appendChild(elem('div', { class: 'doh-integration-card-title' }, [item.label || item.slug]));
    const statusPill = elem('div', { class: 'doh-integration-card-status', dataset: { status: item.status } }, [statusLabelFor(item.status)]);
    return { card, titleRow, statusPill, isConnected };
  }

  // Append the not-connected footer: a Connect button alongside the status pill
  // in the card's head row. `onConnect` receives the markConnecting revert fn,
  // which modal flows (vault, Merge) call to restore the button on cancel/error
  // and navigation flows simply let persist as the page leaves.
  function appendConnectFooter(card, titleRow, statusPill, onConnect) {
    const connectBtn = elem('button', {
      class: 'doh-integration-btn',
      onclick: () => { onConnect(markConnecting(connectBtn)); },
    }, ['Connect']);
    const trailing = elem('div', { class: 'doh-integration-card-trailing' }, [connectBtn, statusPill]);
    card.appendChild(elem('div', { class: 'doh-integration-card-head' }, [titleRow, trailing]));
  }

  // ── Merge connector flow ──────────────────────────────────────────

  function showMergeExplainerModal(item, onContinue, onCancel) {
    const backdrop = elem('div', { class: 'doh-modal-backdrop' });
    const cancel = () => { backdrop.remove(); if (onCancel) onCancel(); };
    const modal = elem('div', { class: 'doh-modal' }, [
      elem('div', { class: 'doh-modal-title' }, ['Connect ' + item.label]),
      elem('div', { class: 'doh-modal-body' }, [
        'You’ll authenticate in a new tab via Merge.dev, our secure integrations broker. ' +
        'Authentication happens directly with ' + item.label + '.'
      ]),
      elem('div', { class: 'doh-modal-actions' }, [
        elem('button', {
          class: 'doh-integration-btn',
          onclick: cancel,
        }, ['Cancel']),
        elem('button', {
          class: 'doh-integration-btn doh-integration-btn-primary',
          onclick: () => { backdrop.remove(); onContinue(); },
        }, ['Continue']),
      ]),
    ]);
    backdrop.appendChild(modal);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) cancel(); });
    // document.body.appendChild(backdrop);
  }

  function showMergeWaitingModal(item, onCancel) {
    const backdrop = elem('div', { class: 'doh-modal-backdrop' });
    const modal = elem('div', { class: 'doh-modal' }, [
      elem('div', { class: 'doh-modal-title' }, ['Waiting for ' + item.label + '…']),
      elem('div', { class: 'doh-modal-body' }, [
        'Complete authentication in the tab that just opened. When Merge confirms, ' +
        'this dialog closes automatically.',
      ]),
      elem('div', { class: 'doh-modal-actions' }, [
        elem('button', {
          class: 'doh-integration-btn',
          onclick: () => { backdrop.remove(); onCancel(); },
        }, ['Cancel']),
      ]),
    ]);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    return backdrop;
  }

  // Floating "in progress" dialog shown after an OAuth-return sentinel, while
  // the broker primes its cache (and any managed-env WebUI restart settles).
  // status_items() reads cache-only, so the first render after a connect would
  // otherwise paint a stale "not connected" card until the invalidate
  // round-trip lands. The dialog signals work-in-progress over the still-
  // visible page and blocks interaction; init() removes it once the real
  // render completes and logos have reloaded. No Cancel: the round-trip is
  // short and there's nothing to abort.
  function showTransitionModal(sentinel) {
    // The catalog (with each provider's properly-cased label) isn't loaded yet
    // at this point, so the title stays provider-agnostic to avoid mis-casing
    // a brand name (e.g. "Github"). Only one transition is ever in flight.
    const title = (sentinel.transition === 'disconnected' ? 'Disconnecting' : 'Finishing connection') + '…';
    // Transparent backdrop (doh-transition-backdrop): the dialog floats over
    // the page, which stays visible behind it, and blocks interaction. We do
    // NOT re-render the page while the dialog is up, so nothing behind it
    // changes (no stale cards, no blank logos) until the WebUI is back.
    const backdrop = elem('div', { class: 'doh-modal-backdrop doh-transition-backdrop' });
    const modal = elem('div', { class: 'doh-modal doh-transition-modal' }, [
      elem('div', { class: 'doh-transition-spinner' }),
      elem('div', { class: 'doh-modal-title' }, [title]),
      elem('div', { class: 'doh-modal-body' }, ['Syncing status with DevOps Hero. This will only take a moment.']),
    ]);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    return backdrop;
  }

  async function startMergeConnect(item, revert) {
    const revertOnce = () => { if (revert) { revert(); revert = null; } };
    // showMergeExplainerModal(item, async () => {
    let resp;
    try {
      resp = await fetch('/__doh_broker/integrations/merge/link-token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connector_slug: item.slug }),
      });
    } catch (_) {
      alert('Could not reach the integrations broker. Try again.');
      revertOnce();
      return;
    }
    if (!resp.ok) {
      alert('Merge link-token request failed.');
      revertOnce();
      return;
    }
    const data = await resp.json();
    if (!data.magic_link_url) {
      alert('Merge did not return a magic link.');
      revertOnce();
      return;
    }
    window.open(data.magic_link_url, '_blank');

    let stopped = false;
    const waiting = showMergeWaitingModal(item, () => { stopped = true; revertOnce(); });
    const start = Date.now();
    const intervalMs = 3000;
    const timeoutMs = 5 * 60 * 1000;
    const tick = async () => {
      if (stopped) return;
      if (Date.now() - start > timeoutMs) {
        stopped = true;
        waiting.remove();
        revertOnce();
        return;
      }
      try {
        const r = await fetch(
          '/__doh_broker/integrations/merge/connector-status?connector_slug=' + encodeURIComponent(item.slug),
          { cache: 'no-store' },
        );
        if (r.ok) {
          const s = await r.json();
          if (s.status === 'connected') {
            stopped = true;
            waiting.remove();
            await refreshAndRender();
            return;
          }
        }
      } catch (_) { /* keep polling */ }
      setTimeout(tick, intervalMs);
    };
    setTimeout(tick, intervalMs);
    // }, revertOnce);
  }

  function renderConnectorCard(item) {
    const isMergeConnector = item.kind === 'merge_connector';
    const provider = (isMergeConnector ? 'merge:' : 'mcp:') + item.slug;
    const { card, titleRow, statusPill, isConnected } = buildCardScaffold(item, provider);

    if (isConnected) {
      const disconnectBtn = disconnectButton(provider, () => runDisconnect(provider, async () => {
        if (isMergeConnector) {
          await fetch('/__doh_broker/integrations/merge/disconnect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ connector_slug: item.slug }),
          });
        } else {
          await fetch('/__doh_broker/integrations/' + item.slug + '/disconnect', { method: 'POST' });
        }
        await refreshAndRender();
      }));
      card.appendChild(elem('div', { class: 'doh-integration-card-head' }, [titleRow, statusPill]));
      card.appendChild(elem('div', { class: 'doh-integration-card-body' }, [disconnectBtn]));
      return card;
    }

    appendConnectFooter(card, titleRow, statusPill, (revert) => {
      if (isMergeConnector) startMergeConnect(item, revert);
      else window.location.href = buildMcpConnectUrl(item.slug);
    });
    return card;
  }

  function fieldInputFor(field) {
    const id = 'dohVaultField_' + field.name;
    const wrapper = elem('label', { class: 'doh-vault-field', for: id });
    wrapper.appendChild(elem('span', { class: 'doh-vault-field-label' }, [
      field.label + (field.required ? ' *' : ''),
    ]));
    let input;
    if (field.kind === 'textarea') {
      input = elem('textarea', {
        id,
        class: 'doh-vault-input doh-vault-textarea',
        name: field.name,
        rows: '4',
      });
      input.value = field.value || '';
    } else {
      input = elem('input', {
        id,
        class: 'doh-vault-input',
        name: field.name,
        type: field.kind === 'secret' ? 'password' : 'text',
        placeholder: field.placeholder || '',
        autocomplete: 'off',
      });
    }
    input.dataset.kind = field.kind || 'text';
    wrapper.appendChild(input);
    if (field.help) wrapper.appendChild(elem('span', { class: 'doh-vault-field-help' }, [field.help]));
    return wrapper;
  }

  async function requestVaultSetupSession(item) {
    const url = '/__doh_broker/integrations/' + encodeURIComponent(item.slug) +
      '/vault/setup-session?origin=' + encodeURIComponent(window.location.origin);
    let response;
    try {
      response = await fetch(url, { method: 'POST', cache: 'no-store' });
    } catch (_) {
      throw new Error(VAULT_NETWORK_ERROR);
    }
    if (!response.ok) {
      let message = 'Could not start the vault setup flow.';
      try { message = (await response.json()).error || message; } catch (_) { /* ignore */ }
      throw new Error(message);
    }
    return await response.json();
  }

  async function submitVaultForm(session, form) {
    const credentials = {};
    const config = {};
    for (const input of form.querySelectorAll('[name]')) {
      const value = input.value || '';
      if (input.dataset.kind === 'secret') {
        if (value.trim()) credentials[input.name] = value;
      } else {
        config[input.name] = value;
      }
    }
    let response;
    try {
      response = await fetch(session.action_url, {
        method: 'POST',
        credentials: 'omit',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify({
          submit_token: session.submit_token,
          credentials,
          config,
        }),
      });
    } catch (_) {
      throw new Error(VAULT_NETWORK_ERROR);
    }
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* ignore */ }
    if (!response.ok) throw new Error(payload.error || 'Vault submission failed.');
    return payload;
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

  async function refreshModelDropdownsIfProviderAffectsPicker(item) {
    if (!item || !item.affects_model_picker) return;
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

  // Wire a vault form's submit: POST to DOH, invalidate local provider state,
  // swap the action row to a Close button, and refresh the pane. Shared by the
  // generic and Slack renderers so the save/restart/refresh flow is single-sourced.
  function wireVaultSubmit(opts) {
    const { form, session, item, saveBtn, actions, errorBox, successBox, close } = opts;
    const showCloseAction = () => {
      actions.replaceChildren(elem('button', {
        class: 'doh-integration-btn doh-integration-btn-primary',
        type: 'button',
        onclick: close,
      }, ['Close']));
    };
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      let saved = false;
      errorBox.style.display = 'none';
      successBox.style.display = 'none';
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving…';
      try {
        await submitVaultForm(session, form);
        saved = true;
        successBox.textContent = 'Saved. Applying credentials…';
        successBox.style.display = '';
        let restarted = true;
        try {
          await invalidateBrokerTlsCache(item.slug);
        } catch (err) {
          restarted = false;
          successBox.textContent =
            err.message ||
            'Saved, but applying the credentials failed. Redeploy this Hermes app to apply them.';
        }
        if (item.affects_model_picker) {
          successBox.textContent = 'Saved. Updating the model list…';
          await refreshModelDropdownsIfProviderAffectsPicker(item);
        }
        showCloseAction();
        try {
          await refreshAndRender();
        } catch (_) { /* sidebar refresh can recover on next open */ }
        if (restarted) {
          successBox.textContent = 'Saved. The new credentials are active.';
        }
      } catch (err) {
        errorBox.textContent = err.message || 'Save failed.';
        errorBox.style.display = '';
      } finally {
        if (!saved) {
          saveBtn.disabled = false;
          saveBtn.textContent = 'Save';
        }
      }
    });
  }

  function showGenericVaultConfigModal(item, session, onClose) {
    if (session.schema && session.schema.mode === 'link_poll') {
      showLinkPollConnectModal(item, session, onClose);
      return;
    }
    const schema = session.schema;
    const backdrop = elem('div', { class: 'doh-modal-backdrop doh-vault-backdrop' });
    const close = () => { backdrop.remove(); if (onClose) onClose(); };
    const errorBox = elem('div', { class: 'doh-vault-error', style: { display: 'none' } });
    const successBox = elem('div', { class: 'doh-vault-success', style: { display: 'none' } });
    const form = elem('form', { class: 'doh-vault-form' });
    for (const field of schema.fields || []) {
      form.appendChild(fieldInputFor(field));
    }
    const saveBtn = elem('button', {
      class: 'doh-integration-btn doh-integration-btn-primary',
      type: 'submit',
    }, ['Save']);
    const actions = elem('div', { class: 'doh-modal-actions' }, [
      elem('button', {
        class: 'doh-integration-btn',
        type: 'button',
        onclick: close,
      }, ['Cancel']),
      saveBtn,
    ]);
    form.appendChild(actions);
    wireVaultSubmit({ form, session, item, saveBtn, actions, errorBox, successBox, close });
    const modal = elem('div', { class: 'doh-modal doh-vault-modal' }, [
      elem('div', { class: 'doh-modal-title' }, [(schema.status === 'connected' ? 'Configure ' : 'Connect ') + (schema.label || item.label)]),
      elem('div', { class: 'doh-modal-body' }, [schema.message || 'Credentials are sent directly to the DevOps Hero vault.']),
      errorBox,
      successBox,
      form,
    ]);
    backdrop.appendChild(modal);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
    document.body.appendChild(backdrop);
  }

  // ── Link+poll vault flow ──────────────────────────────────────────
  // Vault providers whose credential is created in an external app (e.g.
  // Telegram managed bots) return `schema.mode === 'link_poll'`: the modal
  // shows a QR code the user scans with their phone, and we poll DOH's
  // setup-poll endpoint with the session token until the provider reports
  // the credential as connected. The DOH side then already holds the
  // secret — nothing is typed or pasted here, and no desktop app is needed.

  const LINK_POLL_MS = 3000;

  function showLinkPollConnectModal(item, session, onClose) {
    const schema = session.schema;
    const backdrop = elem('div', { class: 'doh-modal-backdrop doh-vault-backdrop' });
    let stopped = false;
    const close = () => { stopped = true; backdrop.remove(); if (onClose) onClose(); };

    const errorBox = elem('div', { class: 'doh-vault-error', style: { display: 'none' } });
    const statusBox = elem('div', { class: 'doh-vault-success' }, [schema.pending_message || 'Waiting for confirmation…']);
    const cancelBtn = elem('button', { class: 'doh-integration-btn', type: 'button', onclick: close }, ['Cancel']);
    const actions = elem('div', { class: 'doh-modal-actions' }, [cancelBtn]);

    const children = [
      elem('div', { class: 'doh-modal-title' }, ['Connect ' + (schema.label || item.label)]),
      elem('div', { class: 'doh-modal-body' }, [schema.message || '']),
    ];
    if (schema.qr_data_uri) {
      children.push(elem('div', { class: 'doh-vault-qr-wrap' }, [
        elem('img', { class: 'doh-vault-qr', src: schema.qr_data_uri, alt: 'QR code', decoding: 'async' }),
        elem('div', { class: 'doh-vault-qr-caption' }, [schema.qr_caption || 'Scan with your phone']),
      ]));
      if (schema.link_note) {
        children.push(elem('div', { class: 'doh-vault-link-note' }, [schema.link_note]));
      }
      children.push(statusBox);
    } else {
      // The control plane reported the flow as unavailable (schema.message
      // says why); there is nothing to open or poll.
      stopped = true;
      cancelBtn.textContent = 'Close';
    }
    children.push(errorBox, actions);
    backdrop.appendChild(elem('div', { class: 'doh-modal doh-vault-modal' }, children));
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
    document.body.appendChild(backdrop);

    const fail = (message) => {
      statusBox.style.display = 'none';
      errorBox.textContent = message || 'Connect failed. Please try again.';
      errorBox.style.display = '';
      cancelBtn.textContent = 'Close';
    };

    // Same post-save dance as wireVaultSubmit: invalidate the broker cache
    // (which rewrites the gateway env and restarts the gateway), then refresh
    // the cards.
    const succeed = async () => {
      statusBox.textContent = 'Connected. Applying credentials…';
      let restarted = true;
      try {
        await invalidateBrokerTlsCache(item.slug);
      } catch (err) {
        restarted = false;
        statusBox.textContent =
          err.message ||
          'Connected, but applying the credentials failed. Redeploy this Hermes app to apply them.';
      }
      actions.replaceChildren(elem('button', {
        class: 'doh-integration-btn doh-integration-btn-primary',
        type: 'button',
        onclick: close,
      }, ['Close']));
      try {
        await refreshAndRender();
      } catch (_) { /* sidebar refresh can recover on next open */ }
      if (restarted) {
        statusBox.textContent = 'Connected. The new credentials are active.';
      }
    };

    const poll = async () => {
      if (stopped) return;
      let response;
      let payload = {};
      try {
        response = await fetch(session.poll_url, {
          method: 'POST',
          credentials: 'omit',
          headers: { 'Content-Type': 'text/plain' },
          body: JSON.stringify({ submit_token: session.submit_token }),
        });
        try { payload = await response.json(); } catch (_) { /* ignore */ }
      } catch (_) {
        setTimeout(poll, LINK_POLL_MS); // network blip: keep polling
        return;
      }
      if (stopped) return;
      if (response.ok && payload.status === 'connected') {
        stopped = true;
        await succeed();
        return;
      }
      if (response.ok) {
        setTimeout(poll, LINK_POLL_MS);
        return;
      }
      stopped = true;
      fail(payload.error); // expired session or provider error: terminal
    };
    if (!stopped) setTimeout(poll, LINK_POLL_MS);
  }

  // ── OAuth device-login flow ───────────────────────────────────────
  // Device providers can't use our redirect callback, so the broker runs the
  // provider's device flow. The browser only starts the session, displays the
  // code + verification URL, and polls for terminal state.

  const DEVICE_POLL_MS = 3000;

  async function startDeviceConnect(item, revert) {
    const revertOnce = () => { if (revert) { revert(); revert = null; } };
    let session;
    try {
      const base = '/__doh_broker/integrations/' + encodeURIComponent(item.slug) + '/device';
      const resp = await fetch(base + '/start', { method: 'POST', cache: 'no-store' });
      session = await resp.json();
      if (!resp.ok || !session.ok) throw new Error(session.error || 'Could not start the login.');
    } catch (err) {
      alert(err.message || 'Could not start the login.');
      revertOnce();
      return;
    }
    showDeviceModal(item, session, revertOnce);
  }

  function showDeviceModal(item, session, onClose) {
    const backdrop = elem('div', { class: 'doh-modal-backdrop' });
    let cancelled = false;
    const base = '/__doh_broker/integrations/' + encodeURIComponent(item.slug) + '/device';
    const close = () => {
      cancelled = true;
      backdrop.remove();
      // Best-effort: tell the broker to drop the in-flight session.
      fetch(base + '/cancel', { method: 'POST' }).catch(() => {});
      if (onClose) onClose();
    };

    const codeEl = elem('div', { class: 'doh-device-code' }, [session.user_code || '—']);
    const link = elem('a', {
      class: 'doh-device-oauth-link',
      href: session.verification_url,
      target: '_blank',
      rel: 'noopener',
    }, [session.verification_url]);
    const statusBox = elem('div', { class: 'doh-vault-success' }, ['Waiting for you to approve in your browser…']);
    const errorBox = elem('div', { class: 'doh-vault-error', style: { display: 'none' } });
    const cancelBtn = elem('button', { class: 'doh-integration-btn', type: 'button', onclick: close }, ['Cancel']);

    const modal = elem('div', { class: 'doh-modal' }, [
      elem('div', { class: 'doh-modal-title' }, ['Connect ' + item.label]),
      elem('div', { class: 'doh-modal-body doh-device-step-label' }, [
        'Open the link below and sign in:',
      ]),
      elem('div', { class: 'doh-modal-body doh-device-step-content' }, [link]),
      elem('div', { class: 'doh-modal-body doh-device-step-label' }, [
        'After opening the link and signing in, enter this code:',
      ]),
      codeEl,
      statusBox,
      errorBox,
      elem('div', { class: 'doh-modal-actions' }, [cancelBtn]),
    ]);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    // Poll the broker for terminal state.
    const poll = async () => {
      if (cancelled) return;
      let st;
      try {
        const resp = await fetch(base + '/status', { cache: 'no-store' });
        st = await resp.json();
      } catch (_) {
        setTimeout(poll, DEVICE_POLL_MS);
        return;
      }
      if (cancelled) return;
      if (st.phase === 'completed') {
        backdrop.remove();
        await refreshAndRender();
        await refreshModelDropdownsIfProviderAffectsPicker(item);
        if (onClose) onClose();
        return;
      }
      if (st.phase === 'failed' || st.phase === null) {
        statusBox.style.display = 'none';
        errorBox.textContent = st.error || 'Login failed. Please try again.';
        errorBox.style.display = '';
        cancelBtn.textContent = 'Close';
        return;
      }
      setTimeout(poll, DEVICE_POLL_MS);
    };
    setTimeout(poll, DEVICE_POLL_MS);
  }

  // Slack manifest name rules (https://docs.slack.dev/reference/app-manifest):
  // display_information.name is <=35 chars (any character); bot_user.display_name
  // is <=80 chars restricted to [a-z0-9._-]. Mirrors provider_slack.py so the
  // live prefill URL matches what the server bakes/persists.
  const SLACK_APP_NAME_MAX_LEN = 35;
  const SLACK_BOT_NAME_MAX_LEN = 80;
  function slackCleanAppName(raw) {
    return (raw || '').trim().slice(0, SLACK_APP_NAME_MAX_LEN).trim() || 'Slackbot';
  }
  function slackCleanBotName(raw) {
    const name = (raw || '').trim().toLowerCase()
      .replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '')
      .slice(0, SLACK_BOT_NAME_MAX_LEN).replace(/^-+|-+$/g, '');
    return name || 'slackbot';
  }

  // Build the Slack "Create app" prefill URL for one mode by embedding that
  // mode's manifest, re-baking the operator's chosen app name into both name
  // fields. The operator clicks it, Slack opens Create-New-App with everything
  // pre-filled (incl. Socket Mode), then generates the app-level token and
  // installs to get the bot token.
  function slackCreateAppUrl(schema, mode, appName) {
    const base = (schema.manifests || {})[mode];
    if (!base) return null;
    const manifest = JSON.parse(JSON.stringify(base));
    if (manifest.display_information) manifest.display_information.name = slackCleanAppName(appName);
    if (manifest.features && manifest.features.bot_user) manifest.features.bot_user.display_name = slackCleanBotName(appName);
    return 'https://api.slack.com/apps?new_app=1&manifest_json=' +
      encodeURIComponent(JSON.stringify(manifest));
  }

  function showSlackConfigModal(item, session, onClose) {
    const schema = session.schema;
    const backdrop = elem('div', { class: 'doh-modal-backdrop doh-vault-backdrop' });
    const close = () => { backdrop.remove(); if (onClose) onClose(); };
    const errorBox = elem('div', { class: 'doh-vault-error', style: { display: 'none' } });
    const successBox = elem('div', { class: 'doh-vault-success', style: { display: 'none' } });
    const form = elem('form', { class: 'doh-vault-form' });

    // Never preselect a disabled mode: a stale credential could carry a mode
    // since turned off (the backend would then reject Save). Clamp to the
    // server's selected_mode only if it's enabled, else the first enabled mode.
    const enabledModes = (schema.modes || []).filter((m) => m.enabled);
    const preferred = schema.selected_mode || 'company_wide';
    let selectedMode =
      enabledModes.some((m) => m.value === preferred) ? preferred
      : (enabledModes[0] ? enabledModes[0].value : preferred);
    // Hidden input carries the chosen mode to the backend as config.workspace_scope
    // (submitVaultForm routes non-secret named inputs into `config`).
    const modeInput = elem('input', { type: 'hidden', name: 'workspace_scope' });
    modeInput.value = selectedMode;

    // Editable app name (named so submitVaultForm routes it into `config`).
    // Defaults to the deploying app's template name; drives both Slack name
    // fields in the prefill manifest, so editing it rebuilds the link.
    const nameInput = elem('input', {
      class: 'doh-vault-input',
      name: 'app_name',
      type: 'text',
      maxlength: String(schema.app_name_max_len || SLACK_APP_NAME_MAX_LEN),
      autocomplete: 'off',
    });
    nameInput.value = schema.app_name || '';

    // Owner email (personal mode only). Named so submitVaultForm routes it into
    // `config`; the backend resolves it to a Slack user_id at save and never
    // persists the address. Prefilled with the deploying user's DOH email.
    const ownerEmailInput = elem('input', {
      class: 'doh-vault-input',
      name: 'owner_email',
      type: 'email',
      placeholder: 'you@company.com',
      autocomplete: 'off',
    });
    ownerEmailInput.value = schema.owner_email || '';
    // On reconfigure an owner is already bound (server blanks owner_email and
    // sends owner_name); a blank email keeps that owner. Say so, or the empty
    // field reads as "no owner set".
    const ownerHelp = schema.owner_name
      ? 'Currently replies to ' + schema.owner_name + '. Leave blank to keep them, or enter a different Slack email to change.'
      : 'The bot will reply only to this person. Use the email tied to your Slack account.';
    const ownerEmailField = elem('label', { class: 'doh-vault-field' }, [
      elem('span', { class: 'doh-vault-field-label' }, ['Your Slack email']),
      ownerEmailInput,
      elem('span', { class: 'doh-vault-field-help' }, [ownerHelp]),
    ]);
    // Only personal mode collects an owner; show/hide as the mode changes.
    const syncOwnerEmail = () => {
      ownerEmailField.style.display = selectedMode === 'personal' ? '' : 'none';
    };

    // Home channel (company-wide only). Optional C… id the bot is invited to;
    // where the gateway delivers cron/proactive output. Personal mode resolves
    // the owner's DM automatically, so this field is hidden there.
    const homeChannelInput = elem('input', {
      class: 'doh-vault-input',
      name: 'home_channel',
      type: 'text',
      placeholder: 'C0123456789',
      autocomplete: 'off',
    });
    homeChannelInput.value = schema.home_channel || '';
    const homeChannelField = elem('label', { class: 'doh-vault-field' }, [
      elem('span', { class: 'doh-vault-field-label' }, ['Home channel (optional)']),
      homeChannelInput,
      elem('span', { class: 'doh-vault-field-help' }, [
        'Channel ID where cron results and proactive messages are posted. Invite the bot to that channel first. Leave blank to set it later with !sethome in the channel.',
      ]),
    ]);
    const syncHomeChannel = () => {
      homeChannelField.style.display = selectedMode === 'company_wide' ? '' : 'none';
    };

    // The prefill link is rebuilt whenever the mode or app name changes — each
    // mode embeds a different manifest (scopes + subscriptions), and the name
    // is re-baked into both manifest name fields.
    const createLink = elem('a', {
      class: 'doh-integration-btn doh-integration-btn-primary doh-slack-create-link',
      target: '_blank',
      rel: 'noopener noreferrer',
    }, ['Create Slack app ↗']);
    const syncCreateLink = () => {
      const url = slackCreateAppUrl(schema, selectedMode, nameInput.value);
      if (url) { createLink.href = url; createLink.style.display = ''; }
      else { createLink.removeAttribute('href'); createLink.style.display = 'none'; }
    };
    nameInput.addEventListener('input', syncCreateLink);

    // Radios deliberately carry NO `name` attribute: submitVaultForm scrapes
    // every `[name]` input into credentials/config, so a named radio would
    // leak a bogus field. The chosen value flows only through `modeInput`.
    // Mutual exclusion is done in JS by unchecking siblings on change.
    const modeChoices = elem('div', { class: 'doh-slack-modes' });
    const radios = [];
    for (const mode of schema.modes || []) {
      const radio = elem('input', { type: 'radio', value: mode.value });
      radio.checked = mode.value === selectedMode;
      if (!mode.enabled) radio.disabled = true;
      radio.addEventListener('change', () => {
        if (!radio.checked) return;
        for (const other of radios) { if (other !== radio) other.checked = false; }
        selectedMode = mode.value;
        modeInput.value = selectedMode;
        syncCreateLink();
        syncOwnerEmail();
        syncHomeChannel();
      });
      radios.push(radio);
      const labelText = mode.label + (mode.enabled ? '' : ' (coming soon)');
      modeChoices.appendChild(elem('label', { class: 'doh-slack-mode' }, [radio, elem('span', null, [labelText])]));
    }

    const steps = elem('ol', { class: 'doh-slack-steps' }, [
      elem('li', null, ['Click ', elem('b', null, ['Create Slack app']), ' and create it in your workspace.']),
      elem('li', null, ['On the app page, generate an ', elem('b', null, ['App-level token']), ' (scope connections:write).']),
      elem('li', null, [elem('b', null, ['Install']), ' the app to your workspace to get the ', elem('b', null, ['Bot token']), '.']),
      elem('li', null, ['Paste both tokens below.']),
    ]);

    form.appendChild(modeInput);
    form.appendChild(elem('label', { class: 'doh-vault-field' }, [
      elem('span', { class: 'doh-vault-field-label' }, ['App name']),
      nameInput,
      elem('span', { class: 'doh-vault-field-help' }, ['Shown in Slack as the app and bot name. Defaults to your agent template.']),
    ]));
    form.appendChild(elem('div', { class: 'doh-vault-field-label' }, ['Agent type']));
    form.appendChild(modeChoices);
    form.appendChild(ownerEmailField);
    form.appendChild(homeChannelField);
    form.appendChild(elem('div', { class: 'doh-slack-create-row' }, [createLink]));
    form.appendChild(steps);
    for (const field of schema.fields || []) {
      form.appendChild(fieldInputFor(field));
    }
    syncCreateLink();
    syncOwnerEmail();
    syncHomeChannel();

    const saveBtn = elem('button', { class: 'doh-integration-btn doh-integration-btn-primary', type: 'submit' }, ['Save']);
    const actions = elem('div', { class: 'doh-modal-actions' }, [
      elem('button', { class: 'doh-integration-btn', type: 'button', onclick: close }, ['Cancel']),
      saveBtn,
    ]);
    form.appendChild(actions);
    wireVaultSubmit({ form, session, item, saveBtn, actions, errorBox, successBox, close });

    const modal = elem('div', { class: 'doh-modal doh-vault-modal' }, [
      elem('div', { class: 'doh-modal-title' }, [(schema.status === 'connected' ? 'Configure ' : 'Connect ') + (schema.label || item.label)]),
      elem('div', { class: 'doh-modal-body' }, [schema.message || 'Tokens are sent directly to the DevOps Hero vault.']),
      errorBox,
      successBox,
      form,
    ]);
    backdrop.appendChild(modal);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
    document.body.appendChild(backdrop);
  }

  // Per-provider config-modal renderers. The generic `showGenericVaultConfigModal`
  // renders any flat `schema.fields` form, and dispatches `mode: 'link_poll'`
  // schemas (Telegram managed bots) to the link+poll modal. Providers
  // whose setup needs more than a flat form (e.g. Slack's mode selector +
  // manifest prefill link + two tokens) register a custom renderer here,
  // keyed by slug; everything else falls back to the generic one. All
  // renderers share the same setup-session/submit/restart plumbing.
  const _VAULT_RENDERERS = {
    slack: showSlackConfigModal,
  };

  async function startVaultConfig(item, revert) {
    // `revert` (from markConnecting) restores the Connect button. Fire it if we
    // never open the modal (error), or when the user dismisses it without
    // connecting; a successful save re-renders the card from scratch so the
    // button is replaced regardless. revert may be omitted (e.g. the Configure
    // button on an already-connected provider reuses this path).
    const revertOnce = () => { if (revert) { revert(); revert = null; } };
    try {
      const session = await requestVaultSetupSession(item);
      const renderer = _VAULT_RENDERERS[item.slug] || showGenericVaultConfigModal;
      renderer(item, session, revertOnce);
    } catch (err) {
      alert(err.message || 'Could not open the vault dialog.');
      revertOnce();
    }
  }

  // One disconnect path for every TLS-intercept provider (vault + OAuth).
  // The broker resolves the provider kind server-side, so both kinds POST
  // here identically.
  function disconnectTlsProvider(item) {
    return runDisconnect(item.slug, async () => {
      const response = await fetch('/__doh_broker/integrations/' + encodeURIComponent(item.slug) + '/tls/disconnect', {
        method: 'POST',
        cache: 'no-store',
      });
      if (!response.ok) {
        let message = 'Disconnect failed. Please try again.';
        try { message = (await response.json()).error || message; } catch (_) { /* ignore */ }
        alert(message);
        return;
      }
      await refreshAndRender();
      await refreshModelDropdownsIfProviderAffectsPicker(item);
    });
  }

  function renderTlsInterceptCard(item, payload) {
    const returnTo = window.location.origin + window.location.pathname;
    const usesVault = item.connect_mode === 'vault';
    const usesDevice = item.connect_mode === 'device';
    const { card, titleRow, statusPill, isConnected } = buildCardScaffold(item, item.slug);

    if (isConnected) {
      card.appendChild(elem('div', { class: 'doh-integration-card-head' }, [titleRow, statusPill]));
      const body = elem('div', { class: 'doh-integration-card-body' });
      const botUsername = item.metadata && item.metadata.bot_username;
      if (botUsername) {
        body.appendChild(elem('div', { class: 'doh-integration-meta' }, ['Connected as @' + botUsername]));
      }
      // Slack personal mode resolves an owner; only that provider sets it.
      const ownerName = item.metadata && item.metadata.owner_name;
      if (ownerName) {
        body.appendChild(elem('div', { class: 'doh-integration-meta' }, ['Replies only to ' + ownerName]));
      }
      if (item.last_refreshed_at) {
        body.appendChild(elem('div', { class: 'doh-integration-meta' }, [
          'Last refreshed: ' + formatDate(item.last_refreshed_at),
        ]));
      }
      // Both connect modes share one Disconnect button; vault providers add a
      // Configure button ahead of it.
      const actions = elem('div', { class: 'doh-integration-actions' });
      if (usesVault) {
        actions.appendChild(elem('button', {
          class: 'doh-integration-btn',
          onclick: () => { startVaultConfig(item); },
        }, ['Configure']));
      }
      actions.appendChild(disconnectButton(item.slug, () => { disconnectTlsProvider(item); }));
      body.appendChild(actions);
      card.appendChild(body);
      return card;
    }

    appendConnectFooter(card, titleRow, statusPill, (revert) => {
      if (usesVault) startVaultConfig(item, revert);
      else if (usesDevice) startDeviceConnect(item, revert);
      else window.location.href = buildTlsConnectUrl(payload, item.slug, returnTo);
    });
    return card;
  }

  function renderSummary(payload) {
    const summary = document.getElementById('dohIntegrationSummary');
    if (!summary) return;
    summary.innerHTML = '';
    if (!payload) {
      summary.appendChild(document.createTextNode('Status unavailable.'));
      return;
    }
    const items = payload.items || [];
    const connected = items.filter((it) => it.status === 'connected').length;
    const total = items.length;
    summary.appendChild(document.createTextNode(
      total === 0
        ? 'No integrations configured.'
        : connected + ' of ' + total + ' connected'
    ));
  }

  function renderPane(payload) {
    renderSummary(payload);

    const list = document.getElementById('dohIntegrationList');
    if (!list) return;
    list.innerHTML = '';

    if (!payload) {
      list.appendChild(elem('div', { class: 'doh-integration-empty' }, [
        'Integration status is unavailable. If this persists, the platform broker may not be running.',
      ]));
      return;
    }

    const items = (payload.items || []).slice().sort(connectedFirst);
    if (items.length === 0) {
      list.appendChild(elem('div', { class: 'doh-integration-empty' }, ['No integrations configured.']));
      return;
    }
    for (const item of items) {
      if (item.kind === 'tls_intercept') {
        list.appendChild(renderTlsInterceptCard(item, payload));
      } else if (item.kind === 'mcp_aggregator' || item.kind === 'merge_connector') {
        list.appendChild(renderConnectorCard(item));
      }
    }
  }

  async function refreshAndRender() {
    _current = await fetchIntegrations();
    renderPane(_current);
  }

  // Tell the broker to drop one provider's cached TLS-intercept token after a
  // known connect/disconnect/config change (vault save, OAuth-return sentinel).
  // The explicit-Refresh-all path goes through /__doh_broker/integrations/refresh
  // instead, which fans out catalog reload + all-providers TLS invalidate.
  //
  // For env-backed providers the broker also rewrites the managed profile env
  // block and restarts whichever process-compose entries the provider declares.
  // Both failure modes (network error reaching the broker, or 5xx from the
  // broker) propagate to the caller. Cache-hint callers (OAuth-return sentinel)
  // catch and ignore: the proxy's 401-evict path recovers stale tokens on the
  // first real call.
  async function invalidateBrokerTlsCache(providerSlug) {
    const url = '/__doh_broker/integrations/' + encodeURIComponent(providerSlug) + '/invalidate_tls_cache';
    const response = await fetch(url, { method: 'POST' });
    if (response.ok) return;
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* ignore */ }
    throw new Error(
      payload.error ||
        'Saved, but applying the credentials failed. Redeploy this Hermes app to apply them.',
    );
  }

  // Drop any ?connected=/?disconnected= sentinel once we've acted on it,
  // so a reload doesn't replay the refresh.
  function consumeReturnSentinel() {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get('connected');
    const disconnected = params.get('disconnected');
    if (!connected && !disconnected) return null;
    params.delete('connected');
    params.delete('disconnected');
    const qs = params.toString();
    const newUrl = window.location.pathname + (qs ? '?' + qs : '') + window.location.hash;
    window.history.replaceState({}, '', newUrl);
    return {
      transition: connected ? 'connected' : 'disconnected',
      provider: connected || disconnected,
    };
  }

  function ensureSidebarTabAndPane() {
    const sidebar = document.querySelector('.sidebar');
    const sidebarNav = sidebar && sidebar.querySelector('.sidebar-nav');
    const mainEl = document.querySelector('main.main');
    // Hermes 0.51+ added a desktop primary `<nav class="rail">` alongside the
    // legacy `.sidebar-nav`. CSS hides `.sidebar-nav` at ≥641px and shows
    // `.rail` instead, so we have to register a button in BOTH so the entry
    // is visible on every viewport. Older versions without `.rail` just have
    // the sidebar-nav button — that's fine.
    const rail = document.querySelector('nav.rail');
    if (!sidebar || !sidebarNav || !mainEl) return false;
    if (document.getElementById('mainIntegrations')) return true;

    const onActivate = () => {
      // Pass `fromRailClick: true` so we get the same rail behaviour as
      // other top-level entries — second click on the active rail icon
      // collapses the sidebar, click while collapsed re-expands it.
      if (typeof window.switchPanel === 'function') {
        window.switchPanel('integrations', { fromRailClick: true });
      }
    };

    // Plug icon — 20×20 in the rail (matches upstream rail icons),
    // 18×18 in the sidebar-nav (matches upstream nav-tab icons).
    const railIcon = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4"/></svg>';
    const navIcon = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4"/></svg>';

    if (rail && !document.getElementById('dohIntegrationsRailBtn')) {
      const railBtn = elem('button', {
        type: 'button',
        class: 'rail-btn nav-tab has-tooltip',
        id: 'dohIntegrationsRailBtn',
        'aria-label': 'Integrations',
        dataset: { panel: 'integrations', tooltip: 'Integrations' },
        onclick: onActivate,
      });
      railBtn.innerHTML = railIcon;
      // Insert before `.rail-spacer` so the button sits with primary panels,
      // not below the spacer where Settings lives.
      const spacer = rail.querySelector('.rail-spacer');
      if (spacer) rail.insertBefore(railBtn, spacer);
      else rail.appendChild(railBtn);
    }

    if (!document.getElementById('dohIntegrationsTab')) {
      const navBtn = elem('button', {
        type: 'button',
        class: 'nav-tab has-tooltip has-tooltip--bottom',
        id: 'dohIntegrationsTab',
        dataset: { panel: 'integrations', label: 'Integrations', tooltip: 'Integrations' },
        onclick: onActivate,
      });
      navBtn.innerHTML = navIcon;
      sidebarNav.appendChild(navBtn);
    }

    // Integrations is a top-level destination, not a sidebar drawer. Mount
    // it as a `#main<Name>.main-view` sibling inside <main>, matching the
    // upstream view-switching contract (see hermes-webui static/style.css —
    // "Generalized main-view switching"). The wrapper around switchPanel adds
    // `showing-integrations` on <main>; our CSS reveals this view and hides
    // `#mainChat` when the class is present.
    const refreshButton = elem('button', {
      class: 'doh-integration-btn doh-integration-page-refresh-btn',
      id: 'dohIntegrationRefreshBtn',
      type: 'button',
      onclick: startRefreshCatalog,
    }, ['Refresh']);
    const refreshNote = elem('div', {
      class: 'doh-integration-page-refresh-note',
      id: 'dohIntegrationRefreshNote',
      style: { display: 'none' },
    });
    const view = elem('section', { class: 'main-view doh-integration-page', id: 'mainIntegrations' }, [
      elem('div', { class: 'doh-integration-page-inner' }, [
        elem('div', { class: 'doh-integration-page-head' }, [
          elem('div', { class: 'doh-integration-page-head-row' }, [
            elem('div', { class: 'doh-integration-page-title' }, ['Integrations']),
            refreshButton,
          ]),
          elem('div', { class: 'doh-integration-page-meta' }, [
            'Third-party accounts the agent can act on.',
          ]),
          refreshNote,
        ]),
        elem('div', { class: 'doh-integration-list', id: 'dohIntegrationList' }),
      ]),
    ]);
    mainEl.appendChild(view);

    // Sidebar panel-view: upstream's switchPanel activates `#panel<Name>`
    // and deactivates the rest, so without our own panel the sidebar would
    // appear empty when Integrations is active. We give it a title and a
    // connected-count summary updated each time we refresh from the broker.
    const sidebarPane = elem('div', { class: 'panel-view', id: 'panelIntegrations' }, [
      elem('div', { class: 'panel-head' }, [
        elem('span', null, ['Integrations']),
      ]),
      elem('div', { class: 'doh-integration-summary', id: 'dohIntegrationSummary' }, [
        'Loading…',
      ]),
    ]);
    const sidebarBottom = sidebar.querySelector('.sidebar-bottom');
    if (sidebarBottom) sidebar.insertBefore(sidebarPane, sidebarBottom);
    else sidebar.appendChild(sidebarPane);
    return true;
  }

  // Wrap upstream switchPanel so:
  //   1. Opening our tab refreshes the broker view.
  //   2. <main> gets `showing-integrations` while we're active and loses it
  //      when leaving — upstream's loop only toggles classes for known panels,
  //      so we apply ours after upstream has run. CSS gated on this class
  //      hides the sidebar while Integrations owns the screen, so our page
  //      isn't sitting next to an empty/confusing sidebar drawer.
  //
  // The refresh fetch is intentionally NOT awaited: a sibling wrapper (e.g.
  // doh-webapps.js) is waiting for us to return before it toggles its own
  // `showing-<panel>` class off, and a multi-second broker fetch in between
  // would leave both panels' classes set simultaneously, so both views would
  // render on top of each other until the fetch resolved.
  function wrapSwitchPanel() {
    if (typeof window.switchPanel !== 'function') return;
    if (window.__dohPanelWrapped) return;
    window.__dohPanelWrapped = true;
    const orig = window.switchPanel;
    window.switchPanel = async function (name) {
      const result = await orig.apply(this, arguments);
      const mainEl = document.querySelector('main.main');
      if (mainEl) mainEl.classList.toggle('showing-integrations', name === 'integrations');
      if (name === 'integrations') refreshAndRender();
      return result;
    };
  }

  function init() {
    if (!ensureSidebarTabAndPane()) {
      // Sidebar DOM not ready yet; retry on the next animation frame.
      // Happens on fresh page loads where the extension script runs before
      // the sidebar renders.
      requestAnimationFrame(init);
      return;
    }
    wrapSwitchPanel();

    const sentinel = consumeReturnSentinel();
    if (sentinel) {
      // User just came back from DOH's start/disconnect via a full page load.
      // Show the dialog first thing so it covers everything (incl. the panel-
      // switch animation), then do all the work behind it:
      //   1. switch to our panel + render (WebUI is up, so logos load) and WAIT
      //      for the logos to cache — the later re-render reuses them so the
      //      restart can't blank them.
      //   2. fire the invalidate, which kicks the system.webui restart.
      //   3. wait for the WebUI to serve again, re-render, then drop the dialog.
      const transitionModal = showTransitionModal(sentinel);
      // Yield a frame so the dialog actually paints before the render work
      // below blocks the main thread — otherwise it'd appear only after.
      new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
        .then(() => { if (typeof window.switchPanel === 'function') window.switchPanel('integrations'); })
        .then(refreshAndRender)
        .then(() => waitForLogos(10000))
        // Cache-hint only — if the broker is unreachable or returns 5xx, the
        // proxy's 401-evict path recovers stale tokens on the first real call.
        .then(() => invalidateBrokerTlsCache(sentinel.provider).catch(() => {}))
        .then(() => waitForWebui(15000))
        .then(refreshAndRender)
        .finally(() => { transitionModal.remove(); });
    } else {
      refreshAndRender();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
