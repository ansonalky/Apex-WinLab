// One visible output and one client request lease; evidence stays in its own renderer.
window.ReviewPanel = (() => {
  const output = document.querySelector('#coach');
  const notice = document.querySelector('#review-notice');
  const heading = document.querySelector('#review-context');
  let mode = 'situation', lease = null;
  const saved = new Map();
  const labels = {situation: 'Current situation', decision: 'Key decision'};
  function busy() {
    if (!lease) return false;
    notice.textContent = 'A review is already running';
    return true;
  }
  function controls() {
    document.querySelectorAll('#coach-run, #start-here, #key-moment-manual, [data-decision="run"], [data-coach="retry"], [data-decision="retry"]').forEach(button => { button.disabled = Boolean(lease); });
    output.setAttribute('aria-busy', String(Boolean(lease)));
  }
  function select(next) {
    if (next !== mode && busy()) return false;
    if (next !== mode) {
      saved.set(mode, {html: output.innerHTML, heading: heading.textContent});
      mode = next;
      const previous = saved.get(mode);
      output.innerHTML = previous ? previous.html : '<p class="when">Choose a key moment to review its decision.</p>';
      heading.textContent = previous ? previous.heading : labels[mode];
    }
    document.querySelectorAll('[data-review-mode]').forEach(button => button.setAttribute('aria-selected', String(button.dataset.reviewMode === mode)));
    document.querySelector('#decision-details').hidden = mode !== 'decision';
    document.querySelector('#situation-details').hidden = mode !== 'situation';
    controls();
    if (mode === "situation" && window.Coach) window.Coach.update();
    if (mode === "decision" && window.DecisionReview) window.DecisionReview.update();
    return true;
  }
  function describe(next, request) {
    heading.textContent = `${labels[next]} · ${request.squadName} at ${window.UI.mmss(request.elapsed)}`;
  }
  function acquire(next, request) {
    if (busy()) return null;
    select(next);
    lease = {};
    notice.textContent = '';
    describe(next, request);
    controls();
    return lease;
  }
  function release(owned) { if (lease === owned) { lease = null; controls(); } }
  function clear(next) { saved.delete(next); }
  const tabs = [...document.querySelectorAll('[data-review-mode]')];
  tabs.forEach((button, index) => {
    button.addEventListener('click', () => select(button.dataset.reviewMode));
    button.addEventListener('keydown', event => {
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
        : event.key === 'ArrowRight' ? (index + 1) % tabs.length
          : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : null;
      if (next === null) return;
      event.preventDefault();
      if (select(tabs[next].dataset.reviewMode)) tabs[next].focus();
    });
  });
  select(mode);
  return {select, acquire, release, busy, clear, describe, active: next => mode === next, refresh: controls};
})();
