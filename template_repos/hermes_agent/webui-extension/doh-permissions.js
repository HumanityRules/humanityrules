// DOH WebUI extension. Loaded per Hermes' docs/EXTENSIONS.md via the
// HERMES_WEBUI_EXTENSION_* env vars exported in webui.sh.
//
// Adds a full-width "Permissions" rail destination: the self-referential IAM
// task-role permissions editor for THIS Hermes deployment. The target
// (app, environment) is implicit — DOH resolves it from the deployment identity
// behind the broker, so no app/env is ever named here. All calls go same-origin
// to the doh_broker control API via the Caddy /__doh_broker/permissions/* route;
// the env bearer and AWS creds never enter the sandbox.
//
// Phase 1 is editor-only and the panel is the sole writer, so a mutation returns
// fresh state and we re-render from it — no cross-process refresh problem yet.
// renderEditor() is container-agnostic (paints into a passed-in element) so the
// phase-2 move into the sidebar is a one-line change of mount target.
(() => {
  'use strict';

  const PERMISSIONS_URL = '/__doh_broker/permissions';
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
    return elem('div', { class: 'doh-perm-levels' }, group.access_levels.map((l) =>
      elem('label', { class: 'doh-perm-level' }, [
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
      elem('span', { class: 'doh-perm-chip' }, [
        arn,
        elem('button', { type: 'button', class: 'doh-perm-chip-x', title: 'Remove', onclick: () => removeResource(group.sid, group.service, arn) }, ['×']),
      ])
    );
    const chipsRow = chips.length
      ? elem('div', { class: 'doh-perm-chips' }, chips)
      : elem('div', { class: 'doh-perm-all-resources' }, ['All resources (*) — no resource restriction']);

    const unselected = (group.available_resources || []).filter((r) => !r.selected);
    const select = elem('select', { class: 'doh-perm-resource-select' }, [
      elem('option', { value: '' }, [group.resource_placeholder || 'Select resource...']),
      ...unselected.map((r) => elem('option', { value: r.arn }, [r.label || r.arn])),
    ]);
    const prefixInput = group.service === 's3'
      ? elem('input', { class: 'doh-perm-s3-prefix', type: 'text', placeholder: 'key prefix (optional)' })
      : null;
    const addBtn = elem('button', {
      type: 'button', class: 'doh-perm-btn doh-perm-btn-primary',
      disabled: !select.value,
      onclick: () => addResource(group.sid, group.service, select.value, prefixInput ? prefixInput.value.trim() : ''),
    }, ['Add']);
    // Add is the required final step, so it stays disabled (and visibly muted)
    // until a resource is picked — at which point it lights up as the primary
    // action next to the dropdown. The post-add re-render resets it.
    select.addEventListener('change', () => { addBtn.disabled = !select.value; });

    return elem('div', { class: 'doh-perm-resources' }, [
      chipsRow,
      elem('div', { class: 'doh-perm-add-resource' }, [select, prefixInput, addBtn]),
    ]);
  }

  function renderGroup(group) {
    return elem('div', { class: 'doh-perm-group' }, [
      elem('div', { class: 'doh-perm-group-head' }, [
        elem('div', { class: 'doh-perm-service' }, [
          elem('span', { class: 'doh-perm-service-name' }, [group.display_name || group.service]),
          elem('code', { class: 'doh-perm-service-prefix' }, [group.service]),
        ]),
        elem('button', { type: 'button', class: 'doh-perm-btn doh-perm-btn-ghost', onclick: () => removeService(group.sid, group.service) }, ['Remove service']),
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
    const wrapper = elem('div', { class: 'doh-perm-add-service doh-perm-service-picker' });
    const input = elem('input', {
      type: 'text',
      class: 'doh-perm-add-service-search',
      placeholder: 'Search services…',
      autocomplete: 'off',
    });
    const list = elem('div', { class: 'doh-perm-picker-list', style: { display: 'none' } });

    function paintList() {
      list.innerHTML = '';
      const query = input.value.trim();
      const curatedFiltered = services.filter((s) => s.is_curated && serviceMatchesQuery(s, query));
      const allFiltered = services.filter((s) => serviceMatchesQuery(s, query));

      const addSection = (title, items) => {
        if (!items.length) return;
        if (title) list.appendChild(elem('div', { class: 'doh-perm-picker-section' }, [title]));
        for (const s of items) {
          list.appendChild(elem('button', {
            type: 'button',
            class: 'doh-perm-picker-item',
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
        list.appendChild(elem('div', { class: 'doh-perm-picker-divider' }));
      }
      if (allFiltered.length) addSection(query ? null : 'All services', allFiltered);
      if (!list.childNodes.length) {
        list.appendChild(elem('div', { class: 'doh-perm-picker-empty' }, ['No matching services']));
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
    const banner = elem('div', { class: `doh-perm-banner doh-perm-banner-${status}` }, [
      elem('div', { class: 'doh-perm-banner-label' }, [label]),
      _draft.status_message ? elem('div', { class: 'doh-perm-banner-msg' }, [_draft.status_message]) : null,
    ]);
    if (status === 'applied' || status === 'failed') {
      banner.appendChild(elem('button', { type: 'button', class: 'doh-perm-btn', onclick: startNewDraft }, ['Start a new draft']));
    }
    return banner;
  }

  function renderMetaRow(label, value) {
    if (!value) return null;
    return elem('div', { class: 'doh-perm-meta-row' }, [
      elem('span', { class: 'doh-perm-meta-label' }, [label]),
      elem('span', { class: 'doh-perm-meta-value' }, [value]),
    ]);
  }

  function renderHeader() {
    const isDraft = _draft.status === 'draft';
    const actions = elem('div', { class: 'doh-perm-actions' }, [
      isDraft ? elem('button', { type: 'button', class: 'doh-perm-btn doh-perm-btn-ghost', onclick: doRefreshResources }, ['Refresh AWS resources']) : null,
      isDraft && _draft.has_changes ? elem('button', { type: 'button', class: 'doh-perm-btn doh-perm-btn-ghost', onclick: doCancel }, ['Cancel changes']) : null,
      isDraft ? elem('button', { type: 'button', class: 'doh-perm-btn doh-perm-btn-primary', disabled: !_draft.has_changes, onclick: doApply }, ['Apply']) : null,
    ]);
    return elem('div', { class: 'doh-perm-head' }, [
      elem('div', { class: 'doh-perm-head-row' }, [
        elem('div', { class: 'doh-perm-title' }, ['Permissions']),
        actions,
      ]),
      elem('div', { class: 'doh-perm-meta' }, [
        renderMetaRow('Agent', _draft.app && _draft.app.name),
        renderMetaRow('Environment', _draft.environment && _draft.environment.slug),
        renderMetaRow('AWS account', _draft.environment && _draft.environment.aws_account),
      ]),
      elem('div', { class: 'doh-perm-sub' }, ['AWS permissions this agent requires. After applying your request, you will need to wait for approval.']),
    ]);
  }

  function render() {
    if (!_rootEl) return;
    _rootEl.innerHTML = '';
    _errorEl = elem('div', { class: 'doh-perm-error', style: { display: 'none' } });

    if (!_draft) {
      _rootEl.appendChild(elem('div', { class: 'doh-perm-empty' }, ['Permissions editor is unavailable. If this persists, the platform broker may not be running.']));
      return;
    }

    // Only a DRAFT is editable. Once Apply leaves DRAFT (approved/applying/
    // applied/failed) the draft-editing components — services, add-service,
    // rationale — would only offer dead controls (mutations 409), so we drop
    // them and show just the status banner; for applied/failed it carries the
    // "Start a new draft" action.
    const isDraft = _draft.status === 'draft';
    const body = elem('div', { class: 'doh-perm-body' }, [
      _errorEl,
      renderStatusBanner(),
      ...(isDraft ? [
        elem('div', { class: 'doh-perm-section-title' }, ['Services']),
        ...(_draft.service_groups && _draft.service_groups.length
          ? _draft.service_groups.map(renderGroup)
          : [elem('div', { class: 'doh-perm-empty' }, ['No services yet. Add one below.'])]),
        renderAddService(),
        elem('div', { class: 'doh-perm-section-title' }, ['Rationale']),
        elem('textarea', {
          class: 'doh-perm-description', rows: '3',
          placeholder: 'Why these permissions are needed (helps the approver).',
          oninput: (e) => onDescriptionInput(e.target.value),
        }, [_draft.description || '']),
      ] : []),
    ]);
    _rootEl.appendChild(elem('div', { class: 'doh-perm-page-inner' }, [renderHeader(), body]));
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

  // ── Mount (rail destination, mirrors doh-integrations.js) ────────────────────

  function ensureRailAndView() {
    const sidebar = document.querySelector('.sidebar');
    const sidebarNav = sidebar && sidebar.querySelector('.sidebar-nav');
    const mainEl = document.querySelector('main.main');
    const rail = document.querySelector('nav.rail');
    if (!sidebar || !sidebarNav || !mainEl) return false;
    if (document.getElementById('mainPermissions')) return true;

    const onActivate = () => {
      if (typeof window.switchPanel === 'function') window.switchPanel('permissions', { fromRailClick: true });
    };
    // Shield-check icon — permissions/governance.
    const railIcon = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/></svg>';
    const navIcon = railIcon.replace(/width="20" height="20"/, 'width="18" height="18"').replace(/stroke-width="1.5"/, 'stroke-width="2"');

    if (rail && !document.getElementById('dohPermissionsRailBtn')) {
      const railBtn = elem('button', {
        type: 'button', class: 'rail-btn nav-tab has-tooltip', id: 'dohPermissionsRailBtn',
        'aria-label': 'Permissions', dataset: { panel: 'permissions', tooltip: 'Permissions' }, onclick: onActivate,
      });
      railBtn.innerHTML = railIcon;
      const spacer = rail.querySelector('.rail-spacer');
      if (spacer) rail.insertBefore(railBtn, spacer);
      else rail.appendChild(railBtn);
    }

    if (!document.getElementById('dohPermissionsTab')) {
      const navBtn = elem('button', {
        type: 'button', class: 'nav-tab has-tooltip has-tooltip--bottom', id: 'dohPermissionsTab',
        dataset: { panel: 'permissions', label: 'Permissions', tooltip: 'Permissions' }, onclick: onActivate,
      });
      navBtn.innerHTML = navIcon;
      sidebarNav.appendChild(navBtn);
    }

    // Full-width main-view destination. renderEditor paints into #dohPermissionsRoot
    // (container-agnostic), so phase 2 only changes which element we pass in.
    _rootEl = elem('div', { class: 'doh-perm-root', id: 'dohPermissionsRoot' });
    const view = elem('section', { class: 'main-view doh-perm-page', id: 'mainPermissions' }, [_rootEl]);
    mainEl.appendChild(view);

    const sidebarPane = elem('div', { class: 'panel-view', id: 'panelPermissions' }, [
      elem('div', { class: 'panel-head' }, [elem('span', null, ['Permissions'])]),
      elem('div', { class: 'doh-perm-side-note' }, ['AWS permissions this agent requires. After applying your request, you will need to wait for approval.']),
    ]);
    const sidebarBottom = sidebar.querySelector('.sidebar-bottom');
    if (sidebarBottom) sidebar.insertBefore(sidebarPane, sidebarBottom);
    else sidebar.appendChild(sidebarPane);
    return true;
  }

  // Chain on switchPanel: toggle `showing-permissions` on <main> after upstream
  // runs (its loop only handles known panels), and render on activate. Not
  // awaited — a sibling wrapper may be waiting on us to toggle its own class.
  function wrapSwitchPanel() {
    if (typeof window.switchPanel !== 'function') return;
    if (window.__dohPermissionsWrapped) return;
    window.__dohPermissionsWrapped = true;
    const orig = window.switchPanel;
    window.switchPanel = async function (name) {
      const result = await orig.apply(this, arguments);
      const mainEl = document.querySelector('main.main');
      if (mainEl) mainEl.classList.toggle('showing-permissions', name === 'permissions');
      if (name === 'permissions') refreshAndRender();
      return result;
    };
  }

  function init() {
    if (!ensureRailAndView()) { requestAnimationFrame(init); return; }
    wrapSwitchPanel();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
