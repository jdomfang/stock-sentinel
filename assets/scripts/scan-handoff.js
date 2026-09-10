// Runs in a zero-height Streamlit component. All work is presentation-only.
(() => {
  const {phase, run, target} = window.scanHandoff;
  const host = window.parent;
  const doc = host.document;
  const states = host.__ssScanHandoff || (host.__ssScanHandoff = new Map());
  if (phase === 'start') {
    if (states.has(run)) return;
    for (const state of states.values()) state.dispose?.();
    states.clear();
    doc.getElementById('ss-scan-completion-link')?.remove();
    const state = {displaced: false, done: false};
    const leave = event => {
      if (event.isTrusted && (event.type === 'wheel'
          || !event.target.closest?.('.st-key-discovery_scan_progress'))) {
        state.displaced = true;
      }
    };
    for (const name of ['pointerdown', 'wheel', 'keydown']) doc.addEventListener(name, leave, true);
    state.dispose = () => {
      for (const name of ['pointerdown', 'wheel', 'keydown']) doc.removeEventListener(name, leave, true);
    };
    states.set(run, state);
    return;
  }
  const state = states.get(run) || {done: false, displaced: false};
  if (state.done && phase !== 'bind') return;
  states.set(run, state);
  state.done = true;
  state.dispose?.();
  const move = element => {
    element.focus({preventScroll: true});
    element.scrollIntoView({block: 'start', behavior:
      host.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
  };
  const attach = () => {
    // Streamlit's Markdown renderer can replace heading IDs; bind to our container.
    const result = target === 'ss-scan-results'
      ? doc.querySelector('.scan-results-intro h2')
      : doc.querySelector('.ss-system-state__title')
        || doc.querySelector('[data-testid="stAlert"]') || doc.getElementById(target);
    if (!result) return false;
    const oldAnchor = doc.getElementById(target);
    if (oldAnchor && oldAnchor !== result) oldAnchor.removeAttribute('id');
    result.id = target;
    result.tabIndex = -1;
    const back = doc.querySelector('.scan-results-intro a[href="#sector-pulse"]');
    if (back) back.id = 'ss-back-to-pulse';
    if (back) back.onclick = event => {
      const pulse = doc.querySelector('.st-key-ss_pulse_discovery h2, .st-key-ss_pulse_discovery h3');
      if (!pulse) return;
      event.preventDefault();pulse.tabIndex = -1;move(pulse);
    };
    if (phase !== 'complete') return true;
    if (state.displaced) {
      const link = doc.createElement('a');
      link.id = 'ss-scan-completion-link';
      link.href = '#' + target;
      link.textContent = 'View scan outcome →';
      link.setAttribute('role', 'status');
      link.onclick = event => {event.preventDefault();move(result);link.remove();};
      doc.body.appendChild(link);
    } else {
      host.requestAnimationFrame(() => move(result));
    }
    return true;
  };
  if (!attach()) {
    const observer = new host.MutationObserver(() => {if (attach()) observer.disconnect();});
    observer.observe(doc.body, {childList: true, subtree: true});
    host.setTimeout(() => observer.disconnect(), 5000);
  }
})();
