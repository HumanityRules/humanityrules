// HUMR WebUI panel runtime — the one place that knows how to graft a HUMR
// top-level panel onto the upstream WebUI shell: rail icon, sidebar nav tab,
// sidebar pane, <main> view, and switchPanel integration.
//
// Upstream offers script injection but no UI API: its switchPanel() only
// toggles `showing-<name>` classes for its own hard-coded MAIN_VIEW_PANELS,
// and there is no panel-switch event to subscribe to. So this runtime wraps
// window.switchPanel ONCE and serves every registered panel from that single
// wrapper; feature scripts declare their panel and never touch the shell.
//
// Feature scripts call:
//
//   window.HumrPanel.register({
//     id,           // upstream panel name: switchPanel(id), #main<Id>, #panel<Id>, showing-<id>
//     title,        // nav label, tooltip, aria-label, and sidebar pane heading
//     icon,         // inner markup of a 24x24 stroke icon (path/circle elements)
//     viewClass,    // optional extra class(es) for the <section> main view
//     populateView(view),  // fill the <section id="main<Id>"> once at mount
//     populatePane(pane),  // optional: fill the sidebar pane below its heading
//     onMount(),    // optional: once, right after the shell DOM is injected
//     onShow(),     // optional: entering the panel; NOT awaited (see wrapper)
//     onHide(),     // optional: any switch to another panel; must be idempotent
//   })
//
// onMount/onShow/onHide failures (sync throws and async rejections) are
// logged and contained; they never break the panel switch or other panels.
//
// The feature's CSS reveals its view with `main.main.showing-<id> > #main<Id>`.
(() => {
  'use strict';

  if (window.HumrPanel) return;

  const panels = new Map(); // id -> spec, in registration (= manifest) order
  const mountQueue = [];    // specs waiting for the WebUI shell DOM
  let mountLoopScheduled = false;

  // Element builder shared with feature scripts (exported as HumrPanel.elem).
  function elem(tag, props, children) {
    const el = document.createElement(tag);
    if (props) {
      for (const k in props) {
        if (k === 'style' && typeof props[k] === 'object') Object.assign(el.style, props[k]);
        else if (k === 'dataset' && typeof props[k] === 'object') Object.assign(el.dataset, props[k]);
        else if (k.startsWith('on') && typeof props[k] === 'function') el.addEventListener(k.slice(2), props[k]);
        else if (k === 'disabled') el.disabled = !!props[k];
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

  function strokeIcon(iconMarkup, sizePx, strokeWidth) {
    return '<svg width="' + sizePx + '" height="' + sizePx + '" viewBox="0 0 24 24" fill="none"'
      + ' stroke="currentColor" stroke-width="' + strokeWidth + '" stroke-linecap="round"'
      + ' stroke-linejoin="round" aria-hidden="true">' + iconMarkup + '</svg>';
  }

  // Run a feature handler without letting it break the panel switch or the
  // other panels: sync throws and async rejections both just log. Never
  // awaited — switchPanel must resolve promptly for upstream callers.
  function runHandler(panelId, handlerName, handler) {
    const logFailure = (err) =>
      console.error('[humr-panel] Panel "' + panelId + '" ' + handlerName + ' failed:', err);
    try {
      const pending = handler();
      if (pending && typeof pending.catch === 'function') pending.catch(logFailure);
    } catch (err) {
      logFailure(err);
    }
  }

  function syncPanelClasses(activeId) {
    const mainEl = document.querySelector('main.main');
    if (!mainEl) return;
    for (const id of panels.keys()) mainEl.classList.toggle('showing-' + id, id === activeId);
  }

  // The single wrapper serving every registered panel. Toggling classes for
  // ALL panels in one place (instead of one chained wrapper per feature)
  // means no load-order coupling and no window where two `showing-*` classes
  // are set at once.
  function installSwitchPanelWrapper() {
    if (window.__humrPanelSwitchWrapped) return;
    window.__humrPanelSwitchWrapped = true;
    const upstreamSwitchPanel = window.switchPanel;
    window.switchPanel = async function (name) {
      // Upstream updates its active tab and main-view classes synchronously,
      // then awaits any per-panel lazy loader. Mirror that synchronous state
      // before awaiting so the previous HUMR view cannot overlap the new one
      // while a native panel loads. Reading the active tab also preserves the
      // current view when upstream rejects the switch or only collapses the
      // sidebar.
      const pending = upstreamSwitchPanel.apply(this, arguments);
      const activeTab = document.querySelector('[data-panel].active');
      if (activeTab) syncPanelClasses(activeTab.dataset.panel);

      const result = await pending;
      // false means upstream did NOT switch (settings unsaved-changes guard,
      // rail-click sidebar collapse): the active panel is unchanged, so our
      // handlers must not react either.
      if (result === false) return result;
      const requestedId = name || 'chat'; // upstream treats a missing name as chat
      // Upstream applies its state synchronously but resolves only after its
      // per-panel lazy loaders, so an earlier slow switch (e.g. skills) can
      // finish after a later fast one. The nav tabs' active state always
      // reflects the latest request; if that isn't us, a fresher call owns
      // the UI now and this stale continuation must not touch it.
      const latestTab = document.querySelector('[data-panel].active');
      if (latestTab && latestTab.dataset.panel !== requestedId) return result;
      // Phase 1: settle every panel's class before any handler runs, so a
      // handler that reads the DOM never sees a half-switched <main>.
      // Upstream only toggles showing-* for its own MAIN_VIEW_PANELS; ours
      // are toggled here, and feature CSS keys view visibility off them.
      syncPanelClasses(requestedId);
      // Phase 2: handlers, isolated per panel.
      for (const [id, spec] of panels) {
        const handler = id === requestedId ? spec.onShow : spec.onHide;
        if (handler) runHandler(id, id === requestedId ? 'onShow' : 'onHide', handler);
      }
      return result;
    };
  }

  function mountPanel(spec, shell) {
    const capitalizedId = spec.id.charAt(0).toUpperCase() + spec.id.slice(1);
    if (document.getElementById('main' + capitalizedId)) return;

    const activate = () => {
      // fromRailClick gives our entries the same rail behaviour as upstream's:
      // second click on the active icon collapses the sidebar, click while
      // collapsed re-expands it.
      window.switchPanel(spec.id, { fromRailClick: true });
    };

    // Build everything detached first: if a populate callback throws, nothing
    // has been inserted and the panel fails closed instead of half-mounting.
    const railBtn = elem('button', {
      type: 'button',
      class: 'rail-btn nav-tab has-tooltip',
      id: 'humr' + capitalizedId + 'RailBtn',
      'aria-label': spec.title,
      dataset: { panel: spec.id, tooltip: spec.title },
      onclick: activate,
    });
    railBtn.innerHTML = strokeIcon(spec.icon, 20, 1.5);

    const navBtn = elem('button', {
      type: 'button',
      class: 'nav-tab has-tooltip has-tooltip--bottom',
      id: 'humr' + capitalizedId + 'Tab',
      dataset: { panel: spec.id, label: spec.title, tooltip: spec.title },
      onclick: activate,
    });
    navBtn.innerHTML = strokeIcon(spec.icon, 18, 2);

    const view = elem('section', {
      class: 'main-view' + (spec.viewClass ? ' ' + spec.viewClass : ''),
      id: 'main' + capitalizedId,
    });
    if (spec.populateView) spec.populateView(view);

    // Upstream's switchPanel activates #panel<Id> in the sidebar; without one
    // the sidebar would look empty while our panel is active.
    const pane = elem('div', { class: 'panel-view', id: 'panel' + capitalizedId }, [
      elem('div', { class: 'panel-head' }, [elem('span', null, [spec.title])]),
    ]);
    if (spec.populatePane) spec.populatePane(pane);

    // Hermes 0.51+ shows a desktop `<nav class="rail">` and hides the legacy
    // `.sidebar-nav` at >=641px; register the entry in BOTH so it is visible
    // on every viewport. Older versions without a rail just get the nav tab.
    // The rail button goes before `.rail-spacer` so the entry sits with the
    // primary panels, not below the spacer where Settings lives; likewise the
    // pane goes before `.sidebar-bottom` (insertBefore(_, null) appends).
    const rail = document.querySelector('nav.rail');
    if (rail) rail.insertBefore(railBtn, rail.querySelector('.rail-spacer'));
    shell.sidebarNav.appendChild(navBtn);
    shell.mainEl.appendChild(view);
    shell.sidebar.insertBefore(pane, shell.sidebar.querySelector('.sidebar-bottom'));

    if (spec.onMount) runHandler(spec.id, 'onMount', spec.onMount);
  }

  function runMountLoop() {
    const sidebar = document.querySelector('.sidebar');
    const sidebarNav = sidebar && sidebar.querySelector('.sidebar-nav');
    const mainEl = document.querySelector('main.main');
    // The shell renders after extension scripts on fresh page loads; retry
    // each frame until it exists. switchPanel must exist too: the wrapper
    // hooks it, and onMount handlers may call it immediately.
    if (!sidebar || !sidebarNav || !mainEl || typeof window.switchPanel !== 'function') {
      requestAnimationFrame(runMountLoop);
      return;
    }
    mountLoopScheduled = false;
    installSwitchPanelWrapper();
    while (mountQueue.length) {
      const spec = mountQueue.shift();
      // Isolate mounts: one panel's buildView/onMount blowing up must not
      // strand the panels still queued behind it.
      try {
        mountPanel(spec, { sidebar, sidebarNav, mainEl });
      } catch (err) {
        console.error('[humr-panel] Failed to mount panel "' + spec.id + '":', err);
      }
    }
  }

  function register(spec) {
    if (!spec || !spec.id || !spec.title || !spec.icon) {
      console.error('[humr-panel] register() needs at least id, title, and icon.');
      return;
    }
    if (panels.has(spec.id)) {
      console.error('[humr-panel] Panel "' + spec.id + '" is already registered.');
      return;
    }
    panels.set(spec.id, spec);
    mountQueue.push(spec);
    if (!mountLoopScheduled) {
      mountLoopScheduled = true;
      requestAnimationFrame(runMountLoop);
    }
  }

  window.HumrPanel = { register, elem };
})();
