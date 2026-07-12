// HUMR WebUI extension — Web Apps panel.
//
// Reads from the platform-owned `__admin` webapp (FastAPI, served same-origin
// at /webapps/__admin/api/). Mounted through humr-panel.js: rail icon + sidebar
// summary panel + main view, polling every 3s while the panel is active.
// Read-only in v1; mutations stay on the CLI.
(() => {
  'use strict';

  if (!window.HumrPanel) {
    console.error('[humr-webapps] humr-panel.js failed to load; not mounting.');
    return;
  }
  const elem = window.HumrPanel.elem;

  const API_URL = '/webapps/__admin/api/webapps';
  const POLL_MS = 3000;

  function logUrlFor(slug) {
    return API_URL + '/' + encodeURIComponent(slug) + '/logs?format=text';
  }

  let _current = null;
  let _pollTimer = null;
  let _showInternal = false;

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
    return elem('div', { class: 'humr-webapp-empty' }, [
      elem('div', { class: 'humr-webapp-empty-title' }, ['No web apps yet']),
      elem('p', { class: 'humr-webapp-empty-hint' }, [
        'Your first one will show up in this list.',
      ]),
    ]);
  }

  function renderSummary(payload) {
    const summary = document.getElementById('humrWebappsSummary');
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
      summary.appendChild(elem('div', { class: 'humr-webapp-summary-empty' }, [
        elem('div', { class: 'humr-webapp-summary-empty-title' }, ['No web apps yet']),
        elem('div', { class: 'humr-webapp-summary-empty-hint' }, [
          'Ask the agent in chat to create one.',
        ]),
      ]));
      return;
    }
    summary.appendChild(document.createTextNode(running + ' of ' + total + ' running'));
  }

  function renderRow(item) {
    const card = elem('div', { class: 'humr-webapp-card', dataset: { slug: item.slug } });
    const head = elem('div', { class: 'humr-webapp-card-head' });
    head.appendChild(elem('div', { class: 'humr-webapp-card-title' }, [item.slug]));

    const statusKey = item.is_ready === 'Ready' ? 'ready' : (item.status || 'pending').toLowerCase();
    const statusText = item.is_ready === 'Ready' ? 'Ready' : statusLabelFor(item.status);
    head.appendChild(elem('div', {
      class: 'humr-webapp-status',
      dataset: { status: statusKey },
    }, [statusText]));
    card.appendChild(head);

    const meta = elem('div', { class: 'humr-webapp-meta' });
    if (item.port != null) {
      meta.appendChild(elem('span', { class: 'humr-webapp-meta-item' }, ['port ' + item.port]));
    }
    if (item.restarts) {
      meta.appendChild(elem('span', { class: 'humr-webapp-meta-item' }, [item.restarts + ' restarts']));
    }
    if (item.is_internal) {
      meta.appendChild(elem('span', { class: 'humr-webapp-meta-item humr-webapp-meta-internal' }, ['internal']));
    }
    card.appendChild(meta);

    if (item.routed && item.url) {
      card.appendChild(elem('a', {
        class: 'humr-webapp-url',
        href: item.url,
        target: '_blank',
        rel: 'noopener noreferrer',
      }, [item.url]));
    } else {
      card.appendChild(elem('div', { class: 'humr-webapp-url humr-webapp-url-muted' }, ['(stopped)']));
    }

    card.appendChild(elem('a', {
      class: 'humr-webapp-logs-link',
      href: logUrlFor(item.slug),
      target: '_blank',
      rel: 'noopener noreferrer',
    }, ['View logs']));
    return card;
  }

  function renderPane(payload) {
    renderSummary(payload);

    const note = document.getElementById('humrWebappsExamplesNote');
    if (note) note.hidden = !payload || visibleItems(payload).length === 0;

    const list = document.getElementById('humrWebappsList');
    if (!list) return;
    list.innerHTML = '';

    if (!payload) {
      list.appendChild(elem('div', { class: 'humr-webapp-empty' }, [
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
      _pollTimer = null; // this timer fired; nothing is scheduled right now
      if (!isPanelActive()) return;
      await refreshAndRender();
      // Re-check after the await: the user may have left the panel during the
      // fetch (stop for good), or left and come back (startPolling already
      // scheduled a fresh chain; rescheduling here would double the polling).
      if (!isPanelActive() || _pollTimer != null) return;
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

  // Globe-with-grid icon — distinct from the integrations plug. Conveys
  // "web app at a public URL." humr-panel.js sizes it per slot (rail/nav).
  const GLOBE_ICON = '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>';

  function populateView(view) {
    view.appendChild(
      elem('div', { class: 'humr-webapp-page-inner' }, [
        elem('div', { class: 'humr-webapp-page-head' }, [
          elem('div', { class: 'humr-webapp-page-title' }, ['Web Apps']),
          elem('div', { class: 'humr-webapp-intro' }, [
            elem('p', null, [
              'Web apps are tools your agent builds and runs for you, each at its own web address. They sit behind your login, so ',
              elem('strong', { class: 'humr-webapp-intro-strong' }, ['only you can open them.']),
            ]),
            elem('p', null, [
              'To build your first one, just ask in chat: “Make me a pomodoro timer that logs my focus sessions.” The agent writes the code, starts the app, and the link appears here. From there, describe the change you want and the agent updates the app.',
            ]),
            // Only true while apps are listed; renderPane toggles it.
            elem('p', { id: 'humrWebappsExamplesNote', hidden: '' }, [
              'The apps below shipped with your agent as examples. Try them out, or ask the agent to change or remove them.',
            ]),
          ]),
        ]),
        elem('div', { class: 'humr-webapp-list', id: 'humrWebappsList' }),
      ]),
    );
  }

  // Running-count summary for the sidebar pane, updated on every refresh.
  function populatePane(pane) {
    pane.appendChild(elem('div', { class: 'humr-webapp-summary', id: 'humrWebappsSummary' }, [
      'Loading…',
    ]));
  }

  window.HumrPanel.register({
    id: 'webapps',
    title: 'Web Apps',
    icon: GLOBE_ICON,
    viewClass: 'humr-webapp-page',
    populateView,
    populatePane,
    async onShow() {
      await refreshAndRender();
      // The user may have already left during the fetch; don't restart the
      // polling their onHide just stopped.
      if (isPanelActive()) startPolling();
    },
    onHide: stopPolling, // idempotent — fires on every switch to another panel
  });
})();