// DOH WebUI extension. Loaded per Hermes' docs/EXTENSIONS.md via the
// HERMES_WEBUI_EXTENSION_* env vars exported in supervisor.sh.
//
// Adds an "Integrations" section to the WebUI's Settings panel, rendering
// per-provider cards from the integrations broker's control API. The
// broker is reached same-origin via the WebUI reverse-proxy patch
// (patches-webui/07-doh-broker-proxy.patch). Connect / Disconnect are
// top-level navigations to DOH's control plane; we never call DOH
// cross-origin.
(() => {
  'use strict';

  const STATUS_URL = '/__doh_broker/status';
  const KICK_URL = '/__doh_broker/kick';

  let _currentStatus = null;

  function elem(tag, props, children) {
    const el = document.createElement(tag);
    if (props) {
      for (const k in props) {
        if (k === 'style' && typeof props[k] === 'object') Object.assign(el.style, props[k]);
        else if (k === 'dataset' && typeof props[k] === 'object') Object.assign(el.dataset, props[k]);
        else if (k.startsWith('on') && typeof props[k] === 'function') el.addEventListener(k.slice(2), props[k]);
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

  async function fetchStatus() {
    try {
      const response = await fetch(STATUS_URL, { cache: 'no-store' });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  async function kickBroker() {
    try {
      const response = await fetch(KICK_URL, { method: 'POST', cache: 'no-store' });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) { /* best-effort; the broker is loopback */ }
    return null;
  }

  function buildConnectUrl(status, returnTo) {
    const rd = encodeURIComponent(returnTo);
    return status.doh_control_plane_url.replace(/\/$/, '') + '/integrations/google/start?rd=' + rd;
  }

  function buildDisconnectUrl(status, returnTo) {
    const rd = encodeURIComponent(returnTo);
    return status.doh_control_plane_url.replace(/\/$/, '') + '/integrations/google/disconnect?rd=' + rd;
  }

  function renderProviderCard(providerKey, providerState, status) {
    const label = providerState.label || providerKey;
    const returnTo = window.location.origin + window.location.pathname;

    const card = elem('div', { class: 'doh-integration-card', dataset: { provider: providerKey } });
    const header = elem('div', { class: 'doh-integration-card-head' }, [
      elem('div', { class: 'doh-integration-card-title' }, [label]),
      elem('div', { class: 'doh-integration-card-status', dataset: { status: providerState.status } }, [
        providerState.status === 'connected' ? 'Connected' :
        providerState.status === 'not_connected' ? 'Not connected' :
        providerState.status === 'revoked' ? 'Revoked' :
        providerState.status === 'transient_error' ? 'Checking…' : '—',
      ]),
    ]);
    card.appendChild(header);

    const body = elem('div', { class: 'doh-integration-card-body' });
    if (providerState.status === 'connected') {
      if (providerState.last_refreshed_at) {
        body.appendChild(elem('div', { class: 'doh-integration-meta' }, [
          'Last refreshed: ' + formatDate(providerState.last_refreshed_at),
        ]));
      }
      body.appendChild(elem('button', {
        class: 'doh-integration-btn doh-integration-btn-secondary',
        onclick: () => { window.location.href = buildDisconnectUrl(status, returnTo); },
      }, ['Disconnect']));
    } else if (providerState.status === 'revoked') {
      body.appendChild(elem('div', { class: 'doh-integration-meta' }, [
        'The connection was removed at Google. Reconnect to restore access.',
      ]));
      body.appendChild(elem('button', {
        class: 'doh-integration-btn doh-integration-btn-primary',
        onclick: () => { window.location.href = buildConnectUrl(status, returnTo); },
      }, ['Reconnect']));
    } else if (providerState.status === 'transient_error') {
      body.appendChild(elem('div', { class: 'doh-integration-meta' }, [
        'Checking connection…',
      ]));
    } else {
      body.appendChild(elem('button', {
        class: 'doh-integration-btn doh-integration-btn-primary',
        onclick: () => { window.location.href = buildConnectUrl(status, returnTo); },
      }, ['Connect ' + label]));
    }
    card.appendChild(body);
    return card;
  }

  function renderPane(status) {
    const pane = document.getElementById('settingsPaneIntegrations');
    if (!pane) return;
    pane.innerHTML = '';

    const head = elem('div', { class: 'settings-section-head' }, [
      elem('div', null, [
        elem('div', { class: 'settings-section-title' }, ['Integrations']),
        elem('div', { class: 'settings-section-meta' }, [
          'Third-party accounts the agent can act on. Managed by the platform.',
        ]),
      ]),
    ]);
    pane.appendChild(head);

    if (!status) {
      pane.appendChild(elem('div', { class: 'doh-integration-empty' }, [
        'Integration status is unavailable. If this persists, the platform broker may not be running.',
      ]));
      return;
    }

    const list = elem('div', { class: 'doh-integration-list' });
    const providers = status.providers || {};
    const keys = Object.keys(providers);
    if (keys.length === 0) {
      list.appendChild(elem('div', { class: 'doh-integration-empty' }, ['No integrations configured.']));
    } else {
      for (const key of keys) {
        list.appendChild(renderProviderCard(key, providers[key], status));
      }
    }
    pane.appendChild(list);
  }

  async function refreshAndRender() {
    _currentStatus = await fetchStatus();
    renderPane(_currentStatus);
  }

  // After the Connect/Disconnect round-trip returns us here, ask the broker to
  // refresh now and render the status returned by /kick.
  async function refreshAfterFlow() {
    const status = await kickBroker();
    if (status) {
      _currentStatus = status;
      renderPane(_currentStatus);
      return;
    }
    await refreshAndRender();
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
    return connected ? 'connected' : 'disconnected';
  }

  function ensureMenuItemAndPane() {
    const menu = document.getElementById('settingsMenu');
    const main = document.querySelector('#mainSettings .settings-main');
    if (!menu || !main) return false;
    if (document.getElementById('dohIntegrationsMenuItem')) return true;

    // Menu item, after the last existing one.
    const btn = elem('button', {
      type: 'button',
      class: 'side-menu-item',
      id: 'dohIntegrationsMenuItem',
      dataset: { settingsSection: 'integrations' },
      onclick: () => showIntegrationsSection(),
    }, []);
    // Plug icon.
    btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4"/></svg><span>Integrations</span>';
    menu.appendChild(btn);

    // Pane under .settings-main. Same shape as upstream panes.
    const pane = elem('div', { class: 'settings-pane', id: 'settingsPaneIntegrations' });
    main.appendChild(pane);
    return true;
  }

  function showIntegrationsSection() {
    // Deactivate upstream menu items + panes, activate ours. We bypass
    // upstream's switchSettingsSection because its allow-list doesn't include
    // our name.
    document.querySelectorAll('#settingsMenu .side-menu-item').forEach((it) => {
      it.classList.toggle('active', it.id === 'dohIntegrationsMenuItem');
    });
    document.querySelectorAll('#mainSettings .settings-pane').forEach((p) => {
      p.classList.toggle('active', p.id === 'settingsPaneIntegrations');
    });
    // Mobile dropdown (if present) won't have our value; clear selection.
    const dd = document.getElementById('settingsSectionDropdown');
    if (dd) dd.value = '';
    refreshAndRender();
  }

  // Wrap upstream switchSettingsSection so clicks on the other menu items
  // correctly deactivate our pane. Upstream's function only toggles its own
  // known panes, leaving ours stuck ".active" from the previous visit.
  function wrapSwitchSettingsSection() {
    if (typeof window.switchSettingsSection !== 'function') return;
    if (window.__dohSettingsWrapped) return;
    window.__dohSettingsWrapped = true;
    const orig = window.switchSettingsSection;
    window.switchSettingsSection = function (name) {
      const ours = document.getElementById('settingsPaneIntegrations');
      if (ours) ours.classList.remove('active');
      const ourBtn = document.getElementById('dohIntegrationsMenuItem');
      if (ourBtn) ourBtn.classList.remove('active');
      return orig.apply(this, arguments);
    };
  }

  function init() {
    if (!ensureMenuItemAndPane()) {
      // Settings DOM not ready yet; retry on the next animation frame.
      // Happens on fresh page loads where the extension script runs before
      // panels render.
      requestAnimationFrame(init);
      return;
    }
    wrapSwitchSettingsSection();

    const sentinel = consumeReturnSentinel();
    if (sentinel) {
      // User just came back from DOH's start/disconnect. Route them straight
      // to the Integrations pane and nudge the broker to refresh now.
      if (typeof window.switchPanel === 'function') window.switchPanel('settings');
      showIntegrationsSection();
      refreshAfterFlow();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
