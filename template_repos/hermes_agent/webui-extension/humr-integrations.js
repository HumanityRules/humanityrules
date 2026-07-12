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
    flows = {},
    cardSpecs = {},
    page = {},
  } = namespace;
  const { elem, statusLabelFor, byCategoryThenLabel } = util;
  const { fetchIntegrations, refreshAll, invalidateTlsCache } = broker;
  const { logoImg, waitForLogos, waitForWebui, refreshModelDropdownsIfProviderAffectsPicker } = webui;
  const { showTransitionModal, showOauthErrorModal } = modals;
  const { consumeOAuthSentinel, oauthErrorMessage } = oauthSentinelApi;
  const {
    markConnecting,
    runDisconnect,
  } = flows;

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
    // state.current is already populated (the pane renders on open), so we can tell
    // up front whether this Refresh will rebuild the model picker. Gate on the
    // catalog actually carrying a model provider so deployments with only non-
    // model connectors skip the /api/models round-trip. When it will rebuild,
    // hold the same full-screen dialog the connect-return path uses: refresh_all
    // restarts the model gateway, and blocking interaction until the picker is
    // rebuilt stops the user opening a new chat against the stale dropdown
    // mid-rebuild — the very symptom this whole change fixes.
    const affectsModels = !!(state.current && Array.isArray(state.current.items)
      && state.current.items.some((it) => it && it.affects_model_picker));
    const modal = affectsModels
      ? showTransitionModal({
        title: 'Refreshing integrations…',
        body: 'Syncing status and models with Humanity Rules. This will only take a moment.',
      })
      : null;
    try {
      // One round-trip: the broker reloads the MCP catalog AND invalidates the
      // all-providers TLS cache (which refetches from HUMR and, for vault
      // providers, restarts the gateway). Cooldown 429 short-circuits before
      // the TLS side runs, so repeated clicks while the cooldown is active
      // can't keep kicking the gateway.
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
      // Refresh-all can flip a model provider's connection — e.g. an org-shared
      // OpenRouter key provisioned on the control plane. That never goes through
      // the per-connector vault flow, which is what rebuilds the composer's model
      // picker via refreshModelDropdownsIfProviderAffectsPicker(). We need to call 
      // that after Refresh-all too, or the dropdown keeps its boot-time catalog until a full
      // page reload even though the broker now reports the provider connected.
      // Covers connect AND disconnect (the rebuild re-reads /api/models, so a
      // revoked shared key also drops out live).
      if (affectsModels) {
        await refreshModelDropdownsIfProviderAffectsPicker({ affects_model_picker: true });
      }
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
  function disconnectButton(key, onclick) {
    const pending = state.disconnecting.has(key);
    const props = { class: 'humr-integration-btn humr-integration-btn-secondary', onclick };
    if (pending) props.disabled = true;
    return elem('button', props, [pending ? 'Disconnecting…' : 'Disconnect']);
  }

  // Read-only footer for a provisioned (org- or platform-shared) connection:
  // replaces the Configure / Disconnect actions. Wording mirrors the CP user panel.
  function sharedProvisionNote(label) {
    return elem('div', { class: 'humr-integration-org-shared' }, [label]);
  }

  function configureButton(cardSpec) {
    const canConfigure = typeof cardSpec.configure === 'function';
    const props = { class: 'humr-integration-btn' };
    if (canConfigure) {
      props.onclick = cardSpec.configure;
    } else {
      props.disabled = true;
    }
    return elem('button', props, ['Configure']);
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
  // buttons align across a grid row. `onConnect` receives the markConnecting
  // revert fn, which modal flows (vault, Merge) call to restore the button on
  // cancel/error and navigation flows simply let persist as the page leaves.
  function appendConnectFooter(card, titleRow, statusPill, cardSpec) {
    const connectBtn = elem('button', {
      class: 'humr-integration-btn',
      onclick: () => { cardSpec.connect(markConnecting(connectBtn)); },
    }, ['Connect']);
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
      actions.appendChild(disconnectButton(
        cardSpec.key,
        () => runDisconnect(cardSpec.key, cardSpec.disconnect),
      ));
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

  async function refreshAndRender() {
    state.current = await fetchIntegrations();
    renderMainViewAndLeftPane();
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

    page.configure({ rerender: renderMainViewAndLeftPane, refreshAndRender });

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
  function handleOauthReturnAndRender() {
    const oauthSentinel = consumeOAuthSentinel();
    if (oauthSentinel && oauthSentinel.transition === 'error') {
      // The flow died after the consent redirect; nothing changed broker-side,
      // so a plain render + explanation is enough (no cache invalidate).
      window.switchPanel('integrations');
      refreshAndRender();
      showOauthErrorModal(oauthErrorMessage(oauthSentinel.code));
    } else if (oauthSentinel) {
      // User just came back from HUMR's start/disconnect via a full page load.
      // Show the dialog first thing so it covers everything (incl. the panel-
      // switch animation), then do all the work behind it:
      //   1. switch to our panel + render (WebUI is up, so logos load) and WAIT
      //      for the logos to cache — the later re-render reuses them so the
      //      restart can't blank them.
      //   2. fire the invalidate, which kicks the system.webui restart.
      //   3. wait for the WebUI to serve again, re-render, then drop the dialog.
      const transitionModal = showTransitionModal(oauthSentinel);
      // Yield a frame so the dialog actually paints before the render work
      // below blocks the main thread — otherwise it'd appear only after.
      new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
        .then(() => window.switchPanel('integrations'))
        .then(refreshAndRender)
        .then(() => waitForLogos(10000))
        // Cache-hint only — if the broker is unreachable or returns 5xx, the
        // proxy's 401-evict path recovers stale tokens on the first real call.
        .then(() => invalidateTlsCache(oauthSentinel.provider).catch(() => {}))
        .then(() => waitForWebui(15000))
        .then(refreshAndRender)
        // If the provider returning through HUMR's start/disconnect feeds the
        // model picker (a model provider — e.g. a future OAuth-based Gemini),
        // rebuild the composer dropdown the same way the vault connect and
        // Refresh-all paths do. No OAuth provider is a model provider today, so
        // this is a no-op for now: the lookup finds the integration in the just-
        // refreshed catalog and refreshModelDropdownsIfProviderAffectsPicker()
        // self-guards on its affects_model_picker flag.
        .then(() => refreshModelDropdownsIfProviderAffectsPicker(
          (state.current && Array.isArray(state.current.items))
            ? state.current.items.find((it) => it && it.slug === oauthSentinel.provider)
            : null,
        ))
        .finally(() => {
          transitionModal.remove();
          // A failed narrow rides the disconnected transition with an error
          // code attached — explain it once the card reflects reality.
          if (oauthSentinel.errorCode) showOauthErrorModal(oauthErrorMessage(oauthSentinel.errorCode));
        });
    } else {
      refreshAndRender();
    }
  }

  init();
})();
