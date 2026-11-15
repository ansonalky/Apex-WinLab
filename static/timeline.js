/* Display-only event timeline under the ring status line.
   Clicks use the seek function the dashboard already uses for highlight times.
   The payload is not sent to the model or the coach. */
window.EventTimeline = (() => {
  const root = document.querySelector("#event-timeline");
  let key = null;
  let loadingKey = null;
  let request = 0;
  let payload = null;
  let seekTo = () => {};
  let shownElapsed = 0;
  let duration = 0;

  const esc = (value) => String(value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  function percent(elapsed) {
    if (!(duration > 0) || elapsed == null || !Number.isFinite(Number(elapsed))) return 0;
    return Math.max(0, Math.min(100, (100 * Number(elapsed)) / duration));
  }

  function mark(kind, elapsed, title) {
    const label = esc(title);
    return `<button type="button" class="timeline-mark ${kind}" style="left:${percent(elapsed)}%" data-elapsed="${Number(elapsed)}" title="${label}" aria-label="${label}"></button>`;
  }

  function render() {
    if (!root) return;
    const rings = (payload && payload.rings) || [];
    const markers = (payload && payload.markers) || [];
    const intervals = rings.map((ring) => {
      if (ring.start_elapsed == null) return "";
      if (!ring.end_recorded || ring.end_elapsed == null) {
        const title = `Ring ${ring.ring_number} starts closing. Its end is not recorded.`;
        return `<button type="button" class="timeline-close" style="left:${percent(ring.start_elapsed)}%" data-elapsed="${Number(ring.start_elapsed)}" title="${esc(title)}" aria-label="${esc(title)}"></button>`;
      }
      const left = percent(ring.start_elapsed);
      const width = Math.max(0.4, percent(ring.end_elapsed) - left);
      const closing = `Ring ${ring.ring_number} closing`;
      const closed = `Ring ${ring.ring_number} closed`;
      return `<span class="timeline-interval" style="left:${left}%;width:${width}%" title="${esc(closing)}"></span>
        <button type="button" class="timeline-close" style="left:${percent(ring.end_elapsed)}%" data-elapsed="${Number(ring.end_elapsed)}" title="${esc(closed)}" aria-label="${esc(closed)}"></button>`;
    }).join("");
    const marks = markers.map((item) => (item.elapsed == null ? "" : mark(item.kind, item.elapsed, item.text))).join("");
    root.hidden = false;
    root.innerHTML = `<div class="timeline-track" data-track>
        ${intervals}${marks}
        <div class="timeline-playhead" data-playhead style="left:${percent(shownElapsed)}%"></div>
      </div>
      <p class="timeline-legend">Yellow bar: ring closing. Yellow mark: recorded close. Green: confirmed kill. Light blue: knock. Red: death. Red with a white edge: squad eliminated. White line: current time.</p>
      <p class="timeline-tip" data-tip></p>`;
  }

  function movePlayhead() {
    const head = root && root.querySelector("[data-playhead]");
    if (head) head.style.left = `${percent(shownElapsed)}%`;
  }

  if (root) {
    root.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-elapsed]");
      if (button) {
        seekTo(Number(button.dataset.elapsed));
        return;
      }
      const track = event.target.closest("[data-track]");
      if (!track || !(duration > 0)) return;
      const rect = track.getBoundingClientRect();
      if (!(rect.width > 0)) return;
      const ratio = (event.clientX - rect.left) / rect.width;
      seekTo(Math.round(Math.max(0, Math.min(duration, ratio * duration))));
    });
    root.addEventListener("pointerover", (event) => {
      const tip = root.querySelector("[data-tip]");
      const target = event.target.closest("[title]");
      if (tip) tip.textContent = target ? target.getAttribute("title") || "" : "";
    });
  }

  async function sync(options) {
    if (!root || !options) return;
    seekTo = options.seek || seekTo;
    shownElapsed = Number(options.elapsed) || 0;
    if (Number(options.duration) > 0) duration = Number(options.duration);
    const nextKey = `${options.gameId}:${options.team}`;
    if (key === nextKey && payload) {
      movePlayhead();
      return;
    }
    if (loadingKey === nextKey) {
      movePlayhead();
      return;
    }
    const mine = ++request;
    key = nextKey;
    loadingKey = nextKey;
    try {
      const response = await fetch(`/api/matches/${options.gameId}/event-timeline?team=${encodeURIComponent(options.team)}`);
      const body = await response.json();
      if (mine !== request) return;
      payload = body && body.available ? body : { rings: [], markers: [], available: false };
      render();
    } catch (error) {
      if (mine !== request) return;
      payload = null;
      root.hidden = false;
      root.innerHTML = `<p class="unavailable">Event timeline could not be loaded.</p>`;
      console.error(error);
    } finally {
      if (mine === request) loadingKey = null;
    }
  }

  return { sync };
})();
