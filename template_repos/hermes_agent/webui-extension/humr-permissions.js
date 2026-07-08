// HUMR WebUI extension. Loaded per Hermes' docs/EXTENSIONS.md via the
// HERMES_WEBUI_EXTENSION_* env vars exported in webui.sh.
//
// Adds a Settings → Permissions section (after System): the self-referential IAM
// task-role permissions editor for THIS Hermes deployment. The target
// (app, environment) is implicit — HUMR resolves it from the deployment identity
// behind the broker, so no app/env is ever named here. All calls go same-origin
// to the humr_broker control API via the Caddy /__humr_broker/permissions/* route;
// the env bearer and AWS creds never enter the sandbox.
//
// Phase 1 is editor-only and the panel is the sole writer, so a mutation returns
// fresh state and we re-render from it — no cross-process refresh problem yet.
(() => {
  'use strict';

  const PERMISSIONS_URL = '/__humr_broker/permissions';
  const APPLY_POLL_MS = 2000;
  const DESCRIPTION_DEBOUNCE_MS = 600;

  let _draft = null;          // latest GET /draft state
  let _catalog = null;        // cached service-catalog {services, access_levels}
  let _rootEl = null;         // the element renderEditor paints into
  let _applyPollTimer = null;
  let _descriptionTimer = null;

  function elem(tag, props, children) {
    const el = document.createElement(tag);
    if (props) {
      for (const k in props) {
        if (k === 'style' && typeof props[k] === 'object') Object.assign(el.style, props[k]);
        else if (k === 'dataset' && typeof props[k] === 'object') Object.assign(el.dataset, props[k]);
        else if (k.startsWith('on') && typeof props[k] === 'function') el.addEventListener(k.slice(2), props[k]);
        else if (k === 'disabled') el.disabled = !!props[k];
        else if (k === 'checked') el.checked = !!props[k];
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

  // ── Broker calls ──────────────────────────────────────────────────────────

  async function fetchDraft(requestId) {
    const url = requestId ? `${PERMISSIONS_URL}/draft?request_id=${encodeURIComponent(requestId)}` : `${PERMISSIONS_URL}/draft`;
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) throw new Error(`draft fetch failed (${response.status})`);
    return await response.json();
  }

  async function fetchCatalog() {
    if (_catalog) return _catalog;
    const response = await fetch(`${PERMISSIONS_URL}/service-catalog`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`service-catalog fetch failed (${response.status})`);
    _catalog = await response.json();
    return _catalog;
  }

  async function postStatement(requestId, body) {
    const response = await fetch(`${PERMISSIONS_URL}/draft/${encodeURIComponent(requestId)}/statement`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`statement failed (${response.status})`);
    return await response.json();
  }

  function postDescription(requestId, description) {
    return fetch(`${PERMISSIONS_URL}/draft/${encodeURIComponent(requestId)}/description`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ description }),
    });
  }

  async function postSimple(requestId, action) {
    const response = await fetch(`${PERMISSIONS_URL}/draft/${encodeURIComponent(requestId)}/${action}`, { method: 'POST' });
    if (!response.ok) {
      let payload = {};
      try { payload = await response.json(); } catch (_) { /* ignore */ }
      throw new Error(payload.error || `${action} failed (${response.status})`);
    }
    return await response.json();
  }

  // ── Mutations (panel is the sole writer; re-render from returned state) ──────

  function applyMutationResult(result) {
    if (result.service_groups) _draft.service_groups = result.service_groups;
    if ('has_changes' in result) _draft.has_changes = result.has_changes;
    if (result.updated_at) _draft.updated_at = result.updated_at;
    render();
  }

  async function mutateStatement(body) {
    try {
      applyMutationResult(await postStatement(_draft.request_id, body));
    } catch (err) {
      flashError(err.message);
    }
  }

  // Mutations on an existing statement are keyed by its `sid` (statement_id): a
  // service can appear in more than one statement (e.g. List on * and Read on
  // specific tables), so the service name alone no longer identifies one. Only
  // add_service omits it — it always appends a fresh statement.
  function toggleLevel(sid, service, level, checked) {
    mutateStatement({ action: checked ? 'add_level' : 'remove_level', service, statement_id: sid, level });
  }

  function addResource(sid, service, arn, s3Prefix) {
    if (!arn) return;
    mutateStatement({ action: 'add_resource', service, statement_id: sid, arn, s3_prefix: s3Prefix || '' });
  }

  function removeResource(sid, service, arn) {
    mutateStatement({ action: 'remove_resource', service, statement_id: sid, arn });
  }

  function addService(service) {
    if (service) mutateStatement({ action: 'add_service', service });
  }

  function removeService(sid, service) {
    mutateStatement({ action: 'remove_service', service, statement_id: sid });
  }

  async function doCancel() {
    try {
      applyMutationResult(await postSimple(_draft.request_id, 'cancel'));
    } catch (err) {
      flashError(err.message);
    }
  }

  async function doRefreshResources() {
    try {
      applyMutationResult(await postSimple(_draft.request_id, 'refresh-resources'));
    } catch (err) {
      flashError(err.message);
    }
  }

  async function doApply() {
    try {
      await postSimple(_draft.request_id, 'apply');
      _draft.status = 'approved_pending_apply';
      render();
      startApplyPoll();
    } catch (err) {
      flashError(err.message);
    }
  }

  function onDescriptionInput(value) {
    _draft.description = value;
    if (_descriptionTimer) clearTimeout(_descriptionTimer);
    _descriptionTimer = setTimeout(() => { postDescription(_draft.request_id, value).catch(() => {}); }, DESCRIPTION_DEBOUNCE_MS);
  }

  // ── Apply lifecycle poll (the only poll phase 1 needs) ──────────────────────

  function stopApplyPoll() {
    if (_applyPollTimer) { clearTimeout(_applyPollTimer); _applyPollTimer = null; }
  }

  function startApplyPoll() {
    stopApplyPoll();
    const tick = async () => {
      try {
        _draft = await fetchDraft(_draft.request_id);
      } catch (_) { /* transient; keep polling */ }
      render();
      if (_draft && (_draft.status === 'applied' || _draft.status === 'failed')) return;
      _applyPollTimer = setTimeout(tick, APPLY_POLL_MS);
    };
    _applyPollTimer = setTimeout(tick, APPLY_POLL_MS);
  }

  async function startNewDraft() {
    stopApplyPoll();
    try {
      _draft = await fetchDraft(null);
      render();
    } catch (err) {
      flashError(err.message);
    }
  }

  // ── Rendering ───────────────────────────────────────────────────────────────

  let _errorEl = null;
  function flashError(message) {
    if (!_errorEl) return;
    _errorEl.textContent = message;
    _errorEl.style.display = 'block';
    setTimeout(() => { if (_errorEl) _errorEl.style.display = 'none'; }, 6000);
  }

  function renderLevels(group) {
    return elem('div', { class: 'humr-perm-levels' }, group.access_levels.map((l) =>
      elem('label', { class: 'humr-perm-level' }, [
        elem('input', {
          type: 'checkbox', checked: l.checked,
          onchange: (e) => toggleLevel(group.sid, group.service, l.name, e.target.checked),
        }),
        l.name,
      ])
    ));
  }

  function renderResources(group) {
    const chips = (group.resources || []).map((arn) =>
      elem('span', { class: 'humr-perm-chip' }, [
        arn,
        elem('button', { type: 'button', class: 'humr-perm-chip-x', title: 'Remove', onclick: () => removeResource(group.sid, group.service, arn) }, ['×']),
      ])
    );
    const chipsRow = chips.length
      ? elem('div', { class: 'humr-perm-chips' }, chips)
      : elem('div', { class: 'humr-perm-all-resources' }, ['All resources (*) — no resource restriction']);

    const unselected = (group.available_resources || []).filter((r) => !r.selected);
    const select = elem('select', { class: 'humr-perm-resource-select' }, [
      elem('option', { value: '' }, [group.resource_placeholder || 'Select resource...']),
      ...unselected.map((r) => elem('option', { value: r.arn }, [r.label || r.arn])),
    ]);
    const prefixInput = group.service === 's3'
      ? elem('input', { class: 'humr-perm-s3-prefix', type: 'text', placeholder: 'key prefix (optional)' })
      : null;
    const addBtn = elem('button', {
      type: 'button', class: 'humr-perm-btn humr-perm-btn-primary',
      disabled: !select.value,
      onclick: () => addResource(group.sid, group.service, select.value, prefixInput ? prefixInput.value.trim() : ''),
    }, ['Add']);
    // Add is the required final step, so it stays disabled (and visibly muted)
    // until a resource is picked — at which point it lights up as the primary
    // action next to the dropdown. The post-add re-render resets it.
    select.addEventListener('change', () => { addBtn.disabled = !select.value; });

    return elem('div', { class: 'humr-perm-resources' }, [
      chipsRow,
      elem('div', { class: 'humr-perm-add-resource' }, [select, prefixInput, addBtn]),
    ]);
  }

  function renderGroup(group) {
    return elem('div', { class: 'humr-perm-group' }, [
      elem('div', { class: 'humr-perm-group-head' }, [
        elem('div', { class: 'humr-perm-service' }, [
          elem('span', { class: 'humr-perm-service-name' }, [group.display_name || group.service]),
          elem('code', { class: 'humr-perm-service-prefix' }, [group.service]),
        ]),
        elem('button', { type: 'button', class: 'humr-perm-btn humr-perm-btn-ghost', onclick: () => removeService(group.sid, group.service) }, ['Remove service']),
      ]),
      renderLevels(group),
      renderResources(group),
    ]);
  }

  function serviceMatchesQuery(service, query) {
    if (!query) return true;
    const lower = query.toLowerCase();
    return service.label.toLowerCase().includes(lower) || service.value.toLowerCase().includes(lower);
  }

  function renderAddService() {
    const services = (_catalog && _catalog.services) || [];
    const wrapper = elem('div', { class: 'humr-perm-add-service humr-perm-service-picker' });
    const input = elem('input', {
      type: 'text',
      class: 'humr-perm-add-service-search',
      placeholder: 'Search services…',
      autocomplete: 'off',
    });
    const list = elem('div', { class: 'humr-perm-picker-list', style: { display: 'none' } });

    function paintList() {
      list.innerHTML = '';
      const query = input.value.trim();
      const curatedFiltered = services.filter((s) => s.is_curated && serviceMatchesQuery(s, query));
      const allFiltered = services.filter((s) => serviceMatchesQuery(s, query));

      const addSection = (title, items) => {
        if (!items.length) return;
        if (title) list.appendChild(elem('div', { class: 'humr-perm-picker-section' }, [title]));
        for (const s of items) {
          list.appendChild(elem('button', {
            type: 'button',
            class: 'humr-perm-picker-item',
            dataset: { service: s.value },
            onmousedown: (e) => {
              e.preventDefault();
              input.value = '';
              list.style.display = 'none';
              addService(s.value);
            },
          }, [s.label]));
        }
      };

      if (curatedFiltered.length && !query) {
        addSection('Curated', curatedFiltered);
        list.appendChild(elem('div', { class: 'humr-perm-picker-divider' }));
      }
      if (allFiltered.length) addSection(query ? null : 'All services', allFiltered);
      if (!list.childNodes.length) {
        list.appendChild(elem('div', { class: 'humr-perm-picker-empty' }, ['No matching services']));
      }
    }

    let blurTimer = null;
    input.addEventListener('focus', () => {
      clearTimeout(blurTimer);
      paintList();
      list.style.display = 'block';
    });
    input.addEventListener('input', () => {
      paintList();
      list.style.display = 'block';
    });
    input.addEventListener('blur', () => {
      blurTimer = setTimeout(() => { list.style.display = 'none'; }, 150);
    });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        input.value = '';
        list.style.display = 'none';
        input.blur();
      }
    });

    wrapper.appendChild(input);
    wrapper.appendChild(list);
    return wrapper;
  }

  function renderStatusBanner() {
    const status = _draft.status;
    if (status === 'draft') return null;
    const label = {
      approved_pending_apply: 'Approved — applying shortly…',
      applying: 'Applying changes to AWS…',
      applied: 'Permissions applied.',
      failed: 'Apply failed.',
    }[status] || status;
    const banner = elem('div', { class: `humr-perm-banner humr-perm-banner-${status}` }, [
      elem('div', { class: 'humr-perm-banner-label' }, [label]),
      _draft.status_message ? elem('div', { class: 'humr-perm-banner-msg' }, [_draft.status_message]) : null,
    ]);
    if (status === 'applied' || status === 'failed') {
      banner.appendChild(elem('button', { type: 'button', class: 'humr-perm-btn', onclick: startNewDraft }, ['Start a new draft']));
    }
    return banner;
  }

  function renderMetaRow(label, value) {
    if (!value) return null;
    return elem('div', { class: 'humr-perm-meta-row' }, [
      elem('span', { class: 'humr-perm-meta-label' }, [label]),
      elem('span', { class: 'humr-perm-meta-value' }, [value]),
    ]);
  }

  function renderHeader() {
    const isDraft = _draft.status === 'draft';
    const actions = elem('div', { class: 'humr-perm-actions' }, [
      isDraft ? elem('button', { type: 'button', class: 'humr-perm-btn humr-perm-btn-ghost', onclick: doRefreshResources }, ['Refresh AWS resources']) : null,
      isDraft && _draft.has_changes ? elem('button', { type: 'button', class: 'humr-perm-btn humr-perm-btn-ghost', onclick: doCancel }, ['Cancel changes']) : null,
      isDraft ? elem('button', { type: 'button', class: 'humr-perm-btn humr-perm-btn-primary', disabled: !_draft.has_changes, onclick: doApply }, ['Apply']) : null,
    ]);
    return elem('div', { class: 'humr-perm-head' }, [
      elem('div', { class: 'humr-perm-head-row' }, [
        elem('div', { class: 'humr-perm-title' }, ['Permissions']),
        actions,
      ]),
      elem('div', { class: 'humr-perm-meta' }, [
        renderMetaRow('Agent', _draft.app && _draft.app.name),
        renderMetaRow('Environment', _draft.environment && _draft.environment.slug),
        renderMetaRow('AWS account', _draft.environment && _draft.environment.aws_account),
      ]),
      elem('div', { class: 'humr-perm-sub' }, ['AWS permissions this agent requires. After applying your request, you will need to wait for approval.']),
    ]);
  }

  function render() {
    if (!_rootEl) return;
    _rootEl.innerHTML = '';
    _errorEl = elem('div', { class: 'humr-perm-error', style: { display: 'none' } });

    if (!_draft) {
      _rootEl.appendChild(elem('div', { class: 'humr-perm-empty' }, ['Permissions editor is unavailable. If this persists, the platform broker may not be running.']));
      return;
    }

    // Only a DRAFT is editable. Once Apply leaves DRAFT (approved/applying/
    // applied/failed) the draft-editing components — services, add-service,
    // rationale — would only offer dead controls (mutations 409), so we drop
    // them and show just the status banner; for applied/failed it carries the
    // "Start a new draft" action.
    const isDraft = _draft.status === 'draft';
    const body = elem('div', { class: 'humr-perm-body' }, [
      _errorEl,
      renderStatusBanner(),
      ...(isDraft ? [
        elem('div', { class: 'humr-perm-section-title' }, ['Services']),
        ...(_draft.service_groups && _draft.service_groups.length
          ? _draft.service_groups.map(renderGroup)
          : [elem('div', { class: 'humr-perm-empty' }, ['No services yet. Add one below.'])]),
        renderAddService(),
        elem('div', { class: 'humr-perm-section-title' }, ['Rationale']),
        elem('textarea', {
          class: 'humr-perm-description', rows: '3',
          placeholder: 'Why these permissions are needed (helps the approver).',
          oninput: (e) => onDescriptionInput(e.target.value),
        }, [_draft.description || '']),
      ] : []),
    ]);
    _rootEl.appendChild(elem('div', { class: 'humr-perm-page-inner' }, [renderHeader(), body]));
  }

  async function refreshAndRender() {
    try {
      await fetchCatalog();
      _draft = await fetchDraft(_draft ? _draft.request_id : null);
      // If a previously-tracked request is mid-apply, resume polling.
      if (_draft && (_draft.status === 'approved_pending_apply' || _draft.status === 'applying')) startApplyPoll();
    } catch (_) {
      _draft = null;
    }
    render();
  }

  // ── Mount (Settings section, immediately before System) ────────────────────

  const UPSTREAM_SETTINGS_PANES = ['Conversation', 'Appearance', 'Preferences', 'Providers', 'Plugins', 'System'];
  let _showingPermissions = false;

  function isSettingsPanelActive() {
    return !!document.querySelector('#panelSettings.active')
      || !!document.querySelector('[data-panel="settings"].active');
  }

  function activatePermissionsSection() {
    document.querySelectorAll('#settingsMenu .side-menu-item').forEach((it) => {
      it.classList.toggle('active', it.dataset.settingsSection === 'permissions');
    });
    UPSTREAM_SETTINGS_PANES.forEach((cap) => {
      const pane = document.getElementById('settingsPane' + cap);
      if (pane) pane.classList.remove('active');
    });
    const permPane = document.getElementById('settingsPanePermissions');
    if (permPane) permPane.classList.add('active');
  }

  async function showPermissionsSection() {
    _showingPermissions = true;
    // Only switch panels when Settings is not already open. switchPanel('settings')
    // always calls loadSettingsPanel(), which async-restores _settingsSection
    // (Appearance) and was clobbering Permissions right after we painted it.
    if (!isSettingsPanelActive() && typeof window.switchPanel === 'function') {
      await window.switchPanel('settings');
    }
    activatePermissionsSection();
    refreshAndRender();
  }

  function ensureSettingsSection() {
    const settingsMenu = document.getElementById('settingsMenu');
    const settingsMain = document.querySelector('#mainSettings > .settings-main');
    if (!settingsMenu || !settingsMain) return false;
    if (document.getElementById('settingsPanePermissions')) return true;

    const menuIcon = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/></svg>';
    const menuBtn = elem('button', {
      type: 'button',
      class: 'side-menu-item',
      id: 'humrPermissionsSettingsItem',
      dataset: { settingsSection: 'permissions' },
      onclick: () => {
        if (typeof window.switchSettingsSection === 'function') window.switchSettingsSection('permissions');
      },
    });
    menuBtn.innerHTML = menuIcon + '<span>Permissions</span>';
    // Hermes 0.51 wraps the menu buttons in `.settings-menu-items`, so System
    // is no longer a direct child of #settingsMenu. Insert relative to System's
    // real parent (the wrapper) — settingsMenu.insertBefore() would throw
    // NotFoundError and abort the whole mount, dropping the Permissions section.
    const systemBtn = settingsMenu.querySelector('[data-settings-section="system"]');
    const menuList = systemBtn ? systemBtn.parentNode : settingsMenu.querySelector('.settings-menu-items') || settingsMenu;
    if (systemBtn) menuList.insertBefore(menuBtn, systemBtn);
    else menuList.appendChild(menuBtn);

    _rootEl = elem('div', { class: 'humr-perm-root', id: 'humrPermissionsRoot' });
    const permPane = elem('div', { class: 'settings-pane', id: 'settingsPanePermissions' }, [_rootEl]);
    const systemPane = document.getElementById('settingsPaneSystem');
    if (systemPane) settingsMain.insertBefore(permPane, systemPane);
    else settingsMain.appendChild(permPane);
    return true;
  }

  // Upstream switchSettingsSection() has a hardcoded allow-list, so route
  // permissions through a wrapper (same pattern as the old Integrations pane).
  function wrapSwitchSettingsSection() {
    if (typeof window.switchSettingsSection !== 'function') return;
    if (window.__humrPermissionsSettingsWrapped) return;
    window.__humrPermissionsSettingsWrapped = true;
    const orig = window.switchSettingsSection;
    window.switchSettingsSection = async function (name) {
      if (name === 'permissions') {
        await showPermissionsSection();
        return;
      }
      _showingPermissions = false;
      const permPane = document.getElementById('settingsPanePermissions');
      if (permPane) permPane.classList.remove('active');
      return orig.apply(this, arguments);
    };
  }

  // loadSettingsPanel() finishes with switchSettingsSection(_settingsSection)
  // (Appearance). If the user opened Permissions while that fetch was still
  // in flight, restore Permissions after the panel hydrate completes.
  function wrapLoadSettingsPanel() {
    if (typeof window.loadSettingsPanel !== 'function') return;
    if (window.__humrPermissionsLoadWrapped) return;
    window.__humrPermissionsLoadWrapped = true;
    const orig = window.loadSettingsPanel;
    window.loadSettingsPanel = async function () {
      await orig.apply(this, arguments);
      if (_showingPermissions) activatePermissionsSection();
    };
  }

  function init() {
    if (!ensureSettingsSection()) { requestAnimationFrame(init); return; }
    wrapSwitchSettingsSection();
    wrapLoadSettingsPanel();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
