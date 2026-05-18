// DOH WebUI extension. Loaded per Hermes' docs/EXTENSIONS.md via the
// HERMES_WEBUI_EXTENSION_* env vars exported in supervisor.sh.
//
// Adds an "Integrations" tab to the WebUI's main left sidebar nav, rendering
// per-provider cards from the integrations broker's unified control API. The
// broker is reached same-origin via the WebUI reverse-proxy patch
// (patches-webui/07-doh-broker-proxy.patch). Connect / Disconnect for TLS-
// intercept providers (Google) are top-level navigations to DOH's control
// plane; MCP-aggregator providers (Notion) flow entirely through the broker.
(() => {
  'use strict';

  const INTEGRATIONS_URL = '/__doh_broker/integrations';

  let _current = null;
  const _disconnecting = new Set();

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

  let _refreshInflight = false;

  async function startRefreshCatalog() {
    if (_refreshInflight) return;
    const btn = document.getElementById('dohIntegrationRefreshBtn');
    const note = document.getElementById('dohIntegrationRefreshNote');
    if (note) { note.style.display = 'none'; note.textContent = ''; }
    _refreshInflight = true;
    if (btn) btn.disabled = true;
    try {
      const response = await fetch('/__doh_broker/integrations/refresh_catalog', {
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
        if (note) {
          note.textContent = 'Refresh failed. Please try again.';
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
      if (btn) btn.disabled = false;
    }
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

  function connectedFirst(a, b) {
    if (a.status === 'connected' && b.status !== 'connected') return -1;
    if (a.status !== 'connected' && b.status === 'connected') return 1;
    const aLabel = (a.label || a.slug || '').toLowerCase();
    const bLabel = (b.label || b.slug || '').toLowerCase();
    return aLabel.localeCompare(bLabel);
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

  function renderConnectorCard(item) {
    const isMergeConnector = item.kind === 'merge_connector';
    const provider = (isMergeConnector ? 'merge:' : 'mcp:') + item.slug;
    const isConnected = item.status === 'connected';
    const cardClass = isConnected ? 'doh-integration-card' : 'doh-integration-card doh-integration-card-row';
    const card = elem('div', { class: cardClass, dataset: { provider } });
    const titleRow = elem('div', { class: 'doh-integration-card-title-row' });
    if (item.logo_url) {
      titleRow.appendChild(elem('img', {
        class: 'doh-integration-logo',
        src: item.logo_url,
        alt: '',
        loading: 'lazy',
        decoding: 'async',
      }));
    }
    titleRow.appendChild(elem('div', { class: 'doh-integration-card-title' }, [item.label || item.slug]));
    const statusPill = elem('div', { class: 'doh-integration-card-status', dataset: { status: item.status } }, [statusLabelFor(item.status)]);

    if (isConnected) {
      const pending = _disconnecting.has(provider);
      const btnProps = {
        class: 'doh-integration-btn doh-integration-btn-secondary',
        onclick: async () => {
          if (_disconnecting.has(provider)) return;
          _disconnecting.add(provider);
          renderPane(_current);
          try {
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
          } finally {
            _disconnecting.delete(provider);
            renderPane(_current);
          }
        },
      };
      if (pending) btnProps.disabled = '';
      const disconnectBtn = elem('button', btnProps, [pending ? 'Disconnecting…' : 'Disconnect']);
      card.appendChild(elem('div', { class: 'doh-integration-card-head' }, [titleRow, statusPill]));
      card.appendChild(elem('div', { class: 'doh-integration-card-body' }, [disconnectBtn]));
      return card;
    }

    const connectBtn = elem('button', {
      class: 'doh-integration-btn',
      onclick: () => {
        if (isMergeConnector) startMergeConnect(item);
        else window.location.href = buildMcpConnectUrl(item.slug);
      },
    }, ['Connect']);
    const trailing = elem('div', { class: 'doh-integration-card-trailing' }, [connectBtn, statusPill]);
    card.appendChild(elem('div', { class: 'doh-integration-card-head' }, [titleRow, trailing]));
    return card;
  }

  function renderTlsInterceptCard(item, payload) {
    const returnTo = window.location.origin + window.location.pathname;
    const isConnected = item.status === 'connected';
    const cardClass = isConnected ? 'doh-integration-card' : 'doh-integration-card doh-integration-card-row';
    const card = elem('div', { class: cardClass, dataset: { provider: item.slug } });
    const titleRow = elem('div', { class: 'doh-integration-card-title-row' });
    if (item.logo_url) {
      titleRow.appendChild(elem('img', {
        class: 'doh-integration-logo',
        src: item.logo_url,
        alt: '',
        loading: 'lazy',
        decoding: 'async',
      }));
    }
    titleRow.appendChild(elem('div', { class: 'doh-integration-card-title' }, [item.label]));
    const statusPill = elem('div', { class: 'doh-integration-card-status', dataset: { status: item.status } }, [statusLabelFor(item.status)]);

    if (isConnected) {
      card.appendChild(elem('div', { class: 'doh-integration-card-head' }, [titleRow, statusPill]));
      const body = elem('div', { class: 'doh-integration-card-body' });
      if (item.last_refreshed_at) {
        body.appendChild(elem('div', { class: 'doh-integration-meta' }, [
          'Last refreshed: ' + formatDate(item.last_refreshed_at),
        ]));
      }
      body.appendChild(elem('button', {
        class: 'doh-integration-btn doh-integration-btn-secondary',
        onclick: () => { window.location.href = buildGoogleDisconnectUrl(payload, returnTo); },
      }, ['Disconnect']));
      card.appendChild(body);
      return card;
    }

    const connectBtn = elem('button', {
      class: 'doh-integration-btn',
      onclick: () => { window.location.href = buildGoogleConnectUrl(payload, returnTo); },
    }, ['Connect']);
    const trailing = elem('div', { class: 'doh-integration-card-trailing' }, [connectBtn, statusPill]);
    card.appendChild(elem('div', { class: 'doh-integration-card-head' }, [titleRow, trailing]));
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
  function wrapSwitchPanel() {
    if (typeof window.switchPanel !== 'function') return;
    if (window.__dohPanelWrapped) return;
    window.__dohPanelWrapped = true;
    const orig = window.switchPanel;
    window.switchPanel = async function (name) {
      const result = await orig.apply(this, arguments);
      const mainEl = document.querySelector('main.main');
      if (mainEl) mainEl.classList.toggle('showing-integrations', name === 'integrations');
      if (name === 'integrations') await refreshAndRender();
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
    if (sentinel && typeof window.switchPanel === 'function') {
      // User just came back from DOH's start/disconnect — route them straight
      // to the Integrations panel.
      window.switchPanel('integrations');
    }
    // /integrations force-refreshes every TLS-intercept provider before
    // responding, so the rendered status reflects DOH's current view whether
    // we got here via a return-trip or a normal page load.
    refreshAndRender();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
