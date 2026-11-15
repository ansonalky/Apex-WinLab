// AI tactical review: runs only when "Analyze this situation" is clicked, for
// the match, squad and second shown at that moment. The request never blocks
// seeking. A review stays labeled with its own second when the replay moves.
// After an error the old review is removed, never shown as if it were new.
window.Coach = (() => {
  const box = document.querySelector("#coach");
  const button = document.querySelector("#coach-run");
  let context = () => null;
  let seekTo = () => {};
  let status = null;
  let token = 0;
  let controller = null;
  let shownResult = null;
  let lastRequest = null;
  let running = false;

  const esc = (value) => String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const clock = (s) => `${String(Math.floor(Math.max(0, s) / 60)).padStart(2, "0")}:${String(Math.max(0, s) % 60).padStart(2, "0")}`;

  function itemsById(packet) {
    return Object.fromEntries(((packet && packet.items) || []).map((item) => [item.id, item]));
  }

  function chips(ids, items) {
    return (ids || []).map((id) => {
      const item = items[id];
      if (!item) return `<span class="unavailable">${esc(id)}?</span>`;
      return `<button type="button" class="evidence" data-elapsed="${Number(item.elapsed)}" title="${esc(item.label)} · ${esc(item.clock)} — click to seek">${esc(id)} · ${esc(item.clock)}</button>`;
    }).join("");
  }

  function setupState(setup) {
    const missing = (setup.missing || []).map((m) => `<li>${esc(m)}</li>`).join("");
    const steps = (setup.setup || []).map((m) => `<li>${esc(m)}</li>`).join("");
    return `<p class="unavailable"><strong>AI review is not set up.</strong> No analysis was requested and none is shown.</p>
      <p class="when">Provider ${esc(setup.provider || "none")} · model ${esc(setup.model || "none")} · prompt ${esc(setup.prompt_version || "")}</p>
      <ul>${missing}</ul>
      <details><summary>How to set it up</summary><ol>${steps}</ol></details>`;
  }

  function packetDetails(packet) {
    if (!packet) return "";
    const rows = packet.items.map((item) => `<details class="packet-item"><summary>${chips([item.id], itemsById(packet))} ${esc(item.label)}${item.window ? ` <span class="when">(window ${esc(item.window.start_clock)}–${esc(item.window.end_clock)})</span>` : ""}</summary><pre>${esc(JSON.stringify(item.data, null, 1))}</pre></details>`).join("");
    return `<details><summary>Evidence packet: ${packet.items.length} items, ${esc(packet.window.start_clock)}–${esc(packet.window.end_clock)}, nothing later</summary>${rows}</details>`;
  }

  function heading(r) {
    const meta = r.provider_meta || {};
    const served = meta.served_model && meta.served_model !== r.model ? ` (served by ${esc(meta.served_model)})` : "";
    const source = r.cached ? `from cache, generated ${esc(r.generated_at || "")}` : `generated ${esc(r.generated_at || "")}${r.latency_s != null ? ` in ${r.latency_s} s` : ""}`;
    return `<p><span class="tag ai">AI-generated · reviewable</span><strong>Review of ${esc(r.squad_name)} at ${esc(r.clock)}</strong> (replay t ${r.t}) · evidence window ${esc(r.window.start_clock)}–${esc(r.window.end_clock)}</p>
      <p class="when">${esc(r.label || "")} ${esc(r.provider)} · ${esc(r.model)}${served} · effort ${esc(r.effort || "default")} · prompt ${esc(r.prompt_version)} · ${source}.</p>`;
  }

  function factorCard(factor, items) {
    const facts = (factor.observed || []).map((o) => `<li>${esc(o.fact)} ${chips(o.evidence_ids, items)}</li>`).join("");
    return `<article class="factor ${esc(factor.assessment)}">
      <header><strong>${esc(factor.name)}</strong><span class="tag ${esc(factor.assessment)}">${esc(factor.assessment)}</span></header>
      <div class="facts"><span class="section-label">Observed facts (from the evidence)</span><ul>${facts}</ul></div>
      <div class="interp"><span class="section-label">Interpretation (AI)</span><p>${esc(factor.interpretation)}</p></div>
      ${factor.uncertainty ? `<div class="uncert"><span class="section-label">Uncertainty</span><p>${esc(factor.uncertainty)}</p></div>` : ""}
      <div><span class="section-label">Evidence</span>${chips(factor.evidence_ids, items)}</div>
    </article>`;
  }

  function analysisView(r) {
    const a = r.analysis;
    const items = itemsById(r.packet);
    const recs = (a.recommendations || []).map((rec) => `<li class="rec"><span class="tag">${rec.type === "review_question" ? "review question" : "conditional"}</span>${rec.condition ? `<em>${esc(rec.condition)}</em> — ` : ""}${esc(rec.text)} ${chips(rec.evidence_ids, items)}</li>`).join("");
    const missing = (a.missing_information || []).map((m) => `<li>${esc(m)}</li>`).join("");
    return `<div class="stale-slot"></div>${heading(r)}
      <span class="section-label">Situation summary (AI)</span>
      <p class="summary">${esc(a.situation_summary)}</p>
      <span class="section-label">Tactical factors</span>
      ${(a.factors || []).map((f) => factorCard(f, items)).join("")}
      <span class="section-label">Recommendations and review questions (AI, conditional; not instructions)</span>
      <ul class="recs">${recs}</ul>
      ${missing ? `<span class="section-label">Missing information that could change this</span><ul class="missing">${missing}</ul>` : ""}
      ${packetDetails(r.packet)}
      <p><button type="button" class="action quiet" data-coach="rerun">Analyze the current moment</button></p>`;
  }

  function errorView(r, request) {
    const problems = (r.problems || []).map((p) => `<li>${esc(p)}</li>`).join("");
    const at = request ? ` for ${esc(request.squadName)} at ${clock(request.elapsed)}` : "";
    return `<p class="unavailable"><strong>No review${at}.</strong> ${esc(r.error || r.reason || "The request failed.")}</p>
      ${problems ? `<details open><summary>Grounding checks that failed</summary><ul>${problems}</ul></details>` : ""}
      ${r.kind ? `<p class="when">Kind: ${esc(r.kind)}. No earlier review is shown in its place.</p>` : `<p class="when">No earlier review is shown in its place.</p>`}
      ${r.retryable !== false && request ? `<p><button type="button" class="action" data-coach="retry">Retry ${clock(request.elapsed)}</button></p>` : ""}
      ${packetDetails(r.packet)}`;
  }

  // Label a review against what the replay shows now.
  function update() {
    if (!shownResult || !box) return;
    const slot = box.querySelector(".stale-slot");
    if (!slot) return;
    const now = context();
    const r = shownResult;
    if (!now || String(now.game) !== String(r.game_id)) {
      slot.innerHTML = `<p class="stale">This review is for another match.</p>`;
      return;
    }
    if (String(now.team) !== String(r.team)) {
      slot.innerHTML = `<p class="stale">This review is for ${esc(r.squad_name)} at ${esc(r.clock)}. The replay now shows another squad; it is not a review of the current selection.</p>`;
    } else if (now.t !== r.t) {
      const inWindow = now.t >= r.window.start_t && now.t < r.t;
      slot.innerHTML = inWindow
        ? `<p class="stale viewing">Viewing ${clock(now.elapsed)}, inside this review's evidence window. The review is for ${esc(r.clock)}.</p>`
        : `<p class="stale">This review is for ${esc(r.clock)}. The replay now shows ${clock(now.elapsed)}; it is not a review of the current moment.</p>`;
    } else {
      slot.innerHTML = "";
    }
  }

  async function run(request) {
    if (!box || !request) return;
    lastRequest = request;
    const mine = ++token;
    if (controller) controller.abort();
    controller = new AbortController();
    const timeoutMs = 1000 * ((status && status.timeout_s) || 90) + 15000;
    const timer = setTimeout(() => controller.abort("timeout"), timeoutMs);
    shownResult = null;
    running = true;
    box.innerHTML = `<p class="when">Analyzing ${esc(request.squadName)} at ${clock(request.elapsed)} (evidence from up to 60 s before). The replay stays usable while this runs.</p>
      <p><button type="button" class="action quiet" data-coach="cancel">Cancel</button></p>`;
    let result;
    try {
      const response = await fetch(`/api/matches/${request.game}/coach`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ t: request.t, team: request.team }),
        signal: controller.signal,
      });
      result = await response.json().catch(() => ({ status: "error", error: `HTTP ${response.status}`, retryable: true }));
    } catch (error) {
      result = controller.signal.reason === "timeout"
        ? { status: "error", kind: "timeout", error: "The analysis timed out.", retryable: true }
        : { status: "error", kind: "cancelled", error: "The analysis was cancelled.", retryable: true };
    } finally {
      clearTimeout(timer);
    }
    if (mine !== token) return;
    running = false;
    if (result.status === "ok") {
      shownResult = result;
      box.innerHTML = analysisView(result);
      update();
    } else if (result.status === "not_configured") {
      box.innerHTML = setupState(result.setup || {}) + packetDetails(result.packet);
    } else if (result.status === "finished" || result.status === "squad_eliminated") {
      box.innerHTML = `<p class="unavailable">${esc(result.reason)}</p>`;
    } else {
      box.innerHTML = errorView(result, request);
    }
  }

  function start() {
    const request = context();
    if (!request) return;
    run(request);
  }

  box && box.addEventListener("click", (event) => {
    const chip = event.target.closest("button.evidence");
    if (chip) {
      const elapsed = Number(chip.dataset.elapsed);
      const r = shownResult;
      const now = context();
      // Seek only within the match the review is about.
      if (Number.isFinite(elapsed) && (!r || (now && String(now.game) === String(r.game_id)))) seekTo(elapsed);
      return;
    }
    const action = event.target.closest("button[data-coach]");
    if (!action) return;
    if (action.dataset.coach === "retry") run(lastRequest);
    else if (action.dataset.coach === "rerun") start();
    else if (action.dataset.coach === "cancel" && controller) controller.abort("cancel");
  });
  button && button.addEventListener("click", start);

  async function loadStatus() {
    try {
      status = await (await fetch("/api/coach/status")).json();
    } catch (error) {
      status = null;
    }
    if (!box || shownResult || running) return;
    if (!status) {
      box.innerHTML = `<p class="unavailable">AI provider status could not be loaded.</p>`;
    } else if (!status.configured) {
      box.innerHTML = setupState(status) + `<p class="when">You can still click “Analyze this situation” to see the evidence packet that would be sent.</p>`;
    } else {
      box.innerHTML = `<p class="when">Click “Analyze this situation” to review the selected squad at the second shown. Provider ${esc(status.provider)} · model ${esc(status.model)}. Nothing is sent until you click.</p>`;
    }
  }

  // The dashboard supplies the current moment and its seek function.
  function init(options) {
    context = options.context;
    seekTo = options.seek;
    loadStatus();
  }

  // A new match: an old review's evidence cannot be sought in it.
  function reset() {
    token += 1;
    if (controller) controller.abort("cancel");
    shownResult = null;
    running = false;
    if (box) loadStatus();
  }

  return { init, update, reset };
})();
