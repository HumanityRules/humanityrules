// HUMR Slack integration: custom vault configuration modal and renderer registration.
(() => {
  'use strict';

  const { util, flows, cardSpecs } = window.HumrIntegrations;
  const { elem } = util;
  const { createTlsCardSpec, fieldInputFor, wireVaultSubmit } = flows;

  // Slack manifest name rules (https://docs.slack.dev/reference/app-manifest):
  // display_information.name is <=35 chars (any character); bot_user.display_name
  // is <=80 chars restricted to [a-z0-9._-]. Mirrors provider_slack.py so the
  // live prefill URL matches what the server bakes/persists.
  const SLACK_APP_NAME_MAX_LEN = 35;
  const SLACK_BOT_NAME_MAX_LEN = 80;
  function slackCleanAppName(raw) {
    return (raw || '').trim().slice(0, SLACK_APP_NAME_MAX_LEN).trim() || 'Slackbot';
  }
  function slackCleanBotName(raw) {
    const name = (raw || '').trim().toLowerCase()
      .replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '')
      .slice(0, SLACK_BOT_NAME_MAX_LEN).replace(/^-+|-+$/g, '');
    return name || 'slackbot';
  }

  // Build the Slack "Create app" prefill URL for one mode by embedding that
  // mode's manifest, re-baking the operator's chosen app name into both name
  // fields. The operator clicks it, Slack opens Create-New-App with everything
  // pre-filled (incl. Socket Mode), then generates the app-level token and
  // installs to get the bot token.
  function slackCreateAppUrl(schema, mode, appName) {
    const base = (schema.manifests || {})[mode];
    if (!base) return null;
    const manifest = JSON.parse(JSON.stringify(base));
    if (manifest.display_information) manifest.display_information.name = slackCleanAppName(appName);
    if (manifest.features && manifest.features.bot_user) manifest.features.bot_user.display_name = slackCleanBotName(appName);
    return 'https://api.slack.com/apps?new_app=1&manifest_json=' +
      encodeURIComponent(JSON.stringify(manifest));
  }

  function showSlackConfigModal(integration, session, page, onClose) {
    const schema = session.schema;
    const backdrop = elem('div', { class: 'humr-modal-backdrop humr-vault-backdrop' });
    const close = () => { backdrop.remove(); if (onClose) onClose(); };
    const errorBox = elem('div', { class: 'humr-vault-error', style: { display: 'none' } });
    const successBox = elem('div', { class: 'humr-vault-success', style: { display: 'none' } });
    const form = elem('form', { class: 'humr-vault-form' });

    // Never preselect a disabled mode: a stale credential could carry a mode
    // since turned off (the backend would then reject Save). Clamp to the
    // server's selected_mode only if it's enabled, else the first enabled mode.
    const enabledModes = (schema.modes || []).filter((m) => m.enabled);
    const preferred = schema.selected_mode || 'company_wide';
    let selectedMode =
      enabledModes.some((m) => m.value === preferred) ? preferred
      : (enabledModes[0] ? enabledModes[0].value : preferred);
    // Hidden input carries the chosen mode to the backend as config.workspace_scope
    // (submitVaultForm routes non-secret named inputs into `config`).
    const modeInput = elem('input', { type: 'hidden', name: 'workspace_scope' });
    modeInput.value = selectedMode;

    // Editable app name (named so submitVaultForm routes it into `config`).
    // Defaults to the deploying app's template name; drives both Slack name
    // fields in the prefill manifest, so editing it rebuilds the link.
    const nameInput = elem('input', {
      class: 'humr-vault-input',
      name: 'app_name',
      type: 'text',
      maxlength: String(schema.app_name_max_len || SLACK_APP_NAME_MAX_LEN),
      autocomplete: 'off',
    });
    nameInput.value = schema.app_name || '';

    // Owner email (personal mode only). Named so submitVaultForm routes it into
    // `config`; the backend resolves it to a Slack user_id at save and never
    // persists the address. Prefilled with the deploying user's HUMR email.
    const ownerEmailInput = elem('input', {
      class: 'humr-vault-input',
      name: 'owner_email',
      type: 'email',
      placeholder: 'you@company.com',
      autocomplete: 'off',
    });
    ownerEmailInput.value = schema.owner_email || '';
    // On reconfigure an owner is already bound (server blanks owner_email and
    // sends owner_name); a blank email keeps that owner. Say so, or the empty
    // field reads as "no owner set".
    const ownerHelp = schema.owner_name
      ? 'Currently replies to ' + schema.owner_name + '. Leave blank to keep them, or enter a different Slack email to change.'
      : 'The bot will reply only to this person. Use the email tied to your Slack account.';
    const ownerEmailField = elem('label', { class: 'humr-vault-field' }, [
      elem('span', { class: 'humr-vault-field-label' }, ['Your Slack email']),
      ownerEmailInput,
      elem('span', { class: 'humr-vault-field-help' }, [ownerHelp]),
    ]);
    // Only personal mode collects an owner; show/hide as the mode changes.
    const syncOwnerEmail = () => {
      ownerEmailField.style.display = selectedMode === 'personal' ? '' : 'none';
    };

    // Home channel (company-wide only). Optional C… id the bot is invited to;
    // where the gateway delivers cron/proactive output. Personal mode resolves
    // the owner's DM automatically, so this field is hidden there.
    const homeChannelInput = elem('input', {
      class: 'humr-vault-input',
      name: 'home_channel',
      type: 'text',
      placeholder: 'C0123456789',
      autocomplete: 'off',
    });
    homeChannelInput.value = schema.home_channel || '';
    const homeChannelField = elem('label', { class: 'humr-vault-field' }, [
      elem('span', { class: 'humr-vault-field-label' }, ['Home channel (optional)']),
      homeChannelInput,
      elem('span', { class: 'humr-vault-field-help' }, [
        'Channel ID where cron results and proactive messages are posted. Invite the bot to that channel first. Leave blank to set it later with !sethome in the channel.',
      ]),
    ]);
    const syncHomeChannel = () => {
      homeChannelField.style.display = selectedMode === 'company_wide' ? '' : 'none';
    };

    // The prefill link is rebuilt whenever the mode or app name changes — each
    // mode embeds a different manifest (scopes + subscriptions), and the name
    // is re-baked into both manifest name fields.
    const createLink = elem('a', {
      class: 'humr-integration-btn humr-integration-btn-primary humr-slack-create-link',
      target: '_blank',
      rel: 'noopener noreferrer',
    }, ['Create Slack app ↗']);
    const syncCreateLink = () => {
      const url = slackCreateAppUrl(schema, selectedMode, nameInput.value);
      if (url) { createLink.href = url; createLink.style.display = ''; }
      else { createLink.removeAttribute('href'); createLink.style.display = 'none'; }
    };
    nameInput.addEventListener('input', syncCreateLink);

    // Radios deliberately carry NO `name` attribute: submitVaultForm scrapes
    // every `[name]` input into credentials/config, so a named radio would
    // leak a bogus field. The chosen value flows only through `modeInput`.
    // Mutual exclusion is done in JS by unchecking siblings on change.
    const modeChoices = elem('div', { class: 'humr-slack-modes' });
    const radios = [];
    for (const mode of schema.modes || []) {
      const radio = elem('input', { type: 'radio', value: mode.value });
      radio.checked = mode.value === selectedMode;
      if (!mode.enabled) radio.disabled = true;
      radio.addEventListener('change', () => {
        if (!radio.checked) return;
        for (const other of radios) { if (other !== radio) other.checked = false; }
        selectedMode = mode.value;
        modeInput.value = selectedMode;
        syncCreateLink();
        syncOwnerEmail();
        syncHomeChannel();
      });
      radios.push(radio);
      const labelText = mode.label + (mode.enabled ? '' : ' (coming soon)');
      modeChoices.appendChild(elem('label', { class: 'humr-slack-mode' }, [radio, elem('span', null, [labelText])]));
    }

    const steps = elem('ol', { class: 'humr-slack-steps' }, [
      elem('li', null, ['Click ', elem('b', null, ['Create Slack app']), ' and create it in your workspace.']),
      elem('li', null, ['On the app page, generate an ', elem('b', null, ['App-level token']), ' (scope connections:write).']),
      elem('li', null, [elem('b', null, ['Install']), ' the app to your workspace to get the ', elem('b', null, ['Bot token']), '.']),
      elem('li', null, ['Paste both tokens below.']),
    ]);

    form.appendChild(modeInput);
    form.appendChild(elem('label', { class: 'humr-vault-field' }, [
      elem('span', { class: 'humr-vault-field-label' }, ['App name']),
      nameInput,
      elem('span', { class: 'humr-vault-field-help' }, ['Shown in Slack as the app and bot name. Defaults to your agent template.']),
    ]));
    form.appendChild(elem('div', { class: 'humr-vault-field-label' }, ['Agent type']));
    form.appendChild(modeChoices);
    form.appendChild(ownerEmailField);
    form.appendChild(homeChannelField);
    form.appendChild(elem('div', { class: 'humr-slack-create-row' }, [createLink]));
    form.appendChild(steps);
    for (const field of schema.fields || []) {
      form.appendChild(fieldInputFor(field));
    }
    syncCreateLink();
    syncOwnerEmail();
    syncHomeChannel();

    const saveBtn = elem('button', { class: 'humr-integration-btn humr-integration-btn-primary', type: 'submit' }, ['Save']);
    const actions = elem('div', { class: 'humr-modal-actions' }, [
      elem('button', { class: 'humr-integration-btn', type: 'button', onclick: close }, ['Cancel']),
      saveBtn,
    ]);
    form.appendChild(actions);
    wireVaultSubmit({ form, session, integration, saveBtn, actions, errorBox, successBox, close }, page);

    const modal = elem('div', { class: 'humr-modal humr-vault-modal' }, [
      elem('div', { class: 'humr-modal-title' }, [(schema.status === 'connected' ? 'Configure ' : 'Connect ') + (schema.label || integration.label)]),
      elem('div', { class: 'humr-modal-body' }, [schema.message || 'Tokens are sent directly to the Humanity Rules vault.']),
      errorBox,
      successBox,
      form,
    ]);
    backdrop.appendChild(modal);
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
    document.body.appendChild(backdrop);
  }

  cardSpecs.register({ kind: 'tls_intercept', slug: 'slack' }, (integration, page) => {
    return createTlsCardSpec(integration, page, { vaultRenderer: showSlackConfigModal });
  });
  window.HumrIntegrations.loadedExtensionScripts.add('slack');
})();
