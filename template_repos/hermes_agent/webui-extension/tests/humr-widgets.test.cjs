'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const extensionDir = path.resolve(__dirname, '..');
const widgetsScript = fs.readFileSync(path.join(extensionDir, 'humr-widgets.js'), 'utf8');

class FakeClassList {
  constructor(element) {
    this.element = element;
    this.values = new Set();
  }

  setFromAttribute(value) {
    this.values = new Set(String(value).split(/\s+/).filter(Boolean));
  }

  add(...names) {
    for (const name of names) this.values.add(name);
    this.sync();
  }

  remove(...names) {
    for (const name of names) this.values.delete(name);
    this.sync();
  }

  toggle(name, force) {
    const enabled = force === undefined ? !this.values.has(name) : !!force;
    if (enabled) this.values.add(name);
    else this.values.delete(name);
    this.sync();
    return enabled;
  }

  contains(name) {
    return this.values.has(name);
  }

  sync() {
    this.element.attributes.set('class', Array.from(this.values).join(' '));
  }
}

class FakeTextNode {
  constructor(text) {
    this.nodeType = 3;
    this.textContent = String(text);
    this.parentNode = null;
  }
}

class FakeElement {
  constructor(tagName, namespace = null, ownerDocument = null) {
    this.nodeType = 1;
    this.tagName = String(tagName).toUpperCase();
    this.namespaceURI = namespace;
    this.ownerDocument = ownerDocument;
    this.parentNode = null;
    this.children = [];
    this.attributes = new Map();
    this.dataset = {};
    this.style = {};
    this._hidden = false;
    this.disabled = false;
    this.listeners = new Map();
    this.classList = new FakeClassList(this);
    this._text = '';
    this.innerHtmlAssignments = [];
    this.srcAssignments = [];
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  contains(node) {
    if (node === this) return true;
    return this.children.some((child) => child.nodeType === 1 && child.contains(node));
  }

  get hidden() {
    return this._hidden;
  }

  set hidden(value) {
    this._hidden = !!value;
    if (this._hidden) this.attributes.set('hidden', '');
    else this.attributes.delete('hidden');
  }

  setAttribute(name, value) {
    const stringValue = String(value);
    this.attributes.set(name, stringValue);
    if (name === 'class') this.classList.setFromAttribute(stringValue);
    if (name === 'hidden') this.hidden = true;
    if (name === 'src') this.srcAssignments.push(stringValue);
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  addEventListener(name, listener) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(listener);
  }

  click() {
    for (const listener of this.listeners.get('click') || []) listener({ type: 'click', currentTarget: this });
  }

  focus() {
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    const results = [];
    for (const child of this.children) {
      if (child.nodeType !== 1) continue;
      if (matchesSelector(child, selector)) results.push(child);
      results.push(...child.querySelectorAll(selector));
    }
    return results;
  }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join('');
  }

  set textContent(value) {
    if (this.ownerDocument && this.contains(this.ownerDocument.activeElement)) {
      this.ownerDocument.activeElement = this.ownerDocument.body;
    }
    this._text = String(value);
    this.children = [];
  }

  get innerHTML() {
    return '';
  }

  set innerHTML(value) {
    this.innerHtmlAssignments.push(String(value));
    this.textContent = '';
  }
}

function matchesSelector(element, selector) {
  if (selector.startsWith('#')) return element.getAttribute('id') === selector.slice(1);
  const [tag, className] = selector.split('.');
  if (tag && element.tagName !== tag.toUpperCase()) return false;
  return !className || element.classList.contains(className);
}

class FakeDocument {
  constructor() {
    this.body = new FakeElement('body', null, this);
    this.activeElement = this.body;
  }

  createElement(tagName) {
    return new FakeElement(tagName, null, this);
  }

  createElementNS(namespace, tagName) {
    return new FakeElement(tagName, namespace, this);
  }

  createTextNode(text) {
    return new FakeTextNode(text);
  }

  getElementById(id) {
    return this.body.querySelector('#' + id);
  }

  querySelector(selector) {
    return this.body.querySelector(selector);
  }

  querySelectorAll(selector) {
    return this.body.querySelectorAll(selector);
  }
}

class FakeStorage {
  constructor(initial = {}) {
    this.values = new Map(Object.entries(initial));
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.values.set(key, String(value));
  }

