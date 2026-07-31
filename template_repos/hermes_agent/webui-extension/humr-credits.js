// HUMR WebUI extension: the credits card at the bottom of the sidebar
// (Lovable-style: a small rounded card under the navigation column's content).
// The sidebar is the one left-hand surface wide enough to say everything
// inline — the 140px rail is not — so there is no hover popover.
//
// One component driven by the entitlement snapshot the broker serves
// same-origin at /__humr_broker/billing (Caddy's /__humr_broker/* route). The
// broker enforces against the same snapshot, so what the card shows and what
// the proxy allows cannot disagree.
//
// The card leads with "Credits 0/500", then the plan, the renewal date when
// one exists, and the upgrade link.
//
// The renewal line appears only when the snapshot carries a renewal date. A
// trial's grant is one-time, so telling a trial user to wait for renewal
// would be telling them to wait forever.
//
// Each agent app's WebUI polls its own broker. Polling pauses while the tab is
// hidden and a failed poll changes nothing on screen: the last good numbers
// stay up rather than flickering to an error.
(() => {
  'use strict';

  const BILLING_URL = '/__humr_broker/billing';
  const CARD_ID = 'humrCreditsCard';
  // The broker refreshes its own copy lazily once a minute, so polling faster
  // than that only repeats a local hop.
  const POLL_MS = 30000;

  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  let _card = null;
  let _count = null;
  let _plan = null;
  let _renewal = null;
  let _upgrade = null;
  let _pollTimer = null;

  function elem(tag, props, children) {
    const el = document.createElement(tag);
    if (props) {
      for (const k in props) {
        if (k === 'style' && typeof props[k] === 'object') Object.assign(el.style, props[k]);
        else if (k === 'dataset' && typeof props[k] === 'object') Object.assign(el.dataset, props[k]);
        else if (k.startsWith('on') && typeof props[k] === 'function') el.addEventListener(k.slice(2), props[k]);
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

  // Grouped by hand rather than through toLocaleString: the card reads the
  // same on every machine, and the tests can assert an exact string.
  function groupedNumber(value) {
    return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  // Formatted off the ISO date's own digits. Going through Date() would shift
  // a bare YYYY-MM-DD by a day for anyone west of UTC.
  function formatRenewalDate(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value));
    const month = match ? MONTHS[Number(match[2]) - 1] : null;
    if (!month) return String(value);
    return month + ' ' + Number(match[3]) + ', ' + match[1];
  }

  // A snapshot is only usable if every number the card draws is really a
  // number; anything else and the card stays hidden rather than rendering NaN.
  function usableEntitlementSnapshot(payload) {
    if (!payload || typeof payload !== 'object') return null;
    const entitlementSnapshot = payload.entitlement;
    if (!entitlementSnapshot || typeof entitlementSnapshot !== 'object') return null;
    if (!Number.isFinite(entitlementSnapshot.credits_remaining)) return null;
    if (!Number.isFinite(entitlementSnapshot.monthly_grant)) return null;
    return entitlementSnapshot;
  }

  async function fetchBilling() {
    try {
      const response = await fetch(BILLING_URL, { cache: 'no-store' });
      if (!response.ok) return null;
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  function render(payload) {
    const entitlementSnapshot = usableEntitlementSnapshot(payload);
    if (!entitlementSnapshot) {
      // HUMR has not answered yet (or answered with nothing usable). An agent
      // whose credits are unknown says nothing about them.
      _card.hidden = true;
      return;
    }

    const grant = entitlementSnapshot.monthly_grant;
    const remaining = Math.max(0, entitlementSnapshot.credits_remaining);
    const exhausted = entitlementSnapshot.exhausted === true;

    _card.hidden = false;
    _card.dataset.state = exhausted ? 'out' : 'ok';
    _count.textContent = grant > 0 ? groupedNumber(remaining) + '/' + groupedNumber(grant) : groupedNumber(remaining);

    _plan.textContent = typeof entitlementSnapshot.plan === 'string' && entitlement.plan
      ? entitlementSnapshot.plan.charAt(0).toUpperCase() + entitlementSnapshot.plan.slice(1) + ' plan'
      : '';
    _plan.hidden = !_plan.textContent;

    const renewalLine = entitlementSnapshot.renewal_date ? 'Credits renew on ' + formatRenewalDate(entitlementSnapshot.renewal_date) : '';
    _renewal.textContent = renewalLine;
    _renewal.hidden = !renewalLine;

    const upgradeUrl = typeof payload.upgrade_url === 'string' ? payload.upgrade_url : '';
    if (upgradeUrl) _upgrade.setAttribute('href', upgradeUrl);
    _upgrade.hidden = !upgradeUrl;
  }

  // Total by construction: a rejection here would escape the poll timer and
  // stop the chain, so the card would freeze on whatever it last drew.
  async function refresh() {
    const payload = await fetchBilling();
    // A failed poll leaves the last good numbers up; the card never flickers
    // into an error state over one bad round-trip.
    if (payload === null) return;
    try {
      render(payload);
    } catch (err) {
      console.error('[humr-credits] Failed to render the credits card:', err);
    }
  }

  function stopPolling() {
    if (_pollTimer === null) return;
    clearTimeout(_pollTimer);
    _pollTimer = null;
  }

  function schedulePoll() {
    if (_pollTimer !== null) return;
    _pollTimer = setTimeout(async () => {
      _pollTimer = null;
      await refresh();
      schedulePoll();
    }, POLL_MS);
  }

  async function onVisibilityChange() {
    if (document.visibilityState === 'hidden') {
      stopPolling();
      return;
    }
    stopPolling();
    await refresh();
    schedulePoll();
  }

  function buildCard() {
    _count = elem('span', { class: 'humr-credits-count' });
    _plan = elem('div', { class: 'humr-credits-plan', hidden: '' });
    _renewal = elem('div', { class: 'humr-credits-renewal', hidden: '' });
    _upgrade = elem('a', {
      class: 'humr-credits-upgrade',
      target: '_blank',
      rel: 'noopener',
      hidden: '',
    }, ['Upgrade']);
    _card = elem('div', {
      class: 'humr-credits-card',
      id: CARD_ID,
      role: 'status',
      'aria-live': 'polite',
      'aria-label': 'Credits',
      hidden: '',
    }, [
      elem('div', { class: 'humr-credits-head' }, [
        elem('span', { class: 'humr-credits-title' }, [
          elem('span', { class: 'humr-credits-label' }, ['Credits']),
          _count,
        ]),
        _upgrade,
      ]),
      _plan,
      _renewal,
    ]);
    return _card;
  }

  // The WebUI shell renders after extension scripts on a fresh load, so retry
  // each frame until the sidebar exists. Ordering against the HumR panel panes
  // is handled in CSS (flex `order`) rather than by DOM position, so mounting
  // early or late lands the card at the bottom either way.
  function runMountLoop() {
    try {
      const sidebar = document.querySelector('.sidebar');
      if (!sidebar) {
        requestAnimationFrame(runMountLoop);
        return;
      }
      if (document.getElementById(CARD_ID)) return;
      sidebar.appendChild(buildCard());
      document.addEventListener('visibilitychange', onVisibilityChange);
      refresh().then(schedulePoll);
    } catch (err) {
      console.error('[humr-credits] Failed to mount the credits card:', err);
    }
  }

  requestAnimationFrame(runMountLoop);
})();
