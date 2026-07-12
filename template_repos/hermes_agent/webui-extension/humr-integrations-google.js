// HUMR Google integration: scope picker, card specialization, and OAuth error registrations.
(() => {
  'use strict';

  const { util, broker, oauthSentinel, flows, cardSpecs } = window.HumrIntegrations;
  const { elem } = util;
  const { buildTlsConnectUrl } = broker;
  const { registerOauthErrors } = oauthSentinel;
  const { createTlsCardSpec } = flows;

  // ── Google Workspace scope picker ─────────────────────────────────
  // The CP owns all scope semantics. The card renders the capability
  // projection it receives via integration.metadata.google_grants (per-product
  // off|read|write plus google_email) and submits the user's selection as a
  // `products` query param on the Connect URL — raw Google scopes are never
  // interpreted here. Expanding access goes straight to Google's consent;
  // reducing it makes the CP interpose its own confirmation page (revoking
  // is irreversible and project-global), so this modal needs no
  // narrow-vs-expand awareness.

  const GOOGLE_SCOPE_PRODUCTS = [
    { key: 'gmail', label: 'Gmail' },
    { key: 'calendar', label: 'Calendar' },
    { key: 'contacts', label: 'Contacts' },
    { key: 'drive', label: 'Drive' },
    { key: 'sheets', label: 'Sheets' },
    { key: 'docs', label: 'Docs' },
  ];
  const GOOGLE_SCOPE_LEVELS = [
    { key: 'off', label: 'Off' },
    { key: 'read', label: 'Read' },
    { key: 'write', label: 'Read & write' },
  ];
  const GOOGLE_LEVEL_RANK = { off: 0, read: 1, write: 2 };
  // Google's Drive scopes also authorize the Docs and Sheets content APIs,
  // so those two can never sit below Drive's level. The CP applies the same
  // closure server-side; the modal mirrors it so what you see is what you get.
  const GOOGLE_FLOORED_BY_DRIVE = ['docs', 'sheets'];

  function googleGrants(integration) {
    const grants = integration && integration.metadata && integration.metadata.google_grants;
    return (grants && typeof grants === 'object' && grants.products) ? grants : null;
  }

  function showGoogleScopeModal(integration, catalog, returnTo, revert) {
    const grants = googleGrants(integration);
    const isConnected = integration.status === 'connected';
    // Pre-check from what Google actually granted; a never-connected card
    // defaults to everything readable, nothing writable.
    const selection = {};
    for (const product of GOOGLE_SCOPE_PRODUCTS) {
      const level = grants ? grants.products[product.key] : null;
      selection[product.key] = (level === 'read' || level === 'write') ? level : (grants ? 'off' : 'read');
    }

    const backdrop = elem('div', { class: 'humr-modal-backdrop' });
    const cancel = () => { backdrop.remove(); if (revert) revert(); };

    const applyBtn = elem('button', {
      class: 'humr-integration-btn humr-integration-btn-primary',
      type: 'button',
    }, [isConnected ? 'Update access' : 'Connect']);
    const syncApply = () => {
      applyBtn.disabled = GOOGLE_SCOPE_PRODUCTS.every((p) => selection[p.key] === 'off');
    };

    // Mirror the CP's implied closure: bump docs/sheets up to drive's level
    // and disable their sub-drive buttons ("included with Drive access").
    const segByProduct = {};
    const syncSegs = () => {
      const driveRank = GOOGLE_LEVEL_RANK[selection.drive];
      for (const floored of GOOGLE_FLOORED_BY_DRIVE) {
        if (GOOGLE_LEVEL_RANK[selection[floored]] < driveRank) selection[floored] = selection.drive;
      }
      for (const product of GOOGLE_SCOPE_PRODUCTS) {
        const floor = GOOGLE_FLOORED_BY_DRIVE.includes(product.key) ? driveRank : 0;
        for (const btn of segByProduct[product.key].children) {
          btn.setAttribute('aria-pressed', String(btn.dataset.level === selection[product.key]));
          const belowFloor = GOOGLE_LEVEL_RANK[btn.dataset.level] < floor;
          btn.disabled = belowFloor;
          btn.title = belowFloor ? 'Included with Drive access' : '';
        }
      }
      syncApply();
    };

    const rows = [];
    for (const product of GOOGLE_SCOPE_PRODUCTS) {
      const seg = elem('div', { class: 'humr-scope-seg', role: 'radiogroup', 'aria-label': product.label });
      segByProduct[product.key] = seg;
      for (const level of GOOGLE_SCOPE_LEVELS) {
        seg.appendChild(elem('button', {
          type: 'button',
          class: 'humr-scope-seg-btn',
          dataset: { level: level.key },
          onclick: () => {
            selection[product.key] = level.key;
            syncSegs();
          },
        }, [level.label]));
      }
      rows.push(elem('div', { class: 'humr-scope-row' }, [
        elem('div', { class: 'humr-scope-row-label' }, [product.label]),
        seg,
      ]));
    }
    syncSegs();

    applyBtn.addEventListener('click', () => {
      const productsParam = GOOGLE_SCOPE_PRODUCTS
        .filter((p) => selection[p.key] !== 'off')
        .map((p) => p.key + ':' + selection[p.key])
        .join(',');
      window.location.href = buildTlsConnectUrl(catalog, integration.slug, returnTo) +
        '&products=' + encodeURIComponent(productsParam);
    });

    const bodyLines = [
      'Pick what this agent may access. Google will show a consent screen for your selection.',
    ];
    const modal = elem('div', { class: 'humr-modal humr-scope-modal' }, [
      elem('div', { class: 'humr-modal-title' }, [(isConnected ? 'Configure ' : 'Connect ') + (integration.label || integration.slug)]),
      elem('div', { class: 'humr-modal-body' }, [
        bodyLines.join(' '),
        (grants && grants.google_email)
          ? elem('div', { class: 'humr-scope-account' }, ['Connected as ' + grants.google_email])
          : null,
      ]),
      elem('div', { class: 'humr-scope-rows' }, rows),
      elem('div', { class: 'humr-modal-actions' }, [
        elem('button', { class: 'humr-integration-btn', type: 'button', onclick: cancel }, ['Cancel']),
        applyBtn,
      ]),
    ]);
    backdrop.appendChild(modal);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) cancel(); });
    document.body.appendChild(backdrop);
  }

  cardSpecs.register({ kind: 'tls_intercept', slug: 'google' }, (integration, page) => {
    const catalog = page.catalog;
    return createTlsCardSpec(integration, page, {
      connect(revert) {
        showGoogleScopeModal(integration, catalog, page.returnTo, revert);
      },
      configure() {
        showGoogleScopeModal(integration, catalog, page.returnTo);
      },
    });
  });

  registerOauthErrors({
    google_denied: 'Google connection was cancelled — nothing was changed.',
    google_exchange_failed: 'Google sign-in could not be completed. Try connecting again.',
    google_no_refresh_token: 'Google did not issue new credentials. Remove Humanity Rules under ' +
      'myaccount.google.com › Security › Third-party access, then reconnect.',
    google_narrow_incomplete: 'Your previous Google access was revoked, but the new connection was ' +
      'not completed — the agent is now disconnected from Google. Connect again to restore access.',
    google_stale_flow: 'This Google connection attempt was superseded by a newer change to the ' +
      'connection — nothing was stored. Check the card and connect again if needed.',
  });
  window.HumrIntegrations.loadedExtensionScripts.add('google');
})();
