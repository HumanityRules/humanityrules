// HUMR Merge integration: connector authentication flow and card specification.
(() => {
  'use strict';

  const { page, util, flows, cardSpecs } = window.HumrIntegrations;
  const { elem } = util;
  const { throwForErrorResponse } = flows;

  // ── Merge connector flow ──────────────────────────────────────────

  function showMergeWaitingModal(integration, onCancel) {
    const backdrop = elem('div', { class: 'humr-modal-backdrop' });
    const modal = elem('div', { class: 'humr-modal' }, [
      elem('div', { class: 'humr-modal-title' }, ['Waiting for ' + integration.label + '…']),
      elem('div', { class: 'humr-modal-body' }, [
        'Complete authentication in the tab that just opened. When Merge confirms, ' +
        'this dialog closes automatically.',
      ]),
      elem('div', { class: 'humr-modal-actions' }, [
        elem('button', {
          class: 'humr-integration-btn',
          onclick: () => { backdrop.remove(); onCancel(); },
        }, ['Cancel']),
      ]),
    ]);
    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);
    return backdrop;
  }

  async function startMergeConnect(integration, revert) {
    const revertOnce = () => { if (revert) { revert(); revert = null; } };
    let resp;
    try {
      resp = await fetch('/__humr_broker/integrations/merge/link-token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connector_slug: integration.slug }),
      });
    } catch (_) {
      alert('Could not reach the integrations broker. Try again.');
      revertOnce();
      return;
    }
    if (!resp.ok) {
      alert('Merge link-token request failed.');
      revertOnce();
      return;
    }
    const data = await resp.json();
    if (!data.magic_link_url) {
      alert('Merge did not return a magic link.');
      revertOnce();
      return;
    }
    window.open(data.magic_link_url, '_blank');

    let stopped = false;
    const waiting = showMergeWaitingModal(integration, () => { stopped = true; revertOnce(); });
    const start = Date.now();
    const intervalMs = 3000;
    const timeoutMs = 30 * 60 * 1000;
    const tick = async () => {
      if (stopped) return;
      if (Date.now() - start > timeoutMs) {
        stopped = true;
        waiting.remove();
        revertOnce();
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
            waiting.remove();
            await page.refreshAndRender();
            return;
          }
        }
      } catch (_) { /* keep polling */ }
      setTimeout(tick, intervalMs);
    };
    setTimeout(tick, intervalMs);
  }

  cardSpecs.register({ kind: 'merge_connector' }, (integration) => {
    return {
      details: [],
      connect(revert) {
        startMergeConnect(integration, revert);
      },
      configure: null,
      async disconnect() {
        const response = await fetch('/__humr_broker/integrations/merge/disconnect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ connector_slug: integration.slug }),
        });
        await throwForErrorResponse(response, 'Disconnect failed. Please try again.');
        await page.refreshAndRender();
      },
    };
  });
  window.HumrIntegrations.loadedExtensionScripts.add('merge');
})();
