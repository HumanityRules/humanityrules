// HUMR integration connection flows: provider-neutral vault, device, and disconnect machinery plus connector adapters.
(() => {
  'use strict';

  const { state, util, broker, webui, flows, connectors } = window.HumrIntegrations;
  const { elem } = util;
  const { tlsInterceptPath, mcpPath, buildMcpConnectUrl, invalidateTlsCache } = broker;
  const { refreshModelDropdownsIfProviderAffectsPicker } = webui;

  const VAULT_NETWORK_ERROR = (
    'Could not reach the Humanity Rules vault. Try again. ' +
    'If this keeps happening, ask an admin to check this Hermes deployment.'
  );

  // Put a Connect button into its in-flight "Connecting…" state and return a
  // revert function. For navigation flows (TLS-OAuth, MCP) the page leaves
  // before revert is ever called, so the label simply persists. For modal
  // flows (vault, Merge) the caller reverts when the user cancels or it errors.
  function markConnecting(btn) {
    if (!btn) return () => {};
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Connecting…';
    return () => {
      btn.disabled = false;
      btn.textContent = original;
    };
  }

  // Run a disconnect with the shared guard/spinner/cleanup dance: no-op if one
  // is already in flight for `key`; otherwise mark pending and re-render (so the
  // button shows "Disconnecting…"), run `perform`, then always clear and
  // re-render. `perform` owns the fetch and any post-disconnect refresh.
  async function runDisconnect(key, ctx, perform) {
    if (state.disconnecting.has(key)) return;
    state.disconnecting.add(key);
    ctx.rerender();
    try {
      await perform();
    } finally {
      state.disconnecting.delete(key);
      ctx.rerender();
    }
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

  async function requestVaultSetupSession(item) {
    const url = tlsInterceptPath(item.slug, 'setup-session') +
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
  async function applyVaultCredentials({ item, statusEl, actions, close, verb }, ctx) {
    statusEl.textContent = verb + '. Applying credentials…';
    statusEl.style.display = '';
    let restarted = true;
    try {
      await invalidateTlsCache(item.slug);
    } catch (err) {
      restarted = false;
      statusEl.textContent =
        err.message ||
        verb + ', but applying the credentials failed. Redeploy this Hermes app to apply them.';
    }
    if (item.affects_model_picker) {
      statusEl.textContent = verb + '. Updating the model list…';
      await refreshModelDropdownsIfProviderAffectsPicker(item);
    }
    actions.replaceChildren(elem('button', {
      class: 'humr-integration-btn humr-integration-btn-primary',
      type: 'button',
      onclick: close,
    }, ['Close']));
    try {
      await ctx.refreshAndRender();
    } catch (_) { /* sidebar refresh can recover on next open */ }
    if (restarted) {
      statusEl.textContent = verb + '. The new credentials are active.';
    }
  }

  // Wire a vault form's submit: POST to HUMR, then run the shared apply
  // sequence. Shared by the generic and Slack renderers so the
  // save/restart/refresh flow is single-sourced.
  function wireVaultSubmit(opts, ctx) {
    const { form, session, item, saveBtn, actions, errorBox, successBox, close } = opts;
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
        await applyVaultCredentials({ item, statusEl: successBox, actions, close, verb: 'Saved' }, ctx);
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
  }

  function showGenericVaultConfigModal(item, session, ctx, onClose) {
    if (session.schema && session.schema.mode === 'link_poll') {
      showLinkPollConnectModal(item, session, ctx, onClose);
      return;
    }
    const schema = session.schema;
    const backdrop = elem('div', { class: 'humr-modal-backdrop humr-vault-backdrop' });
    const close = () => { backdrop.remove(); if (onClose) onClose(); };
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
    wireVaultSubmit({ form, session, item, saveBtn, actions, errorBox, successBox, close }, ctx);
    const modal = elem('div', { class: 'humr-modal humr-vault-modal' }, [
      elem('div', { class: 'humr-modal-title' }, [(schema.status === 'connected' ? 'Configure ' : 'Connect ') + (schema.label || item.label)]),
      elem('div', { class: 'humr-modal-body' }, [schema.message || 'Credentials are sent directly to the Humanity Rules vault.']),
      errorBox,
      successBox,
      form,
    ]);
    backdrop.appendChild(modal);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
    document.body.appendChild(backdrop);
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

  function showLinkPollConnectModal(item, session, ctx, onClose) {
    const schema = session.schema;
    const backdrop = elem('div', { class: 'humr-modal-backdrop humr-vault-backdrop' });
    let stopped = false;
    const close = () => { stopped = true; backdrop.remove(); if (onClose) onClose(); };

    const errorBox = elem('div', { class: 'humr-vault-error', style: { display: 'none' } });
    const statusBox = elem('div', { class: 'humr-vault-success' }, [schema.pending_message || 'Waiting for confirmation…']);
    const cancelBtn = elem('button', { class: 'humr-integration-btn', type: 'button', onclick: close }, ['Cancel']);
    const actions = elem('div', { class: 'humr-modal-actions' }, [cancelBtn]);

    const children = [
      elem('div', { class: 'humr-modal-title' }, ['Connect ' + (schema.label || item.label)]),
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
        await applyVaultCredentials({ item, statusEl: statusBox, actions, close, verb: 'Connected' }, ctx);
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
  }

  // ── OAuth device-login flow ───────────────────────────────────────
  // Device providers can't use our redirect callback, so the broker runs the
  // provider's device flow. The browser only starts the session, displays the
  // code + verification URL, and polls for terminal state.

  const DEVICE_POLL_MS = 3000;

  async function startDeviceConnect(item, ctx, revert) {
    const revertOnce = () => { if (revert) { revert(); revert = null; } };
    let session;
    try {
      const base = tlsInterceptPath(item.slug, 'device');
      const resp = await fetch(base + '/start', { method: 'POST', cache: 'no-store' });
      session = await resp.json();
      if (!resp.ok || !session.ok) throw new Error(session.error || 'Could not start the login.');
    } catch (err) {
      alert(err.message || 'Could not start the login.');
      revertOnce();
      return;
    }
    showDeviceModal(item, session, ctx, revertOnce);
  }

  function showDeviceModal(item, session, ctx, onClose) {
    const backdrop = elem('div', { class: 'humr-modal-backdrop' });
    let cancelled = false;
    const base = tlsInterceptPath(item.slug, 'device');
    const close = () => {
      cancelled = true;
      backdrop.remove();
      // Best-effort: tell the broker to drop the in-flight session.
      fetch(base + '/cancel', { method: 'POST' }).catch(() => {});
      if (onClose) onClose();
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
      elem('div', { class: 'humr-modal-title' }, ['Connect ' + item.label]),
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
        await ctx.refreshAndRender();
        await refreshModelDropdownsIfProviderAffectsPicker(item);
        if (onClose) onClose();
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
  }

  // Per-provider config-modal renderers. The generic `showGenericVaultConfigModal`
  // renders any flat `schema.fields` form, and dispatches `mode: 'link_poll'`
  // schemas (Telegram managed bots) to the link+poll modal. Providers
  // whose setup needs more than a flat form (e.g. Slack's mode selector +
  // manifest prefill link + two tokens) register a custom renderer with
  // registerVaultRenderer(), keyed by slug; everything else falls back to the
  // generic one. All renderers share the same setup-session/submit/restart
  // plumbing.

  const _vaultRenderers = new Map();

  function registerVaultRenderer(slug, renderer) {
    if (_vaultRenderers.has(slug)) console.warn('[humr-integrations] Replacing vault renderer for ' + slug + '.');
    _vaultRenderers.set(slug, renderer);
  }

  function vaultRendererFor(slug) {
    return _vaultRenderers.get(slug);
  }

  async function startVaultConfig(item, ctx, revert) {
    // `revert` (from markConnecting) restores the Connect button. Fire it if we
    // never open the modal (error), or when the user dismisses it without
    // connecting; a successful save re-renders the card from scratch so the
    // button is replaced regardless. revert may be omitted (e.g. the Configure
    // button on an already-connected provider reuses this path).
    const revertOnce = () => { if (revert) { revert(); revert = null; } };
    try {
      const session = await requestVaultSetupSession(item);
      const renderer = vaultRendererFor(item.slug) || showGenericVaultConfigModal;
      renderer(item, session, ctx, revertOnce);
    } catch (err) {
      alert(err.message || 'Could not open the vault dialog.');
      revertOnce();
    }
  }

  // One disconnect path for every TLS-intercept provider (vault + OAuth).
  // The broker resolves the provider kind server-side, so both kinds POST
  // here identically.
  function disconnectTlsProvider(item, ctx) {
    return runDisconnect(item.slug, ctx, async () => {
      const response = await fetch(tlsInterceptPath(item.slug, 'disconnect'), {
        method: 'POST',
        cache: 'no-store',
      });
      if (!response.ok) {
        let message = 'Disconnect failed. Please try again.';
        try { message = (await response.json()).error || message; } catch (_) { /* ignore */ }
        alert(message);
        return;
      }
      await ctx.refreshAndRender();
      await refreshModelDropdownsIfProviderAffectsPicker(item);
    });
  }

  connectors.register('mcp_aggregator', {
    cardKey(item) {
      return 'mcp:' + item.slug;
    },
    connect(item, ctx, revert) {
      window.location.href = buildMcpConnectUrl(item.slug);
    },
    async disconnect(item, ctx) {
      await fetch(mcpPath(item.slug, 'disconnect'), { method: 'POST' });
      await ctx.refreshAndRender();
    },
  });

  Object.assign(flows, {
    markConnecting,
    runDisconnect,
    startVaultConfig,
    registerVaultRenderer,
    startDeviceConnect,
    disconnectTlsProvider,
    fieldInputFor,
    wireVaultSubmit,
  });
  window.HumrIntegrations.loadedExtensionScripts.add('connection-flows');
})();
