// HUMR WebUI extension: embedded Widgets panel.
//
// The registry is platform-generated and served from the same origin. This
// host owns navigation only; each Widget owns the document loaded in the one
// iframe below.
(() => {
  'use strict';

  if (!window.HumrPanel) {
    console.error('[humr-widgets] humr-panel.js failed to load; not mounting.');
    return;
  }

  const elem = window.HumrPanel.elem;
  const REGISTRY_URL = '/widgets/__admin/registry.json';
  const POLL_MS = 3000;
  const SELECTED_STORAGE_KEY = 'humr.widgets.selectedSlug';
  const SLUG_PATTERN = /^[a-z][a-z0-9-]{0,30}[a-z0-9]$/;
  const SVG_NS = 'http://www.w3.org/2000/svg';

  // Registry icon names select only host-owned SVG definitions. Unknown names
  // use the generic Widget mark and are never interpreted as markup.
  const ICON_SHAPES = Object.freeze({
    widget: [
      ['rect', { x: '3', y: '3', width: '7', height: '7', rx: '1' }],
      ['rect', { x: '14', y: '3', width: '7', height: '7', rx: '1' }],
      ['rect', { x: '3', y: '14', width: '7', height: '7', rx: '1' }],
      ['rect', { x: '14', y: '14', width: '7', height: '7', rx: '1' }],
    ],
    'chart-bar': [
      ['path', { d: 'M4 20V10M10 20V4M16 20v-7M22 20H2' }],
    ],
    calendar: [
      ['rect', { x: '3', y: '5', width: '18', height: '16', rx: '2' }],
      ['path', { d: 'M16 3v4M8 3v4M3 10h18' }],
    ],
    code: [
      ['path', { d: 'm9 18-6-6 6-6M15 6l6 6-6 6' }],
    ],
    globe: [
      ['circle', { cx: '12', cy: '12', r: '9' }],
      ['path', { d: 'M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18' }],
    ],
    table: [
      ['rect', { x: '3', y: '4', width: '18', height: '16', rx: '2' }],
      ['path', { d: 'M3 10h18M9 4v16' }],
    ],
    'list-check': [
      ['path', { d: 'm3 6 2 2 4-4M11 6h10M3 12l2 2 4-4M11 12h10M3 18l2 2 4-4M11 18h10' }],
    ],
  });

  let _list = null;
  let _frame = null;
  let _state = null;
  let _stateTitle = null;
  let _stateDetail = null;
  let _rows = new Map();
  let _widgets = [];
  let _selectedSlug = readStoredSlug();
  let _frameUrl = null;
  let _hasGoodRegistry = false;
  let _active = false;
  let _session = 0;
  let _latestRequest = 0;
  let _pollTimer = null;

  function readStoredSlug() {
    try {
      const slug = window.localStorage.getItem(SELECTED_STORAGE_KEY);
      return typeof slug === 'string' && SLUG_PATTERN.test(slug) ? slug : null;
    } catch (_) {
      return null;
    }
  }

  function storeSelectedSlug(slug) {
    try {
      if (slug === null) window.localStorage.removeItem(SELECTED_STORAGE_KEY);
      else window.localStorage.setItem(SELECTED_STORAGE_KEY, slug);
    } catch (_) {
      // Storage can be unavailable without affecting Widget navigation.
    }
  }

  function widgetIcon(iconName) {
    const shapes = typeof iconName === 'string' && Object.prototype.hasOwnProperty.call(ICON_SHAPES, iconName)
      ? ICON_SHAPES[iconName]
      : ICON_SHAPES.widget;
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'humr-widget-row-icon');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.7');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    for (const [tag, attributes] of shapes) {
      const shape = document.createElementNS(SVG_NS, tag);
      for (const [name, value] of Object.entries(attributes)) shape.setAttribute(name, value);
      svg.appendChild(shape);
    }
    return svg;
  }

  function validString(value) {
    if (typeof value !== 'string' || !value.trim() || value.includes('\0')) return false;
    for (let index = 0; index < value.length; index += 1) {
      const codeUnit = value.charCodeAt(index);
      if (codeUnit >= 0xD800 && codeUnit <= 0xDBFF) {
        const nextCodeUnit = value.charCodeAt(index + 1);
        if (nextCodeUnit < 0xDC00 || nextCodeUnit > 0xDFFF) return false;
        index += 1;
      } else if (codeUnit >= 0xDC00 && codeUnit <= 0xDFFF) {
        return false;
      }
    }
    return true;
  }

  function validatedWidgetUrl(value, slug) {
    const expected = '/widgets/' + slug + '/';
    if (value !== expected) return null;
    try {
      const parsed = new URL(value, window.location.origin);
      if (parsed.origin !== window.location.origin) return null;
      if (parsed.pathname !== expected || parsed.search || parsed.hash) return null;
      return expected;
    } catch (_) {
      return null;
    }
  }

  function normalizeRegistry(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null;
    if (payload.schema_version !== 1 || !Array.isArray(payload.widgets)) return null;

    const bySlug = new Map();
    for (const candidate of payload.widgets) {
      if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) continue;
      if (typeof candidate.slug !== 'string' || !SLUG_PATTERN.test(candidate.slug)) continue;
      if (!validString(candidate.title)) continue;
      if (candidate.icon !== null && candidate.icon !== undefined && !validString(candidate.icon)) continue;
      const url = validatedWidgetUrl(candidate.url, candidate.slug);
      if (url === null || bySlug.has(candidate.slug)) continue;
      bySlug.set(candidate.slug, {
        slug: candidate.slug,
        title: candidate.title,
        icon: candidate.icon || null,
        url,
      });
    }

    return Array.from(bySlug.values()).sort((left, right) => {
      const titleOrder = left.title.localeCompare(right.title, undefined, { sensitivity: 'base' });
      return titleOrder || left.slug.localeCompare(right.slug);
    });
  }

  async function fetchRegistry() {
    try {
      const response = await fetch(REGISTRY_URL, { cache: 'no-store' });
      if (!response.ok) return null;
      return normalizeRegistry(await response.json());
    } catch (_) {
      return null;
    }
  }

  function showState(title, detail) {
    if (!_state) return;
    _state.hidden = false;
    _stateTitle.textContent = title;
    _stateDetail.textContent = detail || '';
    _stateDetail.hidden = !detail;
  }

  function hideState() {
    if (_state) _state.hidden = true;
  }

  function updateActiveRow() {
    for (const [slug, row] of _rows) {
      const selected = slug === _selectedSlug;
      row.classList.toggle('is-active', selected);
      row.setAttribute('aria-pressed', selected ? 'true' : 'false');
    }
  }

  function selectWidget(widget, userInitiated) {
    _selectedSlug = widget.slug;
    storeSelectedSlug(widget.slug);
    updateActiveRow();
    hideState();
    _frame.hidden = false;
    _frame.setAttribute('title', 'Widget: ' + widget.title);
    if (_frameUrl !== widget.url) {
      _frame.setAttribute('src', widget.url);
      _frameUrl = widget.url;
    }
    if (userInitiated && typeof window._closeMobileSidebarAfterPanelSelection === 'function') {
      window._closeMobileSidebarAfterPanelSelection();
    }
  }

  function clearFrame() {
    _selectedSlug = null;
    storeSelectedSlug(null);
    updateActiveRow();
    if (!_frame) return;
    _frame.hidden = true;
    _frame.setAttribute('title', 'Widget');
    if (_frameUrl !== null) {
      _frame.setAttribute('src', 'about:blank');
      _frameUrl = null;
    }
  }

  function sameRegistry(widgets) {
    if (!_hasGoodRegistry || widgets.length !== _widgets.length) return false;
    return widgets.every((widget, index) => {
      const current = _widgets[index];
      return widget.slug === current.slug
        && widget.title === current.title
        && widget.icon === current.icon
        && widget.url === current.url;
    });
  }

  function focusedRowSlug() {
    const activeElement = document.activeElement;
    for (const [slug, row] of _rows) {
      if (row === activeElement) return slug;
    }
    return null;
  }

  function restoreFocusedRow(slug) {
    if (slug === null) return;
    const row = _rows.get(slug);
    if (row) row.focus({ preventScroll: true });
  }

  function renderRegistry(widgets) {
    if (sameRegistry(widgets)) return;
    const focusedSlug = focusedRowSlug();
    _hasGoodRegistry = true;
    _widgets = widgets;
    _rows = new Map();
    _list.textContent = '';

    for (const widget of widgets) {
      const row = elem('button', {
        type: 'button',
        class: 'humr-widget-row',
        dataset: { slug: widget.slug },
        'aria-pressed': 'false',
        onclick: () => selectWidget(widget, true),
      }, [
        widgetIcon(widget.icon),
        elem('span', { class: 'humr-widget-row-title' }, [widget.title]),
      ]);
      _rows.set(widget.slug, row);
      _list.appendChild(row);
    }

    if (widgets.length === 0) {
      clearFrame();
      showState('No widgets yet', 'Ask your agent to create one in chat.');
      return;
    }

    const selected = widgets.find((widget) => widget.slug === _selectedSlug) || widgets[0];
    selectWidget(selected, false);
    restoreFocusedRow(focusedSlug);
  }

  function isPanelActive() {
    const main = document.querySelector('main.main');
    return !!(main && main.classList.contains('showing-widgets'));
  }

  async function refresh(session) {
    const request = ++_latestRequest;
    if (!_hasGoodRegistry) showState('Loading widgets…', '');
    const widgets = await fetchRegistry();
    if (!_active || session !== _session || request !== _latestRequest) return;
    if (widgets === null) {
      if (!_hasGoodRegistry) {
        showState('Couldn’t load widgets', 'We’ll try again.');
      }
      return;
    }
    renderRegistry(widgets);
  }

  function schedulePoll(session) {
    if (!_active || session !== _session || _pollTimer !== null || !isPanelActive()) return;
    _pollTimer = setTimeout(async () => {
      _pollTimer = null;
      if (!_active || session !== _session || !isPanelActive()) return;
      await refresh(session);
      schedulePoll(session);
    }, POLL_MS);
  }

  function stopPolling() {
    _active = false;
    _session += 1;
    if (_pollTimer !== null) {
      clearTimeout(_pollTimer);
      _pollTimer = null;
    }
  }

  function populateMainView(view) {
    _stateTitle = elem('div', { class: 'humr-widget-state-title' }, ['Loading widgets…']);
    _stateDetail = elem('p', { class: 'humr-widget-state-detail', hidden: '' });
    _state = elem('div', {
      class: 'humr-widget-state',
      id: 'humrWidgetsState',
      role: 'status',
      'aria-live': 'polite',
    }, [_stateTitle, _stateDetail]);

    _frame = elem('iframe', {
      class: 'humr-widget-frame',
      id: 'humrWidgetsFrame',
      title: 'Widget',
      hidden: '',
    });

    view.appendChild(elem('div', { class: 'humr-widget-shell' }, [_state, _frame]));
  }

  function populateLeftPane(pane) {
    _list = elem('div', {
      class: 'humr-widget-list',
      id: 'humrWidgetsList',
      'aria-label': 'Widgets',
    });
    pane.appendChild(_list);
  }

  // Four-tile mark, shared with the generic per-Widget fallback icon.
  const WIDGETS_ICON = '<rect x="3" y="3" width="7" height="7" rx="1"/>'
    + '<rect x="14" y="3" width="7" height="7" rx="1"/>'
    + '<rect x="3" y="14" width="7" height="7" rx="1"/>'
    + '<rect x="14" y="14" width="7" height="7" rx="1"/>';

  window.HumrPanel.register({
    id: 'widgets',
    title: 'Widgets',
    icon: WIDGETS_ICON,
    viewClass: 'humr-widget-page',
    populateMainView,
    populateLeftPane,
    async onShow() {
      _active = true;
      _session += 1;
      const session = _session;
      if (_pollTimer !== null) {
        clearTimeout(_pollTimer);
        _pollTimer = null;
      }
      await refresh(session);
      schedulePoll(session);
    },
    onHide: stopPolling,
  });
})();
