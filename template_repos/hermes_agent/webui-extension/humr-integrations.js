// HUMR integrations page controller: mounts the Integrations tab (via
// humr-panel.js), renders integration cards from the broker's unified control
// API, orchestrates oauth-sentinel returns, and bootstraps.
(() => {
  'use strict';

  const namespace = window.HumrIntegrations || {};
  const {
    state = {},
    util = {},
    broker = {},
    webui = {},
    modals = {},
    oauthSentinel: oauthSentinelApi = {},
    cardSpecs = {},
  } = namespace;
  const { elem, statusLabelFor, byCategoryThenLabel } = util;
  const { fetchIntegrations, refreshAll, invalidateTlsCache } = broker;
  const { logoImg, waitForLogos, waitForWebui, refreshModelDropdowns } = webui;
  const { showTransitionModal, showOauthErrorModal } = modals;
  const { consumeOAuthSentinel, oauthErrorMessage } = oauthSentinelApi;

  // In-flight connect/disconnect keys for UI disablement.
  const _connecting = new Set();
  const _disconnecting = new Set();

  async function refreshAfterChange(cardSpec) {
    await refreshAndRender();
    if (cardSpec.affectsModelPicker) await refreshModelDropdowns();
  }

  const cardActions = {
    isConnecting(cardSpec) {
      return _connecting.has(cardSpec.key);
    },
    async connect(cardSpec) {
      if (cardActions.isConnecting(cardSpec)) return;
      _connecting.add(cardSpec.key);
      renderMainViewAndLeftPane();
      let result = null;
      try {
        result = await cardSpec.connect();
        if (result && result.outcome === 'changed') await refreshAfterChange(cardSpec);
      } catch (err) {
        alert(err.message || 'Connect failed. Please try again.');
      } finally {
        // OAuth redirect: keep the key in _connecting until unload so the card stays disabled mid-nav.
        if (!result || result.outcome !== 'navigating') {
          _connecting.delete(cardSpec.key);
          renderMainViewAndLeftPane();
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
      renderMainViewAndLeftPane();
      try {
        await cardSpec.disconnect();
        await refreshAfterChange(cardSpec);
      } catch (err) {
        alert(err.message || 'Disconnect failed. Please try again.');
      } finally {
        _disconnecting.delete(cardSpec.key);
        renderMainViewAndLeftPane();
      }
    },
  };

  let _refreshInflight = false;

  async function startRefreshCatalog() {
    if (_refreshInflight) return;
    const btn = document.getElementById('humrIntegrationRefreshBtn');
    const note = document.getElementById('humrIntegrationRefreshNote');
    if (note) { note.style.display = 'none'; note.textContent = ''; }
    _refreshInflight = true;
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Refreshing…';
    }
    // Model-provider refreshes restart model state. Keep the transition modal up
    // until the picker is rebuilt so the user cannot select a stale model.
    const affectsModels = !!(state.current && Array.isArray(state.current.items)
      && state.current.items.some((integration) => integration && integration.affects_model_picker));
    const modal = affectsModels
      ? showTransitionModal({
        title: 'Refreshing integrations…',
        body: 'Syncing status and models with Humanity Rules. This will only take a moment.',
      })
      : null;
    try {
      // The broker reloads MCP state and invalidates every TLS provider. A 429
      // returns before invalidation, preventing repeated gateway restarts.
      const response = await refreshAll();
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
      // Refresh-all can change org-shared provider state without cardActions, so
      // make the live model picker match the refreshed broker state.
      if (affectsModels) await refreshModelDropdowns();
    } catch (_) {
      if (note) {
        note.textContent = 'Could not reach the integrations broker.';
        note.style.display = '';
      }
    } finally {
      if (modal) modal.remove();
      _refreshInflight = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Refresh';
      }
    }
  }

  // ── Shared card + disconnect helpers ──────────────────────────────

  // A Disconnect button with the shared in-flight treatment: disabled and
  // labelled "Disconnecting…" while the integration's canonical key is in the
  // pending set.
  function disconnectButton(cardSpec) {
    const pending = cardActions.isDisconnecting(cardSpec);
    const props = {
      class: 'humr-integration-btn humr-integration-btn-secondary',
      onclick: () => cardActions.disconnect(cardSpec),
    };
    if (pending) props.disabled = true;
    return elem('button', props, [pending ? 'Disconnecting…' : 'Disconnect']);
  }

  // Read-only footer for a provisioned (org- or platform-shared) connection:
  // replaces the Configure / Disconnect actions. Wording mirrors the CP user panel.
  function sharedProvisionNote(label) {
    return elem('div', { class: 'humr-integration-org-shared' }, [label]);
  }

  function configureButton(cardSpec) {
    const pending = cardActions.isConnecting(cardSpec);
    return elem('button', {
      class: 'humr-integration-btn',
      onclick: () => cardActions.connect(cardSpec),
      disabled: !cardSpec.canConfigure || pending,
    }, [pending ? 'Configuring…' : 'Configure']);
  }

  // Build the shared card shell for the grid: a vertical card with a title row
  // (optional logo + title) and a status pill in the head.
  function buildCardScaffold(cardSpec) {
    const card = elem('div', { class: 'humr-integration-card' });
    const titleRow = elem('div', { class: 'humr-integration-card-title-row' });
    if (cardSpec.logoUrl) titleRow.appendChild(logoImg(cardSpec.logoUrl));
    titleRow.appendChild(elem('div', { class: 'humr-integration-card-title' }, [cardSpec.label]));
    const statusPill = elem('div', {
      class: 'humr-integration-card-status',
      dataset: { status: cardSpec.status },
    }, [statusLabelFor(cardSpec.status)]);
    return { card, titleRow, statusPill };
  }

  // Append the not-connected footer: a head row (title + status pill) plus a
  // body holding the Connect button, which the card CSS pins to the bottom so
  // buttons align across a grid row. cardActions owns the pending state: modal
  // outcomes clear it, while navigation outcomes keep it until the page leaves.
  function appendConnectFooter(card, titleRow, statusPill, cardSpec) {
    const pending = cardActions.isConnecting(cardSpec);
    const connectBtn = elem('button', {
      class: 'humr-integration-btn',
      onclick: () => cardActions.connect(cardSpec),
      disabled: pending,
    }, [pending ? 'Connecting…' : 'Connect']);
    if (cardSpec.status === 'not_connected') {
      // Compact single-row card: logo + name on the left, Connect on the right.
      // These cards have nothing else to show, so we drop the redundant
      // "Not connected" pill (Connect already says as much) and the empty
      // pinned-to-bottom body that left an awkward gap — no Configure either,
      // since there's nothing to configure before connecting.
      connectBtn.classList.add('humr-integration-card-head-action');
      card.appendChild(elem('div', { class: 'humr-integration-card-head' }, [titleRow, connectBtn]));
      return;
    }
    // Other not-connected states (token expired, checking…) keep the
    // informative pill in the head and the action in the pinned footer body.
    card.appendChild(elem('div', { class: 'humr-integration-card-head' }, [titleRow, statusPill]));
    const body = elem('div', { class: 'humr-integration-card-body' });
    const actions = elem('div', { class: 'humr-integration-actions humr-integration-actions-single' });
    actions.appendChild(connectBtn);
    body.appendChild(actions);
    card.appendChild(body);
  }

  function appendCardDetails(body, details, isShared) {
    for (const detail of details || []) {
      if (isShared && detail.hideWhenShared) continue;
      const props = { class: detail.className || 'humr-integration-meta' };
      if (detail.title) props.title = detail.title;
      body.appendChild(elem('div', props, [detail.text]));
    }
  }

  function renderIntegrationCard(cardSpec) {
    const { card, titleRow, statusPill } = buildCardScaffold(cardSpec);

    if (!cardSpec.isConnected) {
      appendConnectFooter(card, titleRow, statusPill, cardSpec);
      return card;
    }

    card.appendChild(elem('div', { class: 'humr-integration-card-head' }, [titleRow, statusPill]));
    const body = elem('div', { class: 'humr-integration-card-body' });
    appendCardDetails(body, cardSpec.details, !!cardSpec.provisionLabel);
    if (cardSpec.provisionLabel) {
      body.appendChild(sharedProvisionNote(cardSpec.provisionLabel));
      card.appendChild(body);
      return card;
    }

    const actions = elem('div', { class: 'humr-integration-actions' });
    actions.appendChild(configureButton(cardSpec));

    if (typeof cardSpec.disconnect === 'function') {
      actions.appendChild(disconnectButton(cardSpec));
    }
    
    body.appendChild(actions);
    card.appendChild(body);
    return card;
  }

  function renderSummary() {
    const summary = document.getElementById('humrIntegrationSummary');
    if (!summary) return;
    summary.innerHTML = '';
    const catalog = state.current;
    if (!catalog) {
      summary.appendChild(document.createTextNode('Status unavailable.'));
      return;
    }
    const integrations = catalog.items || [];
    const connected = integrations.filter((integration) => integration.status === 'connected').length;
    const total = integrations.length;
    summary.appendChild(document.createTextNode(
      total === 0
        ? 'No integrations configured.'
        : connected + ' of ' + total + ' connected'
    ));
  }

  // Resolve one normalized card specification. Exact kind+slug specializations
  // win over kind defaults; the renderer itself is mechanism-agnostic.
  function renderCard(integration) {
    const cardSpec = cardSpecs.resolve(integration);
    return cardSpec ? renderIntegrationCard(cardSpec) : null;
  }

  // Build a responsive card grid for `integrations`, or null if none render.
  function buildCardGrid(integrations) {
    const grid = elem('div', { class: 'humr-integration-grid' });
    for (const integration of integrations) {
      const card = renderCard(integration);
      if (card) grid.appendChild(card);
    }
    return grid.children.length ? grid : null;
  }

  // Append a titled section (heading + responsive card grid) to `container`.
  // Sections always render their heading so users learn the two groups exist;
  // an empty section shows `emptyHint` instead of a grid.
  function appendSection(container, title, emptyHint, integrations) {
    const section = elem('div', { class: 'humr-integration-section' }, [
      elem('div', { class: 'humr-integration-section-title' }, [title]),
    ]);
    const grid = buildCardGrid(integrations);
    section.appendChild(grid || elem('div', { class: 'humr-integration-empty' }, [emptyHint]));
    container.appendChild(section);
  }

  // Append a section whose body is split into labeled sub-groups, each a
  // sub-heading + its own card grid. Sub-groups with no cards are skipped; if
  // none have cards, `emptyHint` shows instead. Used by "Not connected" to make
  // the Model Providers → Connectors ordering explicit rather than implied.
  function appendGroupedSection(container, title, emptyHint, subGroups) {
    const section = elem('div', { class: 'humr-integration-section' }, [
      elem('div', { class: 'humr-integration-section-title' }, [title]),
    ]);
    let any = false;
    for (const sub of subGroups) {
      const grid = buildCardGrid(sub.integrations);
      if (!grid) continue;
      any = true;
      section.appendChild(elem('div', { class: 'humr-integration-section' }, [
        elem('div', { class: 'humr-integration-subsection-title' }, [sub.title]),
        grid,
      ]));
    }
    if (!any) section.appendChild(elem('div', { class: 'humr-integration-empty' }, [emptyHint]));
    container.appendChild(section);
  }

  function renderMainViewAndLeftPane() {
    renderSummary();

    const catalog = state.current;

    const list = document.getElementById('humrIntegrationList');
    if (!list) return;
    list.innerHTML = '';

    if (!catalog) {
      list.appendChild(elem('div', { class: 'humr-integration-empty' }, [
        'Integration status is unavailable. If this persists, the platform broker may not be running.',
      ]));
      return;
    }

    // Group by connection status: Connected first, then everything not yet
    // connected. The Connected grid stays a single grid sorted model-providers-
    // first; the Not-connected section is split into explicit "Model Providers"
    // and "Connectors" sub-groups so that ordering is labeled, not just implied.
    const integrations = catalog.items || [];
    const isModelProvider = (integration) => integration.category === 'model_provider';
    const isConnector = (integration) => integration.category === 'connector';
    const byLabel = (a, b) =>
      (a.label || a.slug || '').toLowerCase().localeCompare((b.label || b.slug || '').toLowerCase());

    const connected = integrations
      .filter((integration) => integration.status === 'connected')
      .slice()
      .sort(byCategoryThenLabel);

    const notConnected = integrations.filter((integration) => integration.status !== 'connected');

    appendSection(list, 'Connected', 'Nothing connected yet.', connected);
    appendGroupedSection(
      list, 'Not connected', 'Everything is connected.',
      [
        { title: 'Model Providers', integrations: notConnected.filter(isModelProvider).slice().sort(byLabel) },
        { title: 'Connectors', integrations: notConnected.filter(isConnector).slice().sort(byLabel) },
      ],
    );
  }

  let _refreshAndRenderPromise = null;

  function refreshAndRender() {
    if (_refreshAndRenderPromise) return _refreshAndRenderPromise;

    _refreshAndRenderPromise = fetchIntegrations()
      .then((catalog) => {
        state.current = catalog;
        renderMainViewAndLeftPane();
      })
      .finally(() => {
        _refreshAndRenderPromise = null;
      });
    return _refreshAndRenderPromise;
  }

  function populateMainView(view) {
    const refreshButton = elem('button', {
      class: 'humr-integration-btn humr-integration-page-refresh-btn',
      id: 'humrIntegrationRefreshBtn',
      type: 'button',
      onclick: startRefreshCatalog,
    }, ['Refresh']);
    const refreshNote = elem('div', {
      class: 'humr-integration-page-refresh-note',
      id: 'humrIntegrationRefreshNote',
      style: { display: 'none' },
    });
    
    view.appendChild(elem('div', { class: 'humr-integration-page-inner' }, [
      elem('div', { class: 'humr-integration-page-head' }, [
        elem('div', { class: 'humr-integration-page-head-row' }, [
          elem('div', { class: 'humr-integration-page-title' }, ['Integrations']),
          refreshButton,
        ]),
        elem('div', { class: 'humr-integration-page-meta' }, [
          'Third-party accounts the agent can act on.',
        ]),
        refreshNote,
      ]),
      elem('div', { class: 'humr-integration-list', id: 'humrIntegrationList' }),
    ]));
  }

  // Connected-count summary for the sidebar pane, updated on every broker
  // refresh (see renderSummary).
  function populateLeftPane(pane) {
    pane.appendChild(elem('div', { class: 'humr-integration-summary', id: 'humrIntegrationSummary' }, [
      'Loading…',
    ]));
  }

  function init() {
    if (!window.HumrIntegrations) {
      console.error('[humr-integrations] runtime failed to load; not mounting.');
      return;
    }
    if (!window.HumrPanel) {
      console.error('[humr-integrations] humr-panel.js failed to load; not mounting.');
      return;
    }
    // The six scripts are independent deferred fetches: one can 404 or throw during a deploy/restart window
    // or after a dropped response while the rest still run. Mounting a partial bundle yields click-time
    // explosions or silent provider fallbacks, so fail closed with one named error instead.
    const requiredScripts = ['runtime', 'connection-flows', 'google', 'slack', 'merge'];
    const missingScripts = requiredScripts.filter((script) => !namespace.loadedExtensionScripts.has(script));
    if (missingScripts.length) {
      const fileNames = missingScripts.map((script) => 'humr-integrations-' + script + '.js');
      console.error('[humr-integrations] extension script(s) failed to load: ' + fileNames.join(', ') + ' — not mounting.');
      return;
    }

    // Plug icon: 24×24 stroke paths; humr-panel.js sizes it per slot (rail/nav).
    const PLUG_ICON = '<path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4"/>';

    window.HumrPanel.register({
      id: 'integrations',
      title: 'Integrations',
      icon: PLUG_ICON,
      viewClass: 'humr-integration-page',
      populateMainView,
      populateLeftPane,
      onShow: refreshAndRender, // opening the tab re-syncs from the broker
      onMount: handleOauthReturnAndRender,
    });
  }

  // First render, once humr-panel.js has the shell DOM mounted. A HUMR
  // connect/disconnect flow returns to the WebUI via a full page load and
  // leaves an oauth sentinel behind; consume it and pick the matching flow.
  async function handleOauthReturnAndRender() {
    const oauthSentinel = consumeOAuthSentinel();
    if (oauthSentinel && oauthSentinel.transition === 'error') {
      // The flow died after the consent redirect; nothing changed broker-side,
      // so a plain render + explanation is enough (no cache invalidate).
      window.switchPanel('integrations');
      const refreshing = refreshAndRender();
      showOauthErrorModal(oauthErrorMessage(oauthSentinel.code));
      await refreshing;
      return;
    }
    
    if (!oauthSentinel) {
      await refreshAndRender();
      return;
    }

    // Keep the transition modal up while the broker applies the returned
    // credentials and any restarted WebUI becomes ready again.
    const transitionModal = showTransitionModal(oauthSentinel);
    try {
      // Yield two frames so the modal paints before the work begins.
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      window.switchPanel('integrations');
      await refreshAndRender();
      await waitForLogos(10000);
      // Cache hint only: the proxy's 401 path can recover if this fails.
      try { await invalidateTlsCache(oauthSentinel.provider); } catch (_) { /* best-effort */ }
      await waitForWebui(15000);
      await refreshAndRender();

      // No OAuth provider affects models today; keep the return path correct
      // if one does later.
      const integration = (state.current && Array.isArray(state.current.items))
        ? state.current.items.find((integration) => integration && integration.slug === oauthSentinel.provider)
        : null;
      if (integration && integration.affects_model_picker) await refreshModelDropdowns();
    } finally {
      transitionModal.remove();
      // A failed narrow changes state and also carries an explanation.
      if (oauthSentinel.errorCode) showOauthErrorModal(oauthErrorMessage(oauthSentinel.errorCode));
    }
  }

  init();
})();
