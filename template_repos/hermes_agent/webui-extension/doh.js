// DOH WebUI extension. Loaded per Hermes' docs/EXTENSIONS.md via the
// HERMES_WEBUI_EXTENSION_* env vars exported in supervisor.sh.
//
// Adds an "Integrations" section to the WebUI's Settings panel, rendering
// per-provider cards from /extensions/integrations_status.json (published by
// the supervisor-side integrations_refresher.py). Connect / Disconnect are
// top-level navigations to DOH's control plane; we never call DOH cross-origin.
(() => {
  'use strict';

  const STATUS_URL = '/extensions/integrations_status.json';
  // How long to poll for a status flip after Connect/Disconnect round-trips
  // back to us. The refresher tick is 60s; 90s covers one full cycle.
  const POST_FLOW_POLL_MS = 5000;
  const POST_FLOW_POLL_DEADLINE_MS = 90000;

  let _currentStatus = null;
  let _postFlowTimer = null;
  let _postFlowStartedAt = 0;

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
      // Cache-bust; the refresher rewrites this file atomically every tick.
      const response = await fetch(STATUS_URL + '?t=' + Date.now(), { cache: 'no-store' });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
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
        'No integration status file found yet. If this persists, the platform refresher may not be running.',
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

  function startPostFlowPoll() {
    if (_postFlowTimer) return;
    _postFlowStartedAt = Date.now();
    _postFlowTimer = setInterval(async () => {
      if (Date.now() - _postFlowStartedAt > POST_FLOW_POLL_DEADLINE_MS) {
        clearInterval(_postFlowTimer);
        _postFlowTimer = null;
        return;
      }
      await refreshAndRender();
    }, POST_FLOW_POLL_MS);
  }

  // Drop any ?connected=/?disconnected= sentinel once we've acted on it,
  // so a reload doesn't replay the poll.
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
      // to the Integrations pane, and poll until the refresher's status file
      // reflects the new state.
      if (typeof window.switchPanel === 'function') window.switchPanel('settings');
      showIntegrationsSection();
      startPostFlowPoll();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
