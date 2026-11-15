/* Shared loading copy for situation and decision reviews.
   The visible second counter is not an announcement. The live region hears
   the start, one "Still reviewing…" after a wait, and the outcome. */
(function (root) {
  "use strict";

  const STILL_AFTER_S = 8;

  function line(seconds, still) {
    const text = `Reviewing this moment… ${seconds}s`;
    return still ? `${text} Still reviewing…` : text;
  }

  function classifyFailure(signal) {
    const reason = signal && signal.reason;
    if (reason === "timeout") {
      return { status: "error", kind: "timeout", error: "The review timed out.", retryable: true };
    }
    if (signal && signal.aborted) {
      return { status: "error", kind: "cancelled", error: "The review was cancelled.", retryable: true };
    }
    return { status: "error", kind: "network", error: "The review could not reach the server.", retryable: true };
  }

  function completionAnnouncement(result) {
    if (!result) return null;
    if (result.status === "ok") return result.cached ? "Review ready, from cache" : "Review ready";
    if (result.kind === "timeout") return "The review timed out";
    if (result.kind === "cancelled") return "The review was cancelled";
    if (result.kind === "network") return "The review could not reach the server";
    if (result.status === "not_configured") return "AI review is not set up";
    return "Review failed";
  }

  function announcer() {
    let stillSaid = false;
    return {
      reset() { stillSaid = false; },
      tick(seconds) {
        const still = seconds >= STILL_AFTER_S;
        let announce = null;
        if (seconds === 0) announce = "Reviewing this moment";
        else if (still && !stillSaid) {
          stillSaid = true;
          announce = "Still reviewing…";
        }
        return { seconds, still, text: line(seconds, still), announce };
      },
    };
  }

  function paint(rootEl, tick) {
    if (!rootEl || !tick) return;
    const seconds = rootEl.querySelector("[data-review-seconds]");
    const still = rootEl.querySelector("[data-review-still]");
    if (seconds) seconds.textContent = String(tick.seconds);
    if (still) still.hidden = !tick.still;
  }

  function announce(message) {
    const el = typeof document !== "undefined" ? document.querySelector("#ai-announcer") : null;
    if (!el || !message) return;
    el.textContent = "";
    el.textContent = message;
  }

  function loadingMarkup(squadName, timeLabel) {
    return `<p class="when" data-review-line>Reviewing this moment… <span aria-hidden="true"><span data-review-seconds>0</span>s</span></p>
      <p class="when" data-review-still hidden>Still reviewing…</p>
      <p class="when">${squadName} at ${timeLabel}</p>
      <p class="when">The replay stays usable while this runs.</p>`;
  }

  function startClock(rootEl, onAnnounce) {
    const voice = announcer();
    let timer = null;
    const started = Date.now();
    const tick = () => {
      const update = voice.tick(Math.floor((Date.now() - started) / 1000));
      paint(rootEl, update);
      if (update.announce && onAnnounce) onAnnounce(update.announce);
    };
    tick();
    timer = setInterval(tick, 1000);
    return function stop() {
      if (timer != null) clearInterval(timer);
      timer = null;
    };
  }

  root.ReviewStatus = {
    STILL_AFTER_S,
    line,
    classifyFailure,
    completionAnnouncement,
    announcer,
    paint,
    announce,
    loadingMarkup,
    startClock,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
