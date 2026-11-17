/* global REPAIR_KEY */
(() => {
  'use strict';
  const button = document.getElementById('start-repairs');
  const consent = document.getElementById('repair-consent');
  const message = document.getElementById('repair-status');
  let snapshot = null;
  let busy = false;
  let sequence = 0;

  function display(state) {
    snapshot = state;
    const complete = state.status === 'complete';
    button.textContent = state.active ? 'Reference repairs are active'
      : complete ? 'Reference queue complete' : 'Start reference repairs';
    button.disabled = busy || state.active || complete || !consent.checked
      || ['blocked', 'outcome_unknown'].includes(state.status);
    message.textContent = `${state.verified} verified corrections of ${state.total} reviewed items. `
      + `${state.already_clean} already clean. Last checkpoint: ${state.status}. `
      + (state.active ? 'The queue process is active. ' : 'The queue process is not active. ')
      + (state.current_qid ? `Current item: ${state.current_qid}. ` : '')
      + (state.next_attempt_at ? `Saved retry time: ${state.next_attempt_at}.` : '');
  }

  async function refresh() {
    if (busy) return;
    const request = ++sequence;
    try {
      const response = await fetch('/api/repairs/status', {
        headers: {'X-Repair-Key': REPAIR_KEY}, cache: 'no-store',
      });
      if (!response.ok) throw new Error('The queue status is unavailable.');
      const state = await response.json();
      if (request === sequence && !busy) display(state);
    } catch (error) {
      if (request === sequence && !busy) {
        snapshot = null;
        button.disabled = true;
        message.textContent = error.message;
      }
    }
  }

  consent.onchange = () => { if (snapshot) display(snapshot); };
  button.onclick = async () => {
    if (!snapshot || !consent.checked || busy) return;
    busy = true;
    ++sequence;
    button.disabled = true;
    message.textContent = 'Please wait for the queue response.';
    try {
      const response = await fetch('/api/repairs/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Repair-Key': REPAIR_KEY},
        body: JSON.stringify({confirmed: true, scope_digest: snapshot.scope_digest}),
      });
      if (!response.ok) throw new Error('The queue refused the start request. Check its current state.');
      const state = await response.json();
      busy = false;
      display(state);
    } catch (error) {
      message.textContent = `${error.message} No automatic resend will occur. Refresh the page to inspect the state.`;
      snapshot = null;
    } finally {
      busy = false;
    }
  };
  void refresh();
  setInterval(() => { if (snapshot) void refresh(); }, 5000);
})();
