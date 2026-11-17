/* Display-only observer preview. No frame, model, tactical or coach mutation. */
window.ObserverRing = (() => {
  const toggle = document.querySelector("#observer-ring");
  const badge = document.querySelector("#observer-ring-badge");
  const status = document.querySelector("#observer-ring-status");
  let key = null, generation = 0, controller = null, payload = null, redraw = () => {};
  function clear() {
    generation += 1;
    if (controller) controller.abort();
    controller = null;
    key = null;
    payload = null;
    if (status) status.textContent = "";
  }
  if (toggle) toggle.addEventListener("change", () => {
    clear();
    if (badge) badge.hidden = !toggle.checked;
    redraw(); // Disabling removes observer geometry synchronously.
  });
  function view(options) {
    redraw = options.redraw;
    if (!toggle || !toggle.checked) return options.causal;
    const nextKey = `${options.gameId}:${options.t}`;
    if (key === nextKey) return payload || options.causal;
    clear();
    key = nextKey;
    const mine = generation;
    controller = new AbortController();
    if (status) status.textContent = "Loading preview…";
    fetch(`/api/matches/${options.gameId}/ring?t=${options.t}&observer=1`, {signal:controller.signal})
      .then((response) => { if (!response.ok) throw new Error("Preview unavailable"); return response.json(); })
      .then((body) => {
        if (mine !== generation || !toggle.checked || key !== nextKey) return;
        if (body.t !== options.t || body.observer_preview !== true) throw new Error("Preview timestamp mismatch");
        payload = body;
        if (status) status.textContent = body.location ? "" : "Recorded ring geometry unavailable";
        redraw();
      })
      .catch(() => {
        if (mine === generation && status) status.textContent = "Preview unavailable; showing revealed rings";
      })
      .finally(() => { if (mine === generation) controller = null; });
    return options.causal;
  }
  return { view, reset:clear };
})();
