// DOH WebUI extension. Loaded per Hermes' docs/EXTENSIONS.md via the
// HERMES_WEBUI_EXTENSION_* env vars exported in supervisor.sh.
//
// Adds an "Integrations" section to the WebUI's Settings panel, rendering
// per-provider cards from the integrations broker's unified control API. The
// broker is reached same-origin via the WebUI reverse-proxy patch
// (patches-webui/07-doh-broker-proxy.patch). Connect / Disconnect for TLS-
// intercept providers (Google) are top-level navigations to DOH's control
// plane; MCP-aggregator providers (Notion) flow entirely through the broker.
(() => {
  'use strict';

  const INTEGRATIONS_URL = '/__doh_broker/integrations';
  const KICK_URL = '/__doh_broker/integrations/google/kick';

  let _current = null;

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

  async function fetchIntegrations() {
    try {
      const response = await fetch(INTEGRATIONS_URL, { cache: 'no-store' });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  async function kickGoogle() {
    try {
      const response = await fetch(KICK_URL, { method: 'POST', cache: 'no-store' });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) { /* best-effort; the broker is loopback */ }
    return null;
  }

  function buildGoogleConnectUrl(payload, returnTo) {
    const rd = encodeURIComponent(returnTo);
    return payload.doh_control_plane_url.replace(/\/$/, '') + '/integrations/google/start?rd=' + rd;
  }

  function buildGoogleDisconnectUrl(payload, returnTo) {
    const rd = encodeURIComponent(returnTo);
    return payload.doh_control_plane_url.replace(/\/$/, '') + '/integrations/google/disconnect?rd=' + rd;
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
      case 'revoked': return 'Revoked';
      case 'transient_error': return 'Checking…';
      case 'starting': return 'Starting…';
      default: return '—';
    }
  }

  // ── Merge connector flow ──────────────────────────────────────────

  function showMergeExplainerModal(item, onContinue) {
    const backdrop = elem('div', { class: 'doh-modal-backdrop' });
    const close = () => backdrop.remove();
    const modal = elem('div', { class: 'doh-modal' }, [
      elem('div', { class: 'doh-modal-title' }, ['Connect ' + item.label]),
      elem('div', { class: 'doh-modal-body' }, [
        'You’ll authenticate in a new tab via Merge.dev, our integration broker. ' +
        item.label + ' never sees your password — OAuth happens directly with the provider. ' +
        'Close the tab when Merge says you’re done; this list will refresh automatically.',
      ]),
      elem('div', { class: 'doh-modal-actions' }, [
        elem('button', {
          class: 'doh-integration-btn',
          onclick: close,
        }, ['Cancel']),
        elem('button', {
          class: 'doh-integration-btn doh-integration-btn-primary',
          onclick: () => { close(); onContinue(); },
        }, ['Continue']),
      ]),
    ]);
    backdrop.appendChild(modal);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
    document.body.appendChild(backdrop);
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

  async function startMergeConnect(item) {
    showMergeExplainerModal(item, async () => {
      let resp;
      try {
        resp = await fetch('/__doh_broker/integrations/merge/link-token', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ connector_slug: item.slug }),
        });
      } catch (_) {
        alert('Could not reach the integrations broker. Try again.');
        return;
      }
      if (!resp.ok) {
        alert('Merge link-token request failed.');
        return;
      }
      const data = await resp.json();
      if (!data.magic_link_url) {
        alert('Merge did not return a magic link.');
        return;
      }
      window.open(data.magic_link_url, '_blank');

      let stopped = false;
      const waiting = showMergeWaitingModal(item, () => { stopped = true; });
      const start = Date.now();
      const intervalMs = 3000;
      const timeoutMs = 5 * 60 * 1000;
      const tick = async () => {
        if (stopped) return;
        if (Date.now() - start > timeoutMs) {
          stopped = true;
          waiting.remove();
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
    });
  }

  function renderMergeConnectorCard(item) {
    const card = elem('div', { class: 'doh-integration-card', dataset: { provider: 'merge:' + item.slug } });
    const titleRow = elem('div', { class: 'doh-integration-card-title-row' });
    if (item.logo_url) {
      titleRow.appendChild(elem('img', { class: 'doh-integration-logo', src: item.logo_url, alt: '' }));
    }
    titleRow.appendChild(elem('div', { class: 'doh-integration-card-title' }, [item.label || item.slug]));
    const header = elem('div', { class: 'doh-integration-card-head' }, [
      titleRow,
      elem('div', { class: 'doh-integration-card-status', dataset: { status: item.status } }, [statusLabelFor(item.status)]),
    ]);
    card.appendChild(header);

    const body = elem('div', { class: 'doh-integration-card-body' });
    if (item.status === 'connected') {
      body.appendChild(elem('button', {
        class: 'doh-integration-btn doh-integration-btn-secondary',
        onclick: async () => {
          await fetch('/__doh_broker/integrations/merge/disconnect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ connector_slug: item.slug }),
          });
          await refreshAndRender();
        },
      }, ['Disconnect']));
    } else {
      body.appendChild(elem('button', {
        class: 'doh-integration-btn doh-integration-btn-primary',
        onclick: () => startMergeConnect(item),
      }, ['Connect ' + (item.label || item.slug)]));
    }
    card.appendChild(body);
    return card;
  }

  function renderTlsInterceptCard(item, payload) {
    const returnTo = window.location.origin + window.location.pathname;
    const card = elem('div', { class: 'doh-integration-card', dataset: { provider: item.slug } });
    const header = elem('div', { class: 'doh-integration-card-head' }, [
      elem('div', { class: 'doh-integration-card-title' }, [item.label]),
      elem('div', { class: 'doh-integration-card-status', dataset: { status: item.status } }, [statusLabelFor(item.status)]),
    ]);
    card.appendChild(header);

    const body = elem('div', { class: 'doh-integration-card-body' });
    if (item.status === 'connected') {
      if (item.last_refreshed_at) {
        body.appendChild(elem('div', { class: 'doh-integration-meta' }, [
          'Last refreshed: ' + formatDate(item.last_refreshed_at),
        ]));
      }
      body.appendChild(elem('button', {
        class: 'doh-integration-btn doh-integration-btn-secondary',
        onclick: () => { window.location.href = buildGoogleDisconnectUrl(payload, returnTo); },
      }, ['Disconnect']));
    } else if (item.status === 'revoked') {
      body.appendChild(elem('div', { class: 'doh-integration-meta' }, [
        'The connection was removed at ' + item.label + '. Reconnect to restore access.',
      ]));
      body.appendChild(elem('button', {
        class: 'doh-integration-btn doh-integration-btn-primary',
        onclick: () => { window.location.href = buildGoogleConnectUrl(payload, returnTo); },
      }, ['Reconnect']));
    } else if (item.status === 'transient_error' || item.status === 'starting') {
      body.appendChild(elem('div', { class: 'doh-integration-meta' }, ['Checking connection…']));
    } else {
      body.appendChild(elem('button', {
        class: 'doh-integration-btn doh-integration-btn-primary',
        onclick: () => { window.location.href = buildGoogleConnectUrl(payload, returnTo); },
      }, ['Connect ' + item.label]));
    }
    card.appendChild(body);
    return card;
  }

  function renderMcpAggregatorCard(item) {
    const card = elem('div', { class: 'doh-integration-card', dataset: { provider: item.slug } });
    const header = elem('div', { class: 'doh-integration-card-head' }, [
      elem('div', { class: 'doh-integration-card-title' }, [item.label]),
      elem('div', { class: 'doh-integration-card-status', dataset: { status: item.status } }, [statusLabelFor(item.status)]),
    ]);
    card.appendChild(header);

    const body = elem('div', { class: 'doh-integration-card-body' });
    if (item.status === 'connected') {
      body.appendChild(elem('button', {
        class: 'doh-integration-btn doh-integration-btn-secondary',
        onclick: async () => {
          await fetch('/__doh_broker/integrations/' + item.slug + '/disconnect', { method: 'POST' });
          await refreshAndRender();
        },
      }, ['Disconnect']));
    } else {
      body.appendChild(elem('button', {
        class: 'doh-integration-btn doh-integration-btn-primary',
        onclick: () => { window.location.href = buildMcpConnectUrl(item.slug); },
      }, ['Connect ' + item.label]));
    }
    card.appendChild(body);
    return card;
  }

  function renderPane(payload) {
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

    if (!payload) {
      pane.appendChild(elem('div', { class: 'doh-integration-empty' }, [
        'Integration status is unavailable. If this persists, the platform broker may not be running.',
      ]));
      return;
    }

    const list = elem('div', { class: 'doh-integration-list' });
    const items = payload.items || [];
    if (items.length === 0) {
      list.appendChild(elem('div', { class: 'doh-integration-empty' }, ['No integrations configured.']));
    } else {
      for (const item of items) {
        if (item.kind === 'tls_intercept') {
          list.appendChild(renderTlsInterceptCard(item, payload));
        } else if (item.kind === 'mcp_aggregator') {
          list.appendChild(renderMcpAggregatorCard(item));
        } else if (item.kind === 'merge_connector') {
          list.appendChild(renderMergeConnectorCard(item));
        }
      }
    }
    pane.appendChild(list);
  }

  async function refreshAndRender() {
    _current = await fetchIntegrations();
    renderPane(_current);
  }

  // After the Connect/Disconnect round-trip returns us here, ask the broker to
  // refresh now and render the status returned by /kick.
  async function refreshAfterFlow() {
    const kickResult = await kickGoogle();
    if (kickResult) {
      _current = kickResult;
      renderPane(_current);
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