  removeItem(key) {
    this.values.delete(key);
  }
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function response(payload, ok = true) {
  return { ok, json: async () => payload };
}

function createHarness({ mobileCloseHelper = false, storedSlug = null } = {}) {
  const document = new FakeDocument();
  const registrations = [];
  const fetchCalls = [];
  const fetchQueue = [];
  const mobileCloseEvents = [];
  const timers = new Map();
  let nextTimerId = 1;

  const main = document.createElement('main');
  main.setAttribute('class', 'main showing-widgets');
  document.body.appendChild(main);

  function elem(tag, props, children) {
    const element = document.createElement(tag);
    if (props) {
      for (const [name, value] of Object.entries(props)) {
        if (name === 'style' && typeof value === 'object') Object.assign(element.style, value);
        else if (name === 'dataset' && typeof value === 'object') Object.assign(element.dataset, value);
        else if (name.startsWith('on') && typeof value === 'function') element.addEventListener(name.slice(2), value);
        else if (name === 'disabled') element.disabled = !!value;
        else element.setAttribute(name, value);
      }
    }
    for (const child of children || []) {
      if (child == null) continue;
      element.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    }
    return element;
  }

  const window = {
    location: { origin: 'https://agent.example.test' },
    localStorage: new FakeStorage(storedSlug === null ? {} : { 'humr.widgets.selectedSlug': storedSlug }),
    HumrPanel: {
      elem,
      register(spec) { registrations.push(spec); },
    },
  };
  if (mobileCloseHelper) {
    window._closeMobileSidebarAfterPanelSelection = () => mobileCloseEvents.push('close');
  }

  const context = vm.createContext({
    URL,
    console,
    document,
    window,
    fetch(url, options) {
      fetchCalls.push({ url, options });
      if (fetchQueue.length === 0) throw new Error('Unexpected fetch: ' + url);
      const next = fetchQueue.shift();
      return typeof next === 'function' ? next() : next;
    },
    setTimeout(callback, delay) {
      const id = nextTimerId;
      nextTimerId += 1;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  });
  vm.runInContext(widgetsScript, context, { filename: 'humr-widgets.js' });
  assert.equal(registrations.length, 1);

  const registration = registrations[0];
  const view = document.createElement('section');
  const pane = document.createElement('aside');
  registration.populateMainView(view);
  registration.populateLeftPane(pane);
  main.appendChild(view);
  document.body.appendChild(pane);

  return {
    document,
    fetchCalls,
    fetchQueue,
    main,
    mobileCloseEvents,
    pane,
    registration,
    registrations,
    timers,
    view,
    window,
    async runNextTimer() {
      const first = timers.entries().next();
      assert.equal(first.done, false, 'expected a scheduled timer');
      const [id, timer] = first.value;
      timers.delete(id);
      await timer.callback();
    },
  };
}

function registry(widgets) {
  return { schema_version: 1, widgets };
}

function widget(slug, title, icon = null) {
  return { slug, title, icon, url: '/widgets/' + slug + '/' };
}

function rowSlugs(harness) {
  return harness.document.getElementById('humrWidgetsList').children.map((row) => row.dataset.slug);
}

test('manifest loads one Widgets panel immediately before Web Apps', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(extensionDir, 'manifest.json'), 'utf8'));
  const ids = manifest.extensions.map((entry) => entry.id);
  const widgetsIndex = ids.indexOf('humr-widgets');

  assert.equal(ids.filter((id) => id === 'humr-widgets').length, 1);
  assert.equal(ids[widgetsIndex + 1], 'humr-webapps');
  assert.deepEqual(manifest.extensions[widgetsIndex].scripts, ['humr-panel.js', 'humr-widgets.js']);
  assert.deepEqual(manifest.extensions[widgetsIndex].stylesheets, ['humr-widgets.css']);

  const harness = createHarness();
  assert.equal(harness.registration.id, 'widgets');
  assert.equal(harness.registration.title, 'Widgets');
  const frames = harness.document.querySelectorAll('iframe');
  assert.equal(frames.length, 1);
  assert.equal(frames[0].hasAttribute('sandbox'), false);
  assert.equal(frames[0].getAttribute('title'), 'Widget');
});

test('sidebar list owns a flexing vertical scroll area', () => {
  const css = fs.readFileSync(path.join(extensionDir, 'humr-widgets.css'), 'utf8');
  const listRule = /\.humr-widget-list\s*\{([^}]*)\}/.exec(css);

  assert.notEqual(listRule, null);
  assert.match(listRule[1], /flex:\s*1 1 auto/);
  assert.match(listRule[1], /min-height:\s*0/);
  assert.match(listRule[1], /overflow-y:\s*auto/);
});

