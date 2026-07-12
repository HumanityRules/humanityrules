// HUMR integrations page controller: card and page rendering, WebUI shell integration,
// oauth-sentinel orchestration, and bootstrap.
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
    providers = {},
    connectors = {},
  } = namespace;
  const { elem, formatDate, statusLabelFor, byCategoryThenLabel } = util;
  const { fetchIntegrations, refreshAll, buildTlsConnectUrl, invalidateTlsCache } = broker;
  const { logoImg, waitForLogos, waitForWebui, refreshModelDropdownsIfProviderAffectsPicker } = webui;
  const { showTransitionModal, showOauthErrorModal } = modals;
  const { consumeOAuthSentinel, oauthErrorMessage } = oauthSentinelApi;
  const {
    markConnecting,
    runDisconnect,
    startVaultConfig,
    startDeviceConnect,
    disconnectTlsProvider,
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
  // labelled "Disconnecting…" while `key` is in the pending set. `key` is the
  // value tracked in state.disconnecting — the provider slug for TLS-intercept
  // cards, or the "merge:"/"mcp:"-prefixed provider id for connector cards.
  function disconnectButton(key, onclick) {
    const pending = state.disconnecting.has(key);
    const props = { class: 'humr-integration-btn humr-integration-btn-secondary', onclick };
    if (pending) props.disabled = true;
    return elem('button', props, [pending ? 'Disconnecting…' : 'Disconnect']);
  }

  // The provisioned source of this provider's active credential, when it is not
  // the user's own connection: an org admin's share (metadata.org_shared) or a
  // platform-wide default (metadata.platform_shared) — both stamped by the
  // control plane's token_refresh_batch. Such credentials are not user-managed
  // (they cannot be reconfigured or disconnected here), so the card shows a
  // read-only note instead of Configure/Disconnect. Returns the note text, or
  // null when the user owns the connection. Platform is checked first: it is the
  // lowest-priority token source, so if it stamped the outcome no org/personal
  // credential applied.
  function sharedProvisionLabel(item) {
    const meta = item.metadata || {};
    if (meta.platform_shared) return 'Provided by Humanity Rules';
    if (meta.org_shared) return 'Provided by your organization';
    return null;
  }

  // Read-only footer for a provisioned (org- or platform-shared) connection:
  // replaces the Configure / Disconnect actions. Wording mirrors the CP user panel.
  function sharedProvisionNote(label) {
    return elem('div', { class: 'humr-integration-org-shared' }, [label]);
  }

  function configureButton(item, ctx, connectBtnForRevert) {
    const canConfigure = item.connect_mode === 'vault';
    const props = { class: 'humr-integration-btn' };
    if (canConfigure) {
      props.onclick = () => {
        const revert = connectBtnForRevert ? markConnecting(connectBtnForRevert) : undefined;
        startVaultConfig(item, ctx, revert);
      };
    } else {
      props.disabled = true;
    }
    return elem('button', props, ['Configure']);
  }

  // Build the shared card shell for the grid: a vertical card with a title row
  // (optional logo + title) and a status pill in the head. `providerKey`
  // becomes the card's data-provider attribute (slug for TLS, prefixed id for
  // connectors). Each renderer fills in its own connected/not-connected body.
  function buildCardScaffold(item, providerKey) {
    const isConnected = item.status === 'connected';
    const card = elem('div', { class: 'humr-integration-card', dataset: { provider: providerKey } });
    const titleRow = elem('div', { class: 'humr-integration-card-title-row' });
    if (item.logo_url) titleRow.appendChild(logoImg(item.logo_url));
    titleRow.appendChild(elem('div', { class: 'humr-integration-card-title' }, [item.label || item.slug]));
    const statusPill = elem('div', { class: 'humr-integration-card-status', dataset: { status: item.status } }, [statusLabelFor(item.status)]);
    return { card, titleRow, statusPill, isConnected };
  }

  // Append the not-connected footer: a head row (title + status pill) plus a
  // body holding the Connect button, which the card CSS pins to the bottom so
  // buttons align across a grid row. `onConnect` receives the markConnecting
  // revert fn, which modal flows (vault, Merge) call to restore the button on
  // cancel/error and navigation flows simply let persist as the page leaves.
  function appendConnectFooter(card, titleRow, statusPill, item, onConnect) {
    const connectBtn = elem('button', {
      class: 'humr-integration-btn',
      onclick: () => { onConnect(markConnecting(connectBtn)); },
    }, ['Connect']);
    if (item.status === 'not_connected') {
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

  function renderConnectorCard(item, ctx) {
    const adapter = connectors.get(item.kind);
    if (!adapter) return null;
    const provider = adapter.cardKey(item);
    const { card, titleRow, statusPill, isConnected } = buildCardScaffold(item, provider);

    if (isConnected) {
      card.appendChild(elem('div', { class: 'humr-integration-card-head' }, [titleRow, statusPill]));
      const body = elem('div', { class: 'humr-integration-card-body' });
      const connectorSharedLabel = sharedProvisionLabel(item);
      if (connectorSharedLabel) {
        // No connector is org/platform-shareable today (only key/login providers
        // are), but honoring the flag here keeps the read-only treatment
        // consistent if connector adapters ever gain sharing.
        body.appendChild(sharedProvisionNote(connectorSharedLabel));
        card.appendChild(body);
        return card;
      }
      const disconnectBtn = disconnectButton(provider, () => runDisconnect(
        provider,
        ctx,
        () => adapter.disconnect(item, ctx),
      ));
      const actions = elem('div', { class: 'humr-integration-actions' });
      actions.appendChild(configureButton(item, ctx));
      actions.appendChild(disconnectBtn);
      body.appendChild(actions);
      card.appendChild(body);
      return card;
    }

    appendConnectFooter(card, titleRow, statusPill, item, (revert) => {
      adapter.connect(item, ctx, revert);
    });
    return card;
  }

  function renderTlsInterceptCard(item, ctx) {
    const usesVault = item.connect_mode === 'vault';
    const usesDevice = item.connect_mode === 'device';
    const adapter = providers.get(item.slug);
    const { card, titleRow, statusPill, isConnected } = buildCardScaffold(item, item.slug);

    if (isConnected) {
      card.appendChild(elem('div', { class: 'humr-integration-card-head' }, [titleRow, statusPill]));
      const body = elem('div', { class: 'humr-integration-card-body' });
      // Slack personal mode resolves an owner; only that provider sets it.
      const ownerName = item.metadata && item.metadata.owner_name;
      if (ownerName) {
        body.appendChild(elem('div', { class: 'humr-integration-meta' }, ['Replies only to ' + ownerName]));
      }
      const sharedLabel = sharedProvisionLabel(item);
      if (sharedLabel) {
        // Org- or platform-provided credentials are not user-managed: no
        // Configure/Disconnect, and we drop "Last refreshed" to keep the
        // read-only card clean.
        body.appendChild(sharedProvisionNote(sharedLabel));
        card.appendChild(body);
        return card;
      }
      if (item.last_refreshed_at) {
        body.appendChild(elem('div', {
          class: 'humr-integration-meta humr-integration-meta-refresh',
          title: 'Last refreshed: ' + formatDate(item.last_refreshed_at),
        }, [
          'Last refreshed: ' + formatDate(item.last_refreshed_at),
        ]));
      }
      // Configure is always shown; provider adapters can override the action,
      // vault providers open the credential modal, and everyone else renders
      // it disabled.
      const actions = elem('div', { class: 'humr-integration-actions' });
      if (adapter && adapter.configure) {
        actions.appendChild(elem('button', {
          class: 'humr-integration-btn',
          onclick: () => adapter.configure(item, ctx),
        }, ['Configure']));
      } else {
        actions.appendChild(configureButton(item, ctx));
      }
      actions.appendChild(disconnectButton(item.slug, () => { disconnectTlsProvider(item, ctx); }));
      body.appendChild(actions);
      card.appendChild(body);
      return card;
    }

    appendConnectFooter(card, titleRow, statusPill, item, (revert) => {
      if (adapter && adapter.connect) adapter.connect(item, ctx, revert);
      else if (usesVault) startVaultConfig(item, ctx, revert);
      else if (usesDevice) startDeviceConnect(item, ctx, revert);
      else window.location.href = buildTlsConnectUrl(ctx.payload, item.slug, ctx.returnTo);
    });
    return card;
  }

  function renderSummary(payload) {
    const summary = document.getElementById('humrIntegrationSummary');
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

  // Render one item's card by kind. TLS-intercept cards need the page context for
  // their Connect URL; connector cards (MCP + Merge) are self-contained.
  function renderCard(item, ctx) {
    if (item.kind === 'tls_intercept') return renderTlsInterceptCard(item, ctx);
    if (connectors.get(item.kind)) return renderConnectorCard(item, ctx);
    return null;
  }

  // Build a responsive card grid for `items`, or null if none render.
  function buildCardGrid(items, ctx) {
    const grid = elem('div', { class: 'humr-integration-grid' });
    for (const item of items) {
      const card = renderCard(item, ctx);
      if (card) grid.appendChild(card);
    }
    return grid.children.length ? grid : null;
  }

  // Append a titled section (heading + responsive card grid) to `container`.
  // Sections always render their heading so users learn the two groups exist;
  // an empty section shows `emptyHint` instead of a grid.
  function appendSection(container, title, items, ctx, emptyHint) {
    const section = elem('div', { class: 'humr-integration-section' }, [
      elem('div', { class: 'humr-integration-section-title' }, [title]),
    ]);
    const grid = buildCardGrid(items, ctx);
    section.appendChild(grid || elem('div', { class: 'humr-integration-empty' }, [emptyHint]));
    container.appendChild(section);
  }

  // Append a section whose body is split into labeled sub-groups, each a
  // sub-heading + its own card grid. Sub-groups with no cards are skipped; if
  // none have cards, `emptyHint` shows instead. Used by "Not connected" to make
  // the Model Providers → Connectors ordering explicit rather than implied.
  function appendGroupedSection(container, title, subGroups, ctx, emptyHint) {
    const section = elem('div', { class: 'humr-integration-section' }, [
      elem('div', { class: 'humr-integration-section-title' }, [title]),
    ]);
    let any = false;
    for (const sub of subGroups) {
      const grid = buildCardGrid(sub.items, ctx);
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

  function renderPane(payload) {
    renderSummary(payload);

    const ctx = {
      payload,
      returnTo: window.location.origin + window.location.pathname,
      rerender: () => renderPane(state.current),
      refreshAndRender,
    };

    const list = document.getElementById('humrIntegrationList');
    if (!list) return;
    list.innerHTML = '';

    if (!payload) {
      list.appendChild(elem('div', { class: 'humr-integration-empty' }, [
        'Integration status is unavailable. If this persists, the platform broker may not be running.',
      ]));
      return;
    }

    // Group by connection status: Connected first, then everything not yet
    // connected. The Connected grid stays a single grid sorted model-providers-
    // first; the Not-connected section is split into explicit "Model Providers"
    // and "Connectors" sub-groups so that ordering is labeled, not just implied.
    const items = payload.items || [];
    const isModelProvider = (it) => it.category === 'model_provider';
    const byLabel = (a, b) =>
      (a.label || a.slug || '').toLowerCase().localeCompare((b.label || b.slug || '').toLowerCase());

    const connected = items
      .filter((it) => it.status === 'connected')
      .slice()
      .sort(byCategoryThenLabel);
    const notConnected = items.filter((it) => it.status !== 'connected');

    appendSection(list, 'Connected', connected, ctx, 'Nothing connected yet.');
    appendGroupedSection(list, 'Not connected', [
      { title: 'Model Providers', items: notConnected.filter(isModelProvider).slice().sort(byLabel) },
      { title: 'Connectors', items: notConnected.filter((it) => !isModelProvider(it)).slice().sort(byLabel) },
    ], ctx, 'Everything is connected.');
  }

  async function refreshAndRender() {
    state.current = await fetchIntegrations();
    renderPane(state.current);
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

    if (rail && !document.getElementById('humrIntegrationsRailBtn')) {
      const railBtn = elem('button', {
        type: 'button',
        class: 'rail-btn nav-tab has-tooltip',
        id: 'humrIntegrationsRailBtn',
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

    if (!document.getElementById('humrIntegrationsTab')) {
      const navBtn = elem('button', {
        type: 'button',
        class: 'nav-tab has-tooltip has-tooltip--bottom',
        id: 'humrIntegrationsTab',
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
    const view = elem('section', { class: 'main-view humr-integration-page', id: 'mainIntegrations' }, [
      elem('div', { class: 'humr-integration-page-inner' }, [
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
      elem('div', { class: 'humr-integration-summary', id: 'humrIntegrationSummary' }, [
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
  // humr-webapps.js) is waiting for us to return before it toggles its own
  // `showing-<panel>` class off, and a multi-second broker fetch in between
  // would leave both panels' classes set simultaneously, so both views would
  // render on top of each other until the fetch resolved.
  function wrapSwitchPanel() {
    if (typeof window.switchPanel !== 'function') return;
    if (window.__humrPanelWrapped) return;
    window.__humrPanelWrapped = true;
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
    if (!window.HumrIntegrations) {
      console.error('[humr-integrations] runtime failed to load; not mounting.');
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
    if (!ensureSidebarTabAndPane()) {
      // Sidebar DOM not ready yet; retry on the next animation frame.
      // Happens on fresh page loads where the extension script runs before
      // the sidebar renders.
      requestAnimationFrame(init);
      return;
    }
    wrapSwitchPanel();

    const oauthSentinel = consumeOAuthSentinel();
    if (oauthSentinel && oauthSentinel.transition === 'error') {
      // The flow died after the consent redirect; nothing changed broker-side,
      // so a plain render + explanation is enough (no cache invalidate).
      if (typeof window.switchPanel === 'function') window.switchPanel('integrations');
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
        .then(() => { if (typeof window.switchPanel === 'function') window.switchPanel('integrations'); })
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
        // this is a no-op for now: the lookup finds the item in the just-
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

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
