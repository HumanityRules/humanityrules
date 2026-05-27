// DOH WebUI extension — Web Apps panel.
//
// Reads from the platform-owned `__admin` webapp (FastAPI, served same-origin
// at /webapps/__admin/api/). Mirrors doh-integrations.js: rail icon + sidebar
// summary panel + main view, polling every 3s while the panel is active.
// Read-only in v1; mutations stay on the CLI.
(() => {
  'use strict';

  const API_URL = '/webapps/__admin/api/webapps';
  const POLL_MS = 3000;

  let _current = null;
  let _pollTimer = null;
  let _showInternal = false;

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

  async function fetchWebapps() {
    try {
      const response = await fetch(API_URL, { cache: 'no-store' });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  function statusLabelFor(status) {
    if (!status) return '—';
    return status;
  }

  function visibleItems(payload) {
    if (!payload) return [];
    return (payload.items || []).filter((it) => _showInternal || !it.is_internal);
  }

  function emptyStateNode() {
    return elem('div', { class: 'doh-webapp-empty' }, [
      elem('div', { class: 'doh-webapp-empty-title' }, ['No web apps yet']),
      elem('p', { class: 'doh-webapp-empty-hint' }, [
        'Web apps are created by talking to the agent in chat. Describe what you want — a dashboard, API, or internal tool — and the agent will build and deploy it here.',
      ]),
      elem('div', { class: 'doh-webapp-empty-example' }, [
        elem('div', { class: 'doh-webapp-empty-example-label' }, ['Example prompt']),
        elem('div', { class: 'doh-webapp-empty-chat-composer' }, [
          elem('div', { class: 'doh-webapp-empty-chat-prompt' }, [
            'Build me a hello-world webapp',
          ]),
        ]),
      ]),
    ]);
  }

  function renderSummary(payload) {
    const summary = document.getElementById('dohWebappsSummary');
    if (!summary) return;
    summary.innerHTML = '';
    if (!payload) {
      summary.appendChild(document.createTextNode('Status unavailable.'));
      return;
    }
    const items = visibleItems(payload);
    const running = items.filter((it) => it.status === 'Running' && it.is_ready === 'Ready').length;
    const total = items.length;
    if (total === 0) {
      summary.appendChild(elem('div', { class: 'doh-webapp-summary-empty' }, [
        elem('div', { class: 'doh-webapp-summary-empty-title' }, ['No web apps yet']),
        elem('div', { class: 'doh-webapp-summary-empty-hint' }, [
          'Ask the agent in chat to create one.',
        ]),
      ]));
      return;
    }
    summary.appendChild(document.createTextNode(running + ' of ' + total + ' running'));
  }

  function renderRow(item) {
    const card = elem('div', { class: 'doh-webapp-card', dataset: { slug: item.slug } });
    const head = elem('div', { class: 'doh-webapp-card-head' });
    head.appendChild(elem('div', { class: 'doh-webapp-card-title' }, [item.slug]));

    const statusKey = item.is_ready === 'Ready' ? 'ready' : (item.status || 'pending').toLowerCase();
    const statusText = item.is_ready === 'Ready' ? 'Ready' : statusLabelFor(item.status);
    head.appendChild(elem('div', {
      class: 'doh-webapp-status',
      dataset: { status: statusKey },
    }, [statusText]));
    card.appendChild(head);

    const meta = elem('div', { class: 'doh-webapp-meta' });
    if (item.port != null) {
      meta.appendChild(elem('span', { class: 'doh-webapp-meta-item' }, ['port ' + item.port]));
    }
    if (item.restarts) {
      meta.appendChild(elem('span', { class: 'doh-webapp-meta-item' }, [item.restarts + ' restarts']));
    }
    if (item.is_internal) {
      meta.appendChild(elem('span', { class: 'doh-webapp-meta-item doh-webapp-meta-internal' }, ['internal']));
    }
    card.appendChild(meta);

    if (item.routed && item.url) {
      card.appendChild(elem('a', {
        class: 'doh-webapp-url',
        href: item.url,
        target: '_blank',
        rel: 'noopener noreferrer',
      }, [item.url]));
    } else {
      card.appendChild(elem('div', { class: 'doh-webapp-url doh-webapp-url-muted' }, ['(stopped)']));
    }
    return card;
  }

  function renderPane(payload) {
    renderSummary(payload);

    const list = document.getElementById('dohWebappsList');
    if (!list) return;
    list.innerHTML = '';

    if (!payload) {
      list.appendChild(elem('div', { class: 'doh-webapp-empty' }, [
        'Web app status is unavailable. The platform admin webapp may not be running yet.',
      ]));
      return;
    }

    const items = visibleItems(payload);
    if (items.length === 0) {
      list.appendChild(emptyStateNode());
      return;
    }
    for (const item of items) list.appendChild(renderRow(item));
  }

  async function refreshAndRender() {
    _current = await fetchWebapps();
    renderPane(_current);
  }

  function isPanelActive() {
    const mainEl = document.querySelector('main.main');
    return !!(mainEl && mainEl.classList.contains('showing-webapps'));
  }

  function startPolling() {
    if (_pollTimer != null) return;
    const tick = async () => {
      if (!isPanelActive()) {
        stopPolling();
        return;
      }
      await refreshAndRender();
      _pollTimer = setTimeout(tick, POLL_MS);
    };
    _pollTimer = setTimeout(tick, POLL_MS);
  }

  function stopPolling() {
    if (_pollTimer != null) {
      clearTimeout(_pollTimer);
      _pollTimer = null;
    }
  }

  function ensureSidebarTabAndPane() {
    const sidebar = document.querySelector('.sidebar');
    const sidebarNav = sidebar && sidebar.querySelector('.sidebar-nav');
    const mainEl = document.querySelector('main.main');
    const rail = document.querySelector('nav.rail');
    if (!sidebar || !sidebarNav || !mainEl) return false;
    if (document.getElementById('mainWebapps')) return true;

    const onActivate = () => {
      if (typeof window.switchPanel === 'function') {
        window.switchPanel('webapps', { fromRailClick: true });
      }
    };

    // Globe-with-grid icon — distinct from the integrations plug. Conveys
    // "web app at a public URL." 20×20 in the rail, 18×18 in the sidebar-nav.
    const railIcon = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>';
    const navIcon = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>';

    if (rail && !document.getElementById('dohWebappsRailBtn')) {
      const railBtn = elem('button', {
        type: 'button',
        class: 'rail-btn nav-tab has-tooltip',
        id: 'dohWebappsRailBtn',
        'aria-label': 'Web Apps',
        dataset: { panel: 'webapps', tooltip: 'Web Apps' },
        onclick: onActivate,
      });
      railBtn.innerHTML = railIcon;
      const spacer = rail.querySelector('.rail-spacer');
      if (spacer) rail.insertBefore(railBtn, spacer);
      else rail.appendChild(railBtn);
    }

    if (!document.getElementById('dohWebappsTab')) {
      const navBtn = elem('button', {
        type: 'button',
        class: 'nav-tab has-tooltip has-tooltip--bottom',
        id: 'dohWebappsTab',
        dataset: { panel: 'webapps', label: 'Web Apps', tooltip: 'Web Apps' },
        onclick: onActivate,
      });
      navBtn.innerHTML = navIcon;
      sidebarNav.appendChild(navBtn);
    }

    const view = elem('section', { class: 'main-view doh-webapp-page', id: 'mainWebapps' }, [
      elem('div', { class: 'doh-webapp-page-inner' }, [
        elem('div', { class: 'doh-webapp-page-head' }, [
          elem('div', { class: 'doh-webapp-page-title' }, ['Web Apps']),
          elem('div', { class: 'doh-webapp-page-meta' }, [
            'Apps the agent built, each at its own subdomain (<slug>.<hostname>).',
          ]),
        ]),
        elem('div', { class: 'doh-webapp-list', id: 'dohWebappsList' }),
      ]),
    ]);
    mainEl.appendChild(view);

    const sidebarPane = elem('div', { class: 'panel-view', id: 'panelWebapps' }, [
      elem('div', { class: 'panel-head' }, [
        elem('span', null, ['Web Apps']),
      ]),
      elem('div', { class: 'doh-webapp-summary', id: 'dohWebappsSummary' }, [
        'Loading…',
      ]),
    ]);
    const sidebarBottom = sidebar.querySelector('.sidebar-bottom');
    if (sidebarBottom) sidebar.insertBefore(sidebarPane, sidebarBottom);
    else sidebar.appendChild(sidebarPane);
    return true;
  }

  // doh-integrations.js already wraps switchPanel for its `showing-integrations`
  // class. Wrap again here, chaining through the previous wrapper so both
  // panels coexist regardless of script load order.
  function wrapSwitchPanel() {
    if (typeof window.switchPanel !== 'function') return;
    if (window.__dohWebappsWrapped) return;
    window.__dohWebappsWrapped = true;
    const orig = window.switchPanel;
    window.switchPanel = async function (name) {
      const result = await orig.apply(this, arguments);
      const mainEl = document.querySelector('main.main');
      if (mainEl) mainEl.classList.toggle('showing-webapps', name === 'webapps');
      if (name === 'webapps') {
        await refreshAndRender();
        startPolling();
      } else {
        stopPolling();
      }
      return result;
    };
  }

  function init() {
    if (!ensureSidebarTabAndPane()) {
      requestAnimationFrame(init);
      return;
    }
    wrapSwitchPanel();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