test('show fetches immediately and owns one polling chain only while active', async () => {
  const harness = createHarness();
  harness.fetchQueue.push(Promise.resolve(response(registry([]))));

  const showing = harness.registration.onShow();
  assert.equal(harness.fetchCalls.length, 1);
  assert.equal(harness.fetchCalls[0].url, '/widgets/__admin/registry.json');
  assert.equal(harness.fetchCalls[0].options.cache, 'no-store');
  assert.deepEqual(Object.keys(harness.fetchCalls[0].options), ['cache']);
  await showing;
  assert.equal(harness.timers.size, 1);

  harness.fetchQueue.push(Promise.resolve(response(registry([]))));
  await harness.runNextTimer();
  assert.equal(harness.fetchCalls.length, 2);
  assert.equal(harness.timers.size, 1);

  harness.registration.onHide();
  assert.equal(harness.timers.size, 0);
});

test('a stale show fetch cannot overwrite a newer panel session or duplicate polling', async () => {
  const harness = createHarness();
  const first = deferred();
  const second = deferred();
  harness.fetchQueue.push(() => first.promise, () => second.promise);

  const staleShow = harness.registration.onShow();
  harness.registration.onHide();
  const currentShow = harness.registration.onShow();
  second.resolve(response(registry([widget('current-widget', 'Current')])));
  await currentShow;
  assert.deepEqual(rowSlugs(harness), ['current-widget']);
  assert.equal(harness.timers.size, 1);

  first.resolve(response(registry([widget('stale-widget', 'Stale')])));
  await staleShow;
  assert.deepEqual(rowSlugs(harness), ['current-widget']);
  assert.equal(harness.timers.size, 1);
});

test('valid items are sorted and registry text or icon values never become markup', async () => {
  const harness = createHarness();
  harness.fetchQueue.push(Promise.resolve(response(registry([
    widget('bravo-widget', 'Bravo', 'chart-bar'),
    widget('alpha-two', 'alpha', 'constructor'),
    widget('alpha-one', 'Alpha', '<svg onload=alert(1)>'),
    widget('unsafe-title', 'Unsafe <img src=x onerror=alert(1)>', null),
    { slug: 'wrong-url', title: 'Wrong URL', icon: null, url: 'https://evil.example/widget/' },
    { slug: '__admin', title: 'Reserved', icon: null, url: '/widgets/__admin/' },
  ]))));

  await harness.registration.onShow();
  assert.deepEqual(rowSlugs(harness), ['alpha-one', 'alpha-two', 'bravo-widget', 'unsafe-title']);

  const list = harness.document.getElementById('humrWidgetsList');
  const unsafeTitle = list.children[3].querySelector('span.humr-widget-row-title');
  assert.equal(unsafeTitle.textContent, 'Unsafe <img src=x onerror=alert(1)>');
  assert.deepEqual(unsafeTitle.innerHtmlAssignments, []);
  assert.equal(list.children[0].querySelector('svg').children.length, 4);
  assert.equal(list.children[1].querySelector('svg').children.length, 4);
  assert.equal(list.children[2].querySelector('svg').children.length, 1);

  const attributeValues = harness.document.querySelectorAll('svg')
    .flatMap((svg) => Array.from(svg.attributes.values()));
  assert.equal(attributeValues.includes('<svg onload=alert(1)>'), false);
  assert.equal(attributeValues.includes('constructor'), false);
});

