'use strict';

// Tests for the sidebar credits card: the three states it renders, the facts
// it refuses to invent, and how it polls its own broker.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const extensionDir = path.resolve(__dirname, '..');
const creditsScript = fs.readFileSync(path.join(extensionDir, 'humr-credits.js'), 'utf8');

class FakeClassList {
  constructor(element) {
    this.element = element;
    this.values = new Set();
  }

  setFromAttribute(value) {
    this.values = new Set(String(value).split(/\s+/).filter(Boolean));
  }

  contains(name) {
    return this.values.has(name);
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
  constructor(tagName, ownerDocument) {
    this.nodeType = 1;
    this.tagName = String(tagName).toUpperCase();
    this.ownerDocument = ownerDocument;
    this.parentNode = null;
    this.children = [];
    this.attributes = new Map();
    this.dataset = {};
    this.style = {};
    this._hidden = false;
    this.listeners = new Map();
    this.classList = new FakeClassList(this);
    this._text = '';
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
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
    this._text = String(value);
    this.children = [];
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
    this.body = new FakeElement('body', this);
    this.visibilityState = 'visible';
    this.listeners = new Map();
  }

  createElement(tagName) {
    return new FakeElement(tagName, this);
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

  addEventListener(name, listener) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(listener);
  }

  async dispatch(name) {
    for (const listener of this.listeners.get(name) || []) await listener({ type: name });
  }
}

function response(payload, ok = true) {
  return { ok, json: async () => payload };
}

function entitlement(overrides) {
  return {
    entitlement: {
      credits_remaining: 1200,
      monthly_grant: 2000,
      renewal_date: null,
      plan: 'operator',
      exhausted: false,
      ...(overrides || {}),
    },
    upgrade_url: 'https://humr.example/settings/billing/',
  };
}

// Boots the script against a fake shell. `withSidebar: false` withholds the
// sidebar so the mount-retry loop can be observed.
function createHarness(options) {
  const { withSidebar = true } = options || {};
  const document = new FakeDocument();
  const fetchCalls = [];
  const fetchQueue = [];
  const timers = new Map();
  const frameCallbacks = [];
  let nextTimerId = 1;

  const sidebar = document.createElement('aside');
  sidebar.setAttribute('class', 'sidebar');
  if (withSidebar) document.body.appendChild(sidebar);

  const context = vm.createContext({
    console,
    document,
    window: { location: { origin: 'https://agent.example.test' } },
    fetch(url, options) {
      fetchCalls.push({ url, options });
      if (fetchQueue.length === 0) throw new Error('Unexpected fetch: ' + url);
      const next = fetchQueue.shift();
      return typeof next === 'function' ? next() : next;
    },
    requestAnimationFrame(callback) {
      frameCallbacks.push(callback);
      return frameCallbacks.length;
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

  const harness = {
    document,
    sidebar,
    fetchCalls,
    fetchQueue,
    timers,
    frameCallbacks,
    attachSidebar() {
      document.body.appendChild(sidebar);
    },
    runFrame() {
      const callback = frameCallbacks.shift();
      assert.ok(callback, 'expected a scheduled animation frame');
      callback();
    },
    card() {
      return document.getElementById('humrCreditsCard');
    },
    part(className) {
      const card = harness.card();
      return card ? card.querySelector('.' + className) : null;
    },
    async runNextTimer() {
      const first = timers.entries().next();
      assert.equal(first.done, false, 'expected a scheduled timer');
      const [id, timer] = first.value;
      timers.delete(id);
      await timer.callback();
    },
    async settle() {
      // Let the mount's refresh().then(schedulePoll) chain run to completion.
      for (let index = 0; index < 8; index += 1) await Promise.resolve();
    },
  };

  vm.runInContext(creditsScript, context, { filename: 'humr-credits.js' });
  return harness;
}

// Mounts the card and renders one payload, returning the settled harness.
async function mounted(payload, options) {
  const harness = createHarness(options);
  harness.fetchQueue.push(response(payload));
  harness.runFrame();
  await harness.settle();
  return harness;
}

test('manifest ships the credits card as its own extension', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(extensionDir, 'manifest.json'), 'utf8'));
  const entry = manifest.extensions.find((candidate) => candidate.id === 'humr-credits');

  assert.ok(entry, 'humr-credits must be registered in the manifest');
  assert.deepEqual(entry.scripts, ['humr-credits.js']);
  assert.deepEqual(entry.stylesheets, ['humr-credits.css']);
});

test('the card sits at the bottom of the sidebar by flex order, not DOM position', () => {
  const css = fs.readFileSync(path.join(extensionDir, 'humr-credits.css'), 'utf8');

  assert.match(css, /\.sidebar > \.humr-credits-card\s*\{[^}]*order:\s*2/);
  assert.match(css, /\.sidebar > \.humr-credits-card\s*\{[^}]*flex-shrink:\s*0/);
});

test('mounting waits for the sidebar, then polls the broker same-origin', async () => {
  const harness = createHarness({ withSidebar: false });

  harness.runFrame();
  assert.equal(harness.card(), null);
  assert.equal(harness.fetchCalls.length, 0);

  harness.attachSidebar();
  harness.fetchQueue.push(response(entitlement()));
  harness.runFrame();
  await harness.settle();

  assert.equal(harness.fetchCalls[0].url, '/__humr_broker/billing');
  assert.equal(harness.fetchCalls[0].options.cache, 'no-store');
  assert.ok(harness.card());
});

test('a healthy balance renders a quiet meter and nothing else', async () => {
  const harness = await mounted(entitlement({ credits_remaining: 1200 }));

  assert.equal(harness.card().hidden, false);
  assert.equal(harness.card().dataset.state, 'ok');
  assert.equal(harness.part('humr-credits-label').textContent, 'Credits');
  assert.equal(harness.part('humr-credits-count').textContent, '1,200 / 2,000');
  assert.equal(harness.part('humr-credits-meter-fill').style.width, '60%');
  assert.equal(harness.part('humr-credits-note').hidden, true);
  assert.equal(harness.part('humr-credits-upgrade').hidden, true);
});

test('below the warning threshold the card says running low and offers upgrading', async () => {
  const harness = await mounted(entitlement({ credits_remaining: 300 }));

  assert.equal(harness.card().dataset.state, 'low');
  assert.equal(harness.part('humr-credits-note').textContent, 'Running low');
  assert.equal(harness.part('humr-credits-note').hidden, false);
  const upgrade = harness.part('humr-credits-upgrade');
  assert.equal(upgrade.hidden, false);
  assert.equal(upgrade.getAttribute('href'), 'https://humr.example/settings/billing/');
  assert.equal(upgrade.getAttribute('target'), '_blank');
  assert.equal(upgrade.getAttribute('rel'), 'noopener');
});

test('twenty percent remaining is not yet running low', async () => {
  const harness = await mounted(entitlement({ credits_remaining: 400 }));

  assert.equal(harness.card().dataset.state, 'ok');
});

test("a trial's exhausted state offers upgrading alone", async () => {
  const harness = await mounted(entitlement({ credits_remaining: -50, exhausted: true, plan: 'trial', renewal_date: null }));

  assert.equal(harness.card().dataset.state, 'out');
  assert.equal(harness.part('humr-credits-label').textContent, 'Out of credits');
  // Negative balances are a real state at the broker; the card floors them at zero.
  assert.equal(harness.part('humr-credits-count').textContent, '0 / 2,000');
  assert.equal(harness.part('humr-credits-meter-fill').style.width, '0%');
  assert.equal(harness.part('humr-credits-note').hidden, true);
  assert.equal(harness.part('humr-credits-upgrade').hidden, false);
});

test('a renewing plan adds the renewal date to the exhausted state', async () => {
  const harness = await mounted(entitlement({ credits_remaining: -200, exhausted: true, renewal_date: '2026-08-15' }));

  assert.equal(harness.part('humr-credits-note').hidden, false);
  assert.equal(harness.part('humr-credits-note').textContent, 'Credits renew on Aug 15, 2026');
});

test('the card stays hidden until HUMR has answered with real numbers', async () => {
  for (const payload of [
    { entitlement: null, upgrade_url: 'https://humr.example/settings/billing/' },
    { entitlement: { credits_remaining: null, monthly_grant: 2000, exhausted: false } },
    { entitlement: { credits_remaining: 10, monthly_grant: 'lots', exhausted: false } },
  ]) {
    const harness = await mounted(payload);
    assert.equal(harness.card().hidden, true, JSON.stringify(payload));
  }
});

test('a failed poll leaves the last good numbers on screen', async () => {
  const harness = await mounted(entitlement({ credits_remaining: 1200 }));

  harness.fetchQueue.push(response({ error: 'nope' }, false));
  await harness.runNextTimer();

  assert.equal(harness.part('humr-credits-count').textContent, '1,200 / 2,000');
  assert.equal(harness.card().hidden, false);
});

test('polling continues on an interval and repaints the new numbers', async () => {
  const harness = await mounted(entitlement({ credits_remaining: 1200 }));
  assert.equal(harness.timers.size, 1);

  harness.fetchQueue.push(response(entitlement({ credits_remaining: 40 })));
  await harness.runNextTimer();
  await harness.settle();

  assert.equal(harness.part('humr-credits-count').textContent, '40 / 2,000');
  assert.equal(harness.card().dataset.state, 'low');
  assert.equal(harness.timers.size, 1, 'the next poll must already be scheduled');
});

test('a hidden tab stops polling and a returning one refreshes at once', async () => {
  const harness = await mounted(entitlement({ credits_remaining: 1200 }));

  harness.document.visibilityState = 'hidden';
  await harness.document.dispatch('visibilitychange');
  assert.equal(harness.timers.size, 0);

  harness.document.visibilityState = 'visible';
  harness.fetchQueue.push(response(entitlement({ credits_remaining: 900 })));
  await harness.document.dispatch('visibilitychange');
  await harness.settle();

  assert.equal(harness.part('humr-credits-count').textContent, '900 / 2,000');
  assert.equal(harness.timers.size, 1);
});
