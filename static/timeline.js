/* Display-only timeline. Evidence and predictions never read this payload. */
window.EventTimeline = (() => {
  const root = document.querySelector("#event-timeline");
  let key = null, generation = 0, controller = null, payload = null;
  let seekTo = () => {}, shownElapsed = 0, duration = 0, groups = [];
  let activeGroup = null, pinned = false;
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const esc = (value) => String(value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
  const clock = (value) => `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
  const percent = (value) => duration > 0 ? Math.max(0, Math.min(100, value * 100 / duration)) : 0;
  const inRange = (value) => finite(value) && value >= 0 && value <= duration;

  function dismiss() {
    activeGroup = null;
    pinned = false;
    const tip = root && root.querySelector("[data-tip]");
    if (tip) { tip.hidden = true; tip.innerHTML = ""; }
    if (root) root.querySelectorAll("[data-group]").forEach((button) => button.setAttribute("aria-expanded", "false"));
  }

  // Invalidates even requests whose fetch implementation ignores abort().
  function reset() {
    generation += 1;
    if (controller) controller.abort();
    controller = null;
    key = null;
    payload = null;
    groups = [];
    activeGroup = null;
    pinned = false;
    if (root) { root.innerHTML = ""; root.hidden = true; }
  }

  function showDetails(index, pin = false) {
    const group = groups[index];
    const tip = root.querySelector("[data-tip]");
    if (!group || !tip) return;
    activeGroup = index;
    pinned = pin;
    root.querySelectorAll("[data-group]").forEach((button) => button.setAttribute("aria-expanded", String(Number(button.dataset.group) === index)));
    tip.innerHTML = `<strong>${group.length > 1 ? "Choose an event" : "Event details"}</strong>` + group.map((item, i) =>
      `<button type="button" data-event="${i}">${clock(item.elapsed)} · ${esc(item.text)}</button>`).join("");
    tip.style.left = `${Math.min(100, Math.max(0, percent(group[0].elapsed)))}%`;
    tip.style.transform = `translateX(-${Math.min(100, Math.max(0, percent(group[0].elapsed)))}%)`;
    tip.hidden = false;
  }

  function movePlayhead() {
    const head = root && root.querySelector("[data-playhead]");
    if (!head) return;
    head.style.left = `${percent(shownElapsed)}%`;
    head.setAttribute("aria-valuenow", String(shownElapsed));
    head.setAttribute("aria-valuetext", clock(shownElapsed));
  }

  function render() {
    const events = [];
    for (const ring of payload.rings || []) {
      if (ring.end_recorded && inRange(ring.end_elapsed)) {
        events.push({ kind: "ring-close", elapsed: ring.end_elapsed, text: `Ring ${ring.ring_number} closed` });
      } else if (inRange(ring.start_elapsed)) {
        events.push({ kind: "ring-start", elapsed: ring.start_elapsed, text: `Ring ${ring.ring_number} starts closing; completion not recorded` });
      }
    }
    for (const event of payload.markers || []) {
      if (inRange(event.elapsed) && ["kill", "knock", "death", "squad_elimination"].includes(event.kind)) events.push(event);
    }
    events.sort((a, b) => a.elapsed - b.elapsed);
    groups = [];
    // Nearby ticks share an event picker; every event retains its exact time.
    for (const event of events) {
      const last = groups[groups.length - 1];
      if (last && percent(event.elapsed) - percent(last[0].elapsed) < 1.5) last.push(event);
      else groups.push([event]);
    }
    const marks = groups.map((group, i) => `<button type="button" class="timeline-mark" data-group="${i}" style="left:${percent(group[0].elapsed)}%" aria-label="${esc(group.length > 1 ? `${group.length} events near ${clock(group[0].elapsed)}; choose an event` : `${clock(group[0].elapsed)} · ${group[0].text}`)}" aria-expanded="false" aria-controls="timeline-details">${group.map((item) => `<span class="timeline-tick ${item.kind}" aria-hidden="true"></span>`).join("")}</button>`).join("");
    root.hidden = false;
    root.innerHTML = `<div class="timeline-track" data-track role="group" aria-label="Match event timeline">
      ${marks}<div class="timeline-playhead" data-playhead role="slider" tabindex="0" aria-label="Replay time" aria-valuemin="0" aria-valuemax="${duration}" aria-valuenow="${shownElapsed}" aria-valuetext="${clock(shownElapsed)}"></div>
      </div><div class="timeline-endpoints"><span>0:00</span><span>${clock(duration)}</span></div>
      <div class="timeline-tip" id="timeline-details" data-tip role="group" aria-label="Event details" hidden></div>
      <details class="timeline-key"><summary>Timeline key</summary><p>Green: kill · Blue: knock · Red: death · White-edged red: squad eliminated · Solid yellow: ring closed · Hollow yellow: closing start, completion unknown. Select a tick for details; select an event to seek. Use arrow keys on the white playhead to seek by one second, Shift for ten seconds.</p></details>`;
    movePlayhead();
  }

  if (root) {
    root.addEventListener("click", (event) => {
      const choice = event.target.closest("[data-event]");
      if (choice && activeGroup !== null) {
        const item = groups[activeGroup][Number(choice.dataset.event)];
        if (item) seekTo(item.elapsed);
        const anchor = root.querySelector(`[data-group="${activeGroup}"]`);
        if (anchor) anchor.focus();
        dismiss();
        return;
      }
      const button = event.target.closest("[data-group]");
      if (button) {
        const index = Number(button.dataset.group);
        showDetails(index, true);
        if (groups[index].length === 1) seekTo(groups[index][0].elapsed);
        else root.querySelector("[data-tip] [data-event]")?.focus();
        return;
      }
      const track = event.target.closest("[data-track]");
      if (!track || !(duration > 0) || event.target.closest("[data-playhead]")) return;
      const rect = track.getBoundingClientRect();
      if (rect.width > 0) { dismiss(); seekTo(Math.round(Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * duration)); }
    });
    root.addEventListener("pointerover", (event) => {
      const button = event.target.closest("[data-group]");
      if (button && !pinned) showDetails(Number(button.dataset.group));
    });
    root.addEventListener("pointerleave", () => { if (!pinned && !root.contains(document.activeElement)) dismiss(); });
    root.addEventListener("focusin", (event) => {
      const button = event.target.closest("[data-group]");
      if (button) showDetails(Number(button.dataset.group));
    });
    root.addEventListener("focusout", (event) => { if (!root.contains(event.relatedTarget)) dismiss(); });
    root.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        const anchor = activeGroup === null ? null : root.querySelector(`[data-group="${activeGroup}"]`);
        if (anchor) anchor.focus();
        dismiss();
        event.preventDefault();
      }
      if (!event.target.closest("[data-playhead]")) return;
      const step = event.shiftKey ? 10 : 1;
      const next = { ArrowLeft: shownElapsed - step, ArrowDown: shownElapsed - step,
        ArrowRight: shownElapsed + step, ArrowUp: shownElapsed + step, Home: 0, End: duration }[event.key];
      if (next !== undefined) { event.preventDefault(); seekTo(Math.max(0, Math.min(duration, next))); }
    });
    document.addEventListener("pointerdown", (event) => { if (!root.contains(event.target)) dismiss(); });
  }

  async function sync(options) {
    if (!root || !options) return;
    const nextKey = `${options.gameId}:${options.team}`;
    if (key !== nextKey) reset();
    seekTo = options.seek || seekTo;
    shownElapsed = finite(options.elapsed) ? options.elapsed : 0;
    duration = finite(options.duration) && options.duration > 0 ? options.duration : 0;
    if (key === nextKey) { movePlayhead(); return; }
    key = nextKey;
    const mine = generation;
    controller = new AbortController();
    const signal = controller.signal;
    try {
      const response = await fetch(`/api/matches/${options.gameId}/event-timeline?team=${encodeURIComponent(options.team)}`, { signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.json();
      if (mine !== generation || key !== nextKey) return;
      payload = body && body.available ? body : { rings: [], markers: [] };
      render();
    } catch (error) {
      if (mine !== generation || signal.aborted) return;
      key = null; // The next sync can retry.
      root.hidden = false;
      root.innerHTML = `<p class="unavailable">Event timeline could not be loaded.</p>`;
    } finally {
      if (mine === generation) controller = null;
    }
  }
  return { sync, reset };
})();