test('selection persists, falls back after deletion, and reuses the same iframe without reloads', async () => {
  const harness = createHarness({ mobileCloseHelper: true, storedSlug: 'beta-widget' });
  const both = registry([
    widget('alpha-widget', 'Alpha'),
    widget('beta-widget', 'Beta'),
  ]);
  harness.fetchQueue.push(Promise.resolve(response(both)));
  await harness.registration.onShow();

  const frame = harness.document.getElementById('humrWidgetsFrame');
  assert.equal(frame.getAttribute('src'), '/widgets/beta-widget/');
  assert.equal(frame.getAttribute('title'), 'Widget: Beta');
  assert.deepEqual(frame.srcAssignments, ['/widgets/beta-widget/']);
  assert.deepEqual(harness.mobileCloseEvents, []);

  const originalBetaRow = harness.document.getElementById('humrWidgetsList').children[1];
  originalBetaRow.focus();
  harness.fetchQueue.push(Promise.resolve(response(both)));
  await harness.runNextTimer();
  assert.strictEqual(harness.document.getElementById('humrWidgetsFrame'), frame);
  assert.strictEqual(harness.document.getElementById('humrWidgetsList').children[1], originalBetaRow);
  assert.strictEqual(harness.document.activeElement, originalBetaRow);
  assert.deepEqual(frame.srcAssignments, ['/widgets/beta-widget/']);

  harness.fetchQueue.push(Promise.resolve(response(registry([
    widget('alpha-widget', 'Alpha'),
    widget('beta-widget', 'Beta'),
    widget('gamma-widget', 'Gamma'),
  ]))));
  await harness.runNextTimer();
  const replacementBetaRow = harness.document.getElementById('humrWidgetsList').children[1];
  assert.notStrictEqual(replacementBetaRow, originalBetaRow);
  assert.strictEqual(harness.document.activeElement, replacementBetaRow);
  assert.deepEqual(frame.srcAssignments, ['/widgets/beta-widget/']);

  const alphaRow = harness.document.getElementById('humrWidgetsList').children[0];
  alphaRow.focus();
  alphaRow.click();
  assert.equal(alphaRow.classList.contains('is-active'), true);
  assert.equal(harness.window.localStorage.getItem('humr.widgets.selectedSlug'), 'alpha-widget');
  assert.deepEqual(frame.srcAssignments, ['/widgets/beta-widget/', '/widgets/alpha-widget/']);
  assert.deepEqual(harness.mobileCloseEvents, ['close']);

  harness.fetchQueue.push(Promise.resolve(response(registry([widget('beta-widget', 'Beta')]))));
  await harness.runNextTimer();
  assert.equal(harness.window.localStorage.getItem('humr.widgets.selectedSlug'), 'beta-widget');
  assert.strictEqual(harness.document.activeElement, harness.document.body);
  assert.deepEqual(harness.mobileCloseEvents, ['close']);
  assert.deepEqual(frame.srcAssignments, [
    '/widgets/beta-widget/',
    '/widgets/alpha-widget/',
    '/widgets/beta-widget/',
  ]);

  harness.registration.onHide();
  assert.equal(frame.hidden, false);
  harness.fetchQueue.push(Promise.resolve(response(registry([widget('beta-widget', 'Beta')]))));
  await harness.registration.onShow();
  assert.strictEqual(harness.document.getElementById('humrWidgetsFrame'), frame);
  assert.equal(frame.getAttribute('src'), '/widgets/beta-widget/');
  assert.equal(frame.srcAssignments.length, 3);
  assert.deepEqual(harness.mobileCloseEvents, ['close']);

  harness.fetchQueue.push(Promise.resolve(response(registry([]))));
  await harness.runNextTimer();
  assert.equal(frame.hidden, true);
  assert.equal(frame.getAttribute('src'), 'about:blank');
  assert.equal(harness.window.localStorage.getItem('humr.widgets.selectedSlug'), null);
  assert.deepEqual(harness.mobileCloseEvents, ['close']);
});

test('loading, unavailable, empty, and last-good behavior are safe', async () => {
  const harness = createHarness();
  const pending = deferred();
  harness.fetchQueue.push(() => pending.promise);
  const showing = harness.registration.onShow();

  const state = harness.document.getElementById('humrWidgetsState');
  const frame = harness.document.getElementById('humrWidgetsFrame');
  assert.equal(state.textContent, 'Loading widgets…');
  pending.resolve(response(null, false));
  await showing;
  assert.equal(state.textContent, 'Couldn’t load widgetsWe’ll try again.');
  assert.equal(frame.hidden, true);

  harness.fetchQueue.push(Promise.resolve(response(registry([]))));
  await harness.runNextTimer();
  assert.equal(state.textContent, 'No widgets yetAsk your agent to create one in chat.');
  assert.equal(frame.hidden, true);
  assert.equal(frame.hasAttribute('src'), false);

  harness.fetchQueue.push(Promise.resolve(response(registry([widget('safe-widget', 'Safe')]))));
  await harness.runNextTimer();
  assert.equal(frame.hidden, false);
  assert.equal(frame.getAttribute('src'), '/widgets/safe-widget/');

  harness.fetchQueue.push(Promise.resolve(response({ schema_version: 99, widgets: [] })));
  await harness.runNextTimer();
  assert.deepEqual(rowSlugs(harness), ['safe-widget']);
  assert.equal(frame.getAttribute('src'), '/widgets/safe-widget/');
  assert.deepEqual(frame.srcAssignments, ['/widgets/safe-widget/']);
});
