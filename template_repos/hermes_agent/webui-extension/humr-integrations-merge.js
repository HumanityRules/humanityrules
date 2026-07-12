// HUMR Merge integration: connector authentication flow and adapter registration.
(() => {
  'use strict';

  const { util, connectors } = window.HumrIntegrations;
  const { elem } = util;

  // ── Merge connector flow ──────────────────────────────────────────

  function showMergeWaitingModal(item, onCancel) {
    const backdrop = elem('div', { class: 'humr-modal-backdrop' });
    const modal = elem('div', { class: 'humr-modal' }, [
      elem('div', { class: 'humr-modal-title' }, ['Waiting for ' + item.label + '…']),
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

  async function startMergeConnect(item, ctx, revert) {
    const revertOnce = () => { if (revert) { revert(); revert = null; } };
    let resp;
    try {
      resp = await fetch('/__humr_broker/integrations/merge/link-token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connector_slug: item.slug }),
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
    const waiting = showMergeWaitingModal(item, () => { stopped = true; revertOnce(); });
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
          '/__humr_broker/integrations/merge/connector-status?connector_slug=' + encodeURIComponent(item.slug),
          { cache: 'no-store' },
        );
        if (r.ok) {
          const s = await r.json();
          if (s.status === 'connected') {
            stopped = true;
            waiting.remove();
            await ctx.refreshAndRender();
            return;
          }
        }
      } catch (_) { /* keep polling */ }
      setTimeout(tick, intervalMs);
    };
    setTimeout(tick, intervalMs);
  }

  connectors.register('merge_connector', {
    cardKey(item) {
      return 'merge:' + item.slug;
    },
    connect(item, ctx, revert) {
      startMergeConnect(item, ctx, revert);
    },
    async disconnect(item, ctx) {
      await fetch('/__humr_broker/integrations/merge/disconnect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ connector_slug: item.slug }),
      });
      await ctx.refreshAndRender();
    },
  });
  window.HumrIntegrations.loadedExtensionScripts.add('merge');
})();
