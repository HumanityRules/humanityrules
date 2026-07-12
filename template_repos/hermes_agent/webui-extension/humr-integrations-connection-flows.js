// HUMR integration connection flows: provider-neutral vault, device, and
// disconnect machinery plus the default TLS and direct-MCP card specifications.
//
// TLS-intercept Connect (Google, GitHub, …) is a top-level navigation to HUMR's
// control plane; Disconnect goes through the broker so the Integrations pane
// stays open. Direct MCP connectors flow entirely through the broker.
(() => {
  'use strict';

  const { page, util, broker, webui, flows, cardActions, cardSpecs } = window.HumrIntegrations;
  const { elem, formatDate } = util;
  const { tlsInterceptPath, mcpPath, buildTlsConnectUrl, buildMcpConnectUrl, invalidateTlsCache } = broker;
  const { refreshModelDropdownsIfProviderAffectsPicker } = webui;

  const VAULT_NETWORK_ERROR = (
    'Could not reach the Humanity Rules vault. Try again. ' +
    'If this keeps happening, ask an admin to check this Hermes deployment.'
  );

  async function throwForErrorResponse(response, fallbackMessage) {
    if (response.ok) return;
    let message = fallbackMessage;
    try { message = (await response.json()).error || message; } catch (_) { /* ignore */ }
    throw new Error(message);
  }

  function fieldInputFor(field) {
    const id = 'humrVaultField_' + field.name;
    const wrapper = elem('label', { class: 'humr-vault-field', for: id });
    wrapper.appendChild(elem('span', { class: 'humr-vault-field-label' }, [
      field.label + (field.required ? ' *' : ''),
    ]));
    let input;
    if (field.kind === 'textarea') {
      input = elem('textarea', {
        id,
        class: 'humr-vault-input humr-vault-textarea',
        name: field.name,
        rows: '4',
      });
      input.value = field.value || '';
    } else {
      input = elem('input', {
        id,
        class: 'humr-vault-input',
        name: field.name,
        type: 'text',
        placeholder: field.placeholder || '',
        autocomplete: 'off',
      });
    }
    input.dataset.kind = field.kind || 'text';
    wrapper.appendChild(input);
    if (field.help) wrapper.appendChild(elem('span', { class: 'humr-vault-field-help' }, [field.help]));
    return wrapper;
  }

  async function requestVaultSetupSession(integration) {
    const url = tlsInterceptPath(integration.slug, 'setup-session') +
      '?origin=' + encodeURIComponent(window.location.origin);
    let response;
    try {
      response = await fetch(url, { method: 'POST', cache: 'no-store' });
    } catch (_) {
      throw new Error(VAULT_NETWORK_ERROR);
    }
    if (!response.ok) {
      let message = 'Could not start the vault setup flow.';
      try { message = (await response.json()).error || message; } catch (_) { /* ignore */ }
      throw new Error(message);
    }
    return await response.json();
  }

  async function submitVaultForm(session, form) {
    const credentials = {};
    const config = {};
    for (const input of form.querySelectorAll('[name]')) {
      const value = input.value || '';
      if (input.dataset.kind === 'secret') {
        if (value.trim()) credentials[input.name] = value;
      } else {
        config[input.name] = value;
      }
    }
    let response;
    try {
      response = await fetch(session.action_url, {
        method: 'POST',
        credentials: 'omit',
        headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify({
          submit_token: session.submit_token,
          credentials,
          config,
        }),
      });
    } catch (_) {
      throw new Error(VAULT_NETWORK_ERROR);
    }
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* ignore */ }
    if (!response.ok) throw new Error(payload.error || 'Vault submission failed.');
    return payload;
  }

  // Post-save/connect sequence shared by the vault form modals and the
  // link+poll modal: the credential is already stored on HUMR, so invalidate
  // the broker cache (which rewrites the gateway env and restarts the
  // gateway), swap the action row to a Close button, and refresh the cards.
  // `verb` is 'Saved' or 'Connected' depending on how the credential landed.
  async function applyVaultCredentials({ integration, statusEl, actions, close, verb }) {
    statusEl.textContent = verb + '. Applying credentials…';
    statusEl.style.display = '';
    let restarted = true;
    try {
      await invalidateTlsCache(integration.slug);
    } catch (err) {
      restarted = false;
      statusEl.textContent =
        err.message ||
        verb + ', but applying the credentials failed. Redeploy this Hermes app to apply them.';
    }
    if (integration.affects_model_picker) {
      statusEl.textContent = verb + '. Updating the model list…';
      await refreshModelDropdownsIfProviderAffectsPicker(integration);
    }
    actions.replaceChildren(elem('button', {
      class: 'humr-integration-btn humr-integration-btn-primary',
      type: 'button',
      onclick: close,
    }, ['Close']));
    try {
      await page.refreshAndRender();
    } catch (_) { /* sidebar refresh can recover on next open */ }
    if (restarted) {
      statusEl.textContent = verb + '. The new credentials are active.';
    }
  }

  // Wire a vault form's submit: POST to HUMR, then run the shared apply
  // sequence. Shared by the generic and Slack renderers so the
  // save/restart/refresh flow is single-sourced.
  function wireVaultSubmit(opts) {
    const { form, session, integration, saveBtn, actions, errorBox, successBox, close } = opts;
    let resolveChanged;
    const changed = new Promise((resolve) => { resolveChanged = resolve; });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      let saved = false;
      errorBox.style.display = 'none';
      successBox.style.display = 'none';
      saveBtn.disabled = true;
      saveBtn.textContent = 'Saving…';
      try {
        await submitVaultForm(session, form);
        saved = true;
        await applyVaultCredentials({ integration, statusEl: successBox, actions, close, verb: 'Saved' });
        resolveChanged();
      } catch (err) {
        errorBox.textContent = err.message || 'Save failed.';
        errorBox.style.display = '';
      } finally {
        if (!saved) {
          saveBtn.disabled = false;
          saveBtn.textContent = 'Save';
        }
      }
    });
    return changed;
  }

  function showGenericVaultConfigModal(integration, session) {
    if (session.schema && session.schema.mode === 'link_poll') {
      return showLinkPollConnectModal(integration, session);
    }
    const connectOutcome = cardActions.createConnectOutcome();
    const schema = session.schema;
    const backdrop = elem('div', { class: 'humr-modal-backdrop humr-vault-backdrop' });
    const close = () => { backdrop.remove(); connectOutcome.finish('cancelled'); };
    const errorBox = elem('div', { class: 'humr-vault-error', style: { display: 'none' } });
    const successBox = elem('div', { class: 'humr-vault-success', style: { display: 'none' } });
    const form = elem('form', { class: 'humr-vault-form' });
    for (const field of schema.fields || []) {
      form.appendChild(fieldInputFor(field));
    }
    const saveBtn = elem('button', {
      class: 'humr-integration-btn humr-integration-btn-primary',
      type: 'submit',
    }, ['Save']);
    const actions = elem('div', { class: 'humr-modal-actions' }, [
      elem('button', {
        class: 'humr-integration-btn',
        type: 'button',
        onclick: close,
      }, ['Cancel']),
      saveBtn,
    ]);
    form.appendChild(actions);
    wireVaultSubmit({ form, session, integration, saveBtn, actions, errorBox, successBox, close })
      .then(() => connectOutcome.finish('changed'));
    const modal = elem('div', { class: 'humr-modal humr-vault-modal' }, [
      elem('div', { class: 'humr-modal-title' }, [(schema.status === 'connected' ? 'Configure ' : 'Connect ') + (schema.label || integration.label)]),
      elem('div', { class: 'humr-modal-body' }, [schema.message || 'Credentials are sent directly to the Humanity Rules vault.']),
      errorBox,
      successBox,
      form,
    ]);
    backdrop.appendChild(modal);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
    document.body.appendChild(backdrop);
    return connectOutcome.promise;
  }

  // ── Link+poll vault flow ──────────────────────────────────────────
  // Vault providers whose credential is created in an external app (e.g.
  // Telegram managed bots) return `schema.mode === 'link_poll'`: the modal
  // shows a QR code the user scans with their phone, and we poll HUMR's
  // setup-poll endpoint with the session token until the provider reports
  // the credential as connected. The HUMR side then already holds the
  // secret — nothing is typed or pasted here, and no desktop app is needed.

  // 1s keeps detection feeling instant after the user confirms in Telegram;
  // each poll is one getUpdates on the HUMR side, and sessions cap at 30 min.
  const LINK_POLL_MS = 1000;

  function showLinkPollConnectModal(integration, session) {
    const connectOutcome = cardActions.createConnectOutcome();
    const schema = session.schema;
    const backdrop = elem('div', { class: 'humr-modal-backdrop humr-vault-backdrop' });
    let stopped = false;
    const close = () => { stopped = true; backdrop.remove(); connectOutcome.finish('cancelled'); };

    const errorBox = elem('div', { class: 'humr-vault-error', style: { display: 'none' } });
    const statusBox = elem('div', { class: 'humr-vault-success' }, [schema.pending_message || 'Waiting for confirmation…']);
    const cancelBtn = elem('button', { class: 'humr-integration-btn', type: 'button', onclick: close }, ['Cancel']);
    const actions = elem('div', { class: 'humr-modal-actions' }, [cancelBtn]);

    const children = [
      elem('div', { class: 'humr-modal-title' }, ['Connect ' + (schema.label || integration.label)]),
      elem('div', { class: 'humr-modal-body' }, [schema.message || '']),
    ];
    if (schema.qr_data_uri) {
      children.push(elem('div', { class: 'humr-vault-qr-wrap' }, [
        elem('img', { class: 'humr-vault-qr', src: schema.qr_data_uri, alt: 'QR code', decoding: 'async' }),
        elem('div', { class: 'humr-vault-qr-caption' }, [schema.qr_caption || 'Scan with your phone']),
      ]));
      if (schema.link_note) {
        children.push(elem('div', { class: 'humr-vault-link-note' }, [schema.link_note]));
      }
      children.push(statusBox);
    } else {
      // The control plane reported the flow as unavailable (schema.message
      // says why); there is nothing to open or poll.
      stopped = true;
      cancelBtn.textContent = 'Close';
    }
    children.push(errorBox, actions);
    backdrop.appendChild(elem('div', { class: 'humr-modal humr-vault-modal' }, children));
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
    document.body.appendChild(backdrop);

    const fail = (message) => {
      statusBox.style.display = 'none';
      errorBox.textContent = message || 'Connect failed. Please try again.';
      errorBox.style.display = '';
      cancelBtn.textContent = 'Close';
    };

    const poll = async () => {
      if (stopped) return;
      let response;
      let payload = {};
      try {
        response = await fetch(session.poll_url, {
          method: 'POST',
          credentials: 'omit',
          headers: { 'Content-Type': 'text/plain' },
          body: JSON.stringify({ submit_token: session.submit_token }),
        });
        try { payload = await response.json(); } catch (_) { /* ignore */ }
      } catch (_) {
        setTimeout(poll, LINK_POLL_MS); // network blip: keep polling
        return;
      }
      if (stopped) return;
      if (response.ok && payload.status === 'connected') {
        stopped = true;
        await applyVaultCredentials({ integration, statusEl: statusBox, actions, close, verb: 'Connected' });
        connectOutcome.finish('changed');
        return;
      }
      if (response.ok) {
        setTimeout(poll, LINK_POLL_MS);
        return;
      }
      stopped = true;
      fail(payload.error); // expired session or provider error: terminal
    };
    if (!stopped) setTimeout(poll, LINK_POLL_MS);
    return connectOutcome.promise;
  }

  // ── OAuth device-login flow ───────────────────────────────────────
  // Device providers can't use our redirect callback, so the broker runs the
  // provider's device flow. The browser only starts the session, displays the
  // code + verification URL, and polls for terminal state.

  const DEVICE_POLL_MS = 3000;

  async function startDeviceConnect(integration) {
    let session;
    try {
      const base = tlsInterceptPath(integration.slug, 'device');
      const resp = await fetch(base + '/start', { method: 'POST', cache: 'no-store' });
      session = await resp.json();
      if (!resp.ok || !session.ok) throw new Error(session.error || 'Could not start the login.');
    } catch (err) {
      alert(err.message || 'Could not start the login.');
      return { outcome: 'cancelled' };
    }
    return showDeviceModal(integration, session);
  }

  function showDeviceModal(integration, session) {
    const connectOutcome = cardActions.createConnectOutcome();
    const backdrop = elem('div', { class: 'humr-modal-backdrop' });
    let cancelled = false;
    const base = tlsInterceptPath(integration.slug, 'device');
    const close = () => {
      cancelled = true;
      backdrop.remove();
      // Best-effort: tell the broker to drop the in-flight session.
      fetch(base + '/cancel', { method: 'POST' }).catch(() => {});
      connectOutcome.finish('cancelled');
    };

    const codeEl = elem('div', { class: 'humr-device-code' }, [session.user_code || '—']);
    const link = elem('a', {
      class: 'humr-device-oauth-link',
      href: session.verification_url,
      target: '_blank',
      rel: 'noopener',
    }, [session.verification_url]);
    const statusBox = elem('div', { class: 'humr-vault-success' }, ['Waiting for you to approve in your browser…']);
    const errorBox = elem('div', { class: 'humr-vault-error', style: { display: 'none' } });
    const cancelBtn = elem('button', { class: 'humr-integration-btn', type: 'button', onclick: close }, ['Cancel']);

    const modal = elem('div', { class: 'humr-modal' }, [
      elem('div', { class: 'humr-modal-title' }, ['Connect ' + integration.label]),
      elem('div', { class: 'humr-modal-body humr-device-step-label' }, [
        'Open the link below and sign in:',
      ]),
      elem('div', { class: 'humr-modal-body humr-device-step-content' }, [link]),
      elem('div', { class: 'humr-modal-body humr-device-step-label' }, [
        'After opening the link and signing in, enter this code:',
      ]),
      codeEl,
      statusBox,
      errorBox,
      elem('div', { class: 'humr-modal-actions' }, [cancelBtn]),
    ]);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    // Poll the broker for terminal state.
    const poll = async () => {
      if (cancelled) return;
      let st;
      try {
        const resp = await fetch(base + '/status', { cache: 'no-store' });
        st = await resp.json();
      } catch (_) {
        setTimeout(poll, DEVICE_POLL_MS);
        return;
      }
      if (cancelled) return;
      if (st.phase === 'completed') {
        backdrop.remove();
        try {
          await page.refreshAndRender();
          await refreshModelDropdownsIfProviderAffectsPicker(integration);
        } finally {
          connectOutcome.finish('changed');
        }
        return;
      }
      if (st.phase === 'failed' || st.phase === null) {
        statusBox.style.display = 'none';
        errorBox.textContent = st.error || 'Login failed. Please try again.';
        errorBox.style.display = '';
        cancelBtn.textContent = 'Close';
        return;
      }
      setTimeout(poll, DEVICE_POLL_MS);
    };
    setTimeout(poll, DEVICE_POLL_MS);
    return connectOutcome.promise;
  }

  // Open the shared vault setup plumbing with either the generic schema-driven
  // modal or a card specification's custom modal renderer.
  async function startVaultConfig(integration, modalRenderer) {
    try {
      const session = await requestVaultSetupSession(integration);
      const renderer = modalRenderer || showGenericVaultConfigModal;
      return await renderer(integration, session);
    } catch (err) {
      alert(err.message || 'Could not open the vault dialog.');
      return { outcome: 'cancelled' };
    }
  }

  async function disconnectTlsProvider(integration) {
    const response = await fetch(tlsInterceptPath(integration.slug, 'disconnect'), {
      method: 'POST',
      cache: 'no-store',
    });
    await throwForErrorResponse(response, 'Disconnect failed. Please try again.');
  }

  function tlsCardDetails(integration) {
    const details = [];
    const ownerName = integration.metadata && integration.metadata.owner_name;
    if (ownerName) details.push({ text: 'Replies only to ' + ownerName });
    if (integration.last_refreshed_at) {
      const refreshed = 'Last refreshed: ' + formatDate(integration.last_refreshed_at);
      details.push({
        className: 'humr-integration-meta humr-integration-meta-refresh',
        text: refreshed,
        title: refreshed,
        hideWhenShared: true,
      });
    }
    return details;
  }

  function createTlsCardSpec(integration, customization) {
    const custom = customization || {};
    const usesVault = integration.connect_mode === 'vault';
    const usesDevice = integration.connect_mode === 'device';
    const oauthConnectUrl = (!usesVault && !usesDevice)
      ? buildTlsConnectUrl(integration.slug)
      : null;
    const openVault = () => startVaultConfig(integration, custom.vaultRenderer);
    const defaultConnect = () => {
      if (usesVault) return openVault();
      if (usesDevice) return startDeviceConnect(integration);
      window.location.href = oauthConnectUrl;
      return { outcome: 'navigating' };
    };
    const configure = Object.prototype.hasOwnProperty.call(custom, 'configure')
      ? custom.configure
      : (usesVault ? () => openVault() : null);
    return {
      details: tlsCardDetails(integration),
      connect: custom.connect || defaultConnect,
      configure,
      async disconnect() {
        await disconnectTlsProvider(integration);
      },
    };
  }

  function createMcpCardSpec(integration) {
    return {
      details: [],
      connect() {
        window.location.href = buildMcpConnectUrl(integration.slug);
        return { outcome: 'navigating' };
      },
      configure: null,
      async disconnect() {
        const response = await fetch(mcpPath(integration.slug, 'disconnect'), { method: 'POST' });
        await throwForErrorResponse(response, 'Disconnect failed. Please try again.');
      },
    };
  }

  cardSpecs.register({ kind: 'tls_intercept' }, createTlsCardSpec);
  cardSpecs.register({ kind: 'mcp_aggregator' }, createMcpCardSpec);

  Object.assign(flows, {
    throwForErrorResponse,
    createTlsCardSpec,
    fieldInputFor,
    wireVaultSubmit,
  });
  window.HumrIntegrations.loadedExtensionScripts.add('connection-flows');
})();
