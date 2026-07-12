'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const extensionDir = path.resolve(__dirname, '..');

function loadScript(context, fileName) {
  const source = fs.readFileSync(path.join(extensionDir, fileName), 'utf8');
  vm.runInContext(source, context, { filename: fileName });
}

function createBrowserContext({ modelsReadyAfter = 3 } = {}) {
  const events = [];
  let modelAttempts = 0;
  let now = 0;
  class TestDate extends Date {
    static now() { return now; }
  }
  const window = {
    location: {
      origin: 'https://agent.example.test',
      pathname: '/',
      search: '',
      hash: '',
    },
    history: { replaceState() {} },
  };
  window._refreshModelDropdownsAfterProviderChange = () => {
    events.push('models');
    window._modelDropdownReady = Promise.resolve().then(() => events.push('models-ready'));
  };
  const document = {
    getElementById() { return null; },
    createElement() { throw new Error('DOM creation is not expected in this test'); },
  };
  const context = vm.createContext({
    URLSearchParams,
    alert(message) { events.push('alert:' + message); },
    console,
    document,
    fetch: async (url) => {
      if (String(url).startsWith('/extensions/humr/humr-integrations.css')) {
        return { ok: true };
      }
      if (url === '/api/models') {
        events.push('models-probe');
        modelAttempts += 1;
        if (modelAttempts < modelsReadyAfter) {
          if (modelAttempts === 1) throw new Error('model backend restarting');
          return { ok: false };
        }
        return { ok: true, json: async () => ({ groups: [] }) };
      }
      throw new Error('Unexpected fetch: ' + url);
    },
    requestAnimationFrame(callback) { callback(); },
    setTimeout(callback, delay = 0) { now += delay; callback(); },
    Date: TestDate,
    window,
  });
  return { context, events, window };
}

test('split integration scripts load in manifest order', () => {
  const { context, window } = createBrowserContext();
  let registration = null;
  window.HumrPanel = { register(value) { registration = value; } };

  for (const fileName of [
    'humr-integrations-runtime.js',
    'humr-integrations-connection-flows.js',
    'humr-integrations-google.js',
    'humr-integrations-slack.js',
    'humr-integrations-merge.js',
    'humr-integrations.js',
  ]) {
    loadScript(context, fileName);
  }

  assert.equal(registration.id, 'integrations');
  assert.equal(window.HumrIntegrations.flows, undefined);
  assert.deepEqual(
    Array.from(window.HumrIntegrations.loadedExtensionScripts),
    ['runtime', 'connection-flows', 'google', 'slack', 'merge'],
  );
});

test('configurable cards reuse the connection action', () => {
  const { context, window } = createBrowserContext();
  loadScript(context, 'humr-integrations-runtime.js');
  loadScript(context, 'humr-integrations-connection-flows.js');
  loadScript(context, 'humr-integrations-slack.js');

  const cardSpec = window.HumrIntegrations.cardSpecs.resolve({
    kind: 'tls_intercept',
    slug: 'slack',
    label: 'Slack',
    status: 'connected',
    category: 'connector',
    connect_mode: 'vault',
    affects_model_picker: false,
    metadata: {},
  });

  assert.equal(cardSpec.canConfigure, true);
  assert.equal(typeof cardSpec.connect, 'function');
  assert.equal(typeof cardSpec.disconnect, 'function');
  assert.equal('configure' in cardSpec, false);
  assert.equal(window.HumrIntegrations.cardSpecs.createTls, undefined);
});

test('connect outcomes settle once', async () => {
  const { context, window } = createBrowserContext();
  loadScript(context, 'humr-integrations-runtime.js');

  const connectOutcome = window.HumrIntegrations.cardActions.createConnectOutcome();
  connectOutcome.finish('changed');
  connectOutcome.finish('cancelled');

  assert.equal((await connectOutcome.promise).outcome, 'changed');
});

test('model readiness timeout does not invoke the Hermes refresh hook', async () => {
  const { context, events, window } = createBrowserContext({ modelsReadyAfter: Infinity });
  loadScript(context, 'humr-integrations-runtime.js');

  await window.HumrIntegrations.webui.refreshModelDropdowns();

  assert.equal(events.includes('models'), false);
  assert.equal(events.filter((event) => event === 'models-probe').length > 1, true);
});

test('card actions reconcile only changed outcomes', async () => {
  const { context, events, window } = createBrowserContext();
  loadScript(context, 'humr-integrations-runtime.js');
  const { cardActions, page } = window.HumrIntegrations;
  page.configure({
    rerender() { events.push('render'); },
    refreshAndRender() { events.push('catalog'); },
  });

  const changedCard = {
    key: 'changed',
    affectsModelPicker: true,
    async connect() {
      events.push('provider');
      return { outcome: 'changed' };
    },
  };
  await cardActions.connect(changedCard);
  assert.deepEqual(events, [
    'render',
    'provider',
    'catalog',
    'models-probe',
    'models-probe',
    'models-probe',
    'models',
    'models-ready',
    'render',
  ]);
  assert.equal(cardActions.isConnecting(changedCard), false);

  events.length = 0;
  const cancelledCard = {
    key: 'cancelled',
    affectsModelPicker: true,
    async connect() { return { outcome: 'cancelled' }; },
  };
  await cardActions.connect(cancelledCard);
  assert.deepEqual(events, ['render', 'render']);
  assert.equal(cardActions.isConnecting(cancelledCard), false);

  events.length = 0;
  const navigatingCard = {
    key: 'navigating',
    affectsModelPicker: true,
    connect() { return { outcome: 'navigating' }; },
  };
  await cardActions.connect(navigatingCard);
  assert.deepEqual(events, ['render']);
  assert.equal(cardActions.isConnecting(navigatingCard), true);

  events.length = 0;
  const disconnectingCard = {
    key: 'disconnecting',
    affectsModelPicker: false,
    async disconnect() { events.push('provider'); },
  };
  await cardActions.disconnect(disconnectingCard);
  assert.deepEqual(events, ['render', 'provider', 'catalog', 'render']);
});
