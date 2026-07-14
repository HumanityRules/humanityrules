// HUMR Merge integration: connector authentication flow and card specification.
(() => {
  'use strict';

  const { util, modals, cardSpecs } = window.HumrIntegrations;
  const { elem, throwForErrorResponse } = util;
  const { openConnectModal } = modals;

  // ── Merge connector flow ──────────────────────────────────────────

  async function startMergeConnect(integration) {
    let resp;
    try {
      resp = await fetch('/__humr_broker/integrations/merge/link-token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connector_slug: integration.slug }),
      });
    } catch (_) {
      alert('Could not reach the integrations broker. Try again.');
      return { outcome: 'cancelled' };
    }
    if (!resp.ok) {
      alert('Merge link-token request failed.');
      return { outcome: 'cancelled' };
    }
    const data = await resp.json();
    if (!data.magic_link_url) {
      alert('Merge did not return a magic link.');
      return { outcome: 'cancelled' };
    }
    window.open(data.magic_link_url, '_blank');

    let stopped = false;
    return openConnectModal({
      title: 'Waiting for ' + integration.label + '…',
      onCancelled: () => { stopped = true; },
    }, ({ close }) => {
      const start = Date.now();
      const intervalMs = 3000;
      const timeoutMs = 30 * 60 * 1000;
      const tick = async () => {
        if (stopped) return;
        if (Date.now() - start > timeoutMs) {
          close();
          return;
        }
        try {
          const r = await fetch(
            '/__humr_broker/integrations/merge/connector-status?connector_slug=' + encodeURIComponent(integration.slug),
            { cache: 'no-store' },
          );
          if (r.ok) {
            const s = await r.json();
            if (s.status === 'connected') {
              stopped = true;
              close('changed');
              return;
            }
          }
        } catch (_) { /* keep polling */ }
        setTimeout(tick, intervalMs);
      };
      setTimeout(tick, intervalMs);

      return [
        elem('div', { class: 'humr-modal-body' }, [
          'Complete authentication in the tab that just opened. When Merge confirms, ' +
          'this dialog closes automatically.',
        ]),
        elem('div', { class: 'humr-modal-actions' }, [
          elem('button', { class: 'humr-integration-btn', onclick: close }, ['Cancel']),
        ]),
      ];
    });
  }

  cardSpecs.register({ kind: 'merge_connector' }, (integration) => {
    return {
      details: [],
      connect() {
        return startMergeConnect(integration);
      },
      async disconnect() {
        const response = await fetch('/__humr_broker/integrations/merge/disconnect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ connector_slug: integration.slug }),
        });
        await throwForErrorResponse(response, 'Disconnect failed. Please try again.');
      },
    };
  });
  window.HumrIntegrations.loadedExtensionScripts.add('merge');
})();
