// AI tactical review: runs only when "Analyze this situation" is clicked, for
// the match, squad and second shown at that moment. The request never blocks
// seeking. A review stays labeled with its own second when the replay moves.
// After an error the old review is removed, never shown as if it were new.
//
// Layout: the recommended action leads, with its condition and one sentence
// of why; the summary and alternatives are expandable. Provider metadata,
// evidence IDs and timestamps go in the "Review details" drawer. The stored
// response is never modified; display helpers only choose and trim fields.
window.Coach = (() => {
  const box = document.querySelector("#coach");
  const detailsBox = document.querySelector("#coach-details");
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
  const clock = (s) => window.UI.mmss(s);

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

  // Some responses repeat the condition at the start of the action text
  // (coach-v1/v2, before the prompt said not to). Show it once.
  function withoutCondition(text, condition) {
    const t = String(text || "").trim();
    const c = String(condition || "").trim().replace(/[\s.,;:—–-]+$/, "");
    if (!c || !t.toLowerCase().startsWith(c.toLowerCase())) return t;
    const rest = t.slice(c.length).replace(/^[\s.,;:—–-]+/, "");
    return rest ? rest.charAt(0).toUpperCase() + rest.slice(1) : t;
  }

  function firstSentence(text) {
    const t = String(text || "").trim();
    const match = t.match(/^.*?[.!?](?=\s|$)/);
    return match ? match[0] : t;
  }

  // Display only: an action split from its condition can start lowercase.
  function capitalize(html) {
    return html ? html.charAt(0).toUpperCase() + html.slice(1) : html;
  }

  // The leading block: action, its condition, and why.
  function nextMove({ action, condition, why, check }) {
    if (!action) {
      return `<div class="next-move none"><span class="section-label">Recommended action</span>
        <p class="action">No action is supported by this evidence yet.</p>
        ${check ? `<p class="why">Check first: ${capitalize(check)}</p>` : ""}</div>`;
    }
    return `<div class="next-move"><span class="section-label">Recommended action</span>
      <p class="action">${capitalize(action)}</p>
      ${condition ? `<p class="condition">${condition}</p>` : ""}
      ${why ? `<p class="why"><strong>Why:</strong> ${why}</p>` : ""}</div>`;
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

  // Provider, model, thinking level, prompt, date, latency, cache status.
  function provenance(r) {
    const meta = r.provider_meta || {};
    const usage = meta.usage || {};
    const rows = [
      ["Review", `${esc(r.squad_name)} at ${esc(r.clock)} (replay t ${esc(r.t)}); evidence ${esc(r.window.start_clock)}–${esc(r.window.end_clock)}`],
      ["Provider", esc(r.provider || "Unknown")],
      ["Model", `${esc(r.model || "Unknown")}${meta.served_model && meta.served_model !== r.model ? ` (served by ${esc(meta.served_model)})` : ""}`],
      ["Thinking level", esc(r.effort || "model default")],
      ["Prompt", esc(r.prompt_version || "Unknown")],
      ["Generated", esc(r.generated_at || "Unknown")],
      ["Latency", r.cached ? "served from cache" : r.latency_s != null ? `${esc(r.latency_s)} s` : "Unknown"],
      ["Cache", r.cached ? "from cache (same evidence, provider, model and prompt)" : "new response, now cached"],
      ["Tokens", usage.input_tokens != null ? `${esc(usage.input_tokens)} in · ${esc(usage.output_tokens)} out${usage.thinking_tokens != null ? ` · ${esc(usage.thinking_tokens)} thinking` : ""}` : "Unknown"],
      ["Request", esc(meta.request_id || "Unknown")],
      ["Packet hash", esc((r.packet_hash || "").slice(0, 16) || "Unknown")],
    ];
    return `<p class="when">${esc(r.label || "")}</p>
      <dl class="facts">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
  }

  function factorCard(factor, items) {
    const facts = (factor.observed || []).map((o) => `<li>${esc(o.fact)} ${chips(o.evidence_ids, items)}</li>`).join("");
    return `<article class="factor ${esc(factor.assessment)}">
      <header><strong>${esc(factor.name)}</strong><span class="tag ${esc(factor.assessment)}">${esc(factor.assessment)}</span></header>
      <div class="facts"><span class="section-label">Observed facts</span><ul>${facts}</ul></div>
      <div class="interp"><span class="section-label">Interpretation</span><p>${esc(factor.interpretation)}</p></div>
      ${factor.uncertainty ? `<div class="uncert"><span class="section-label">Uncertainty</span><p>${esc(factor.uncertainty)}</p></div>` : ""}
      <div><span class="section-label">Evidence</span>${chips(factor.evidence_ids, items)}</div>
    </article>`;
  }

  function recLine(rec) {
    const text = esc(withoutCondition(rec.text, rec.condition));
    return `<li class="rec"><span class="tag">${rec.type === "review_question" ? "review question" : "if"}</span>${rec.condition ? `<em>${esc(rec.condition)}</em> — ` : ""}${text}</li>`;
  }

  // The factor that shares the most evidence with the recommendation explains it.
  function whyFor(rec, factors) {
    const ids = new Set(rec.evidence_ids || []);
    let best = null;
    let bestScore = -1;
    for (const factor of factors) {
      const own = new Set([...(factor.evidence_ids || []), ...(factor.observed || []).flatMap((o) => o.evidence_ids || [])]);
      const score = [...own].filter((id) => ids.has(id)).length;
      if (score > bestScore) {
        best = factor;
        bestScore = score;
      }
    }
    return best ? esc(firstSentence(best.interpretation)) : "";
  }

  function analysisView(r) {
    const a = r.analysis;
    const recs = a.recommendations || [];
    const factors = a.factors || [];
    const lead = recs.find((rec) => rec.type === "conditional_recommendation");
    const question = recs.find((rec) => rec.type === "review_question");
    const move = lead
      ? nextMove({ action: esc(withoutCondition(lead.text, lead.condition)), condition: esc(lead.condition || ""), why: whyFor(lead, factors) })
      : nextMove({ check: question ? `${question.condition ? `${esc(question.condition)} — ` : ""}${esc(withoutCondition(question.text, question.condition))}` : esc((a.missing_information || [])[0] || "") });
    const others = recs.filter((rec) => rec !== lead);
    return `<div class="stale-slot"></div>
      <p class="review-for"><strong>${esc(r.squad_name)}</strong> · reviewed at <strong>${esc(r.clock)}</strong></p>
      ${move}
      <details class="sub"><summary>Situation summary</summary><p class="summary">${esc(a.situation_summary)}</p></details>
      ${others.length ? `<details class="sub"><summary>Alternatives and review questions (${others.length})</summary><ul class="recs">${others.map(recLine).join("")}</ul></details>` : ""}`;
  }

  // Review details: metadata, then the evidence with clickable timestamps.
  function detailsView(r) {
    const a = r.analysis;
    const items = itemsById(r.packet);
    const factors = a.factors || [];
    const missing = (a.missing_information || []).map((m) => `<li>${esc(m)}</li>`).join("");
    const recEvidence = (a.recommendations || []).map((rec) => `<li>${rec.type === "review_question" ? "Review question" : "Recommendation"}: ${chips(rec.evidence_ids, items) || '<span class="when">no evidence cited</span>'}</li>`).join("");
    return `${provenance(r)}
      <span class="section-label">Evidence cited by the advice</span><ul class="recs">${recEvidence}</ul>
      <span class="section-label">Factors (${factors.length})</span>
      ${factors.map((f) => factorCard(f, items)).join("")}
      ${missing ? `<span class="section-label">Missing information that could change this</span><ul class="missing">${missing}</ul>` : ""}
      ${packetDetails(r.packet)}`;
  }

  function errorView(r, request) {
    const problems = (r.problems || []).map((p) => `<li>${esc(p)}</li>`).join("");
    const at = request ? ` for ${esc(request.squadName)} at ${clock(request.elapsed)}` : "";
    return `<p class="unavailable"><strong>No review${at}.</strong> ${esc(r.error || r.reason || "The request failed.")}</p>
      ${problems ? `<details open><summary>Grounding checks that failed</summary><ul>${problems}</ul></details>` : ""}
      ${r.kind ? `<p class="when">Kind: ${esc(r.kind)}. No earlier review is shown in its place.</p>` : `<p class="when">No earlier review is shown in its place.</p>`}
      ${r.retryable !== false && request ? `<p><button type="button" class="action" data-coach="retry">Retry ${clock(request.elapsed)}</button></p>` : ""}`;
  }

  function setDetails(html) {
    if (detailsBox) detailsBox.innerHTML = html;
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
    setDetails(`<p class="when">Waiting for the review.</p>`);
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
    try {
      if (result.status === "ok") {
        shownResult = result;
        box.innerHTML = analysisView(result);
        setDetails(detailsView(result));
        update();
      } else if (result.status === "not_configured") {
        box.innerHTML = setupState(result.setup || {});
        setDetails(packetDetails(result.packet) || `<p class="when">No review yet.</p>`);
      } else if (result.status === "finished" || result.status === "squad_eliminated") {
        box.innerHTML = `<p class="unavailable">${esc(result.reason)}</p>`;
        setDetails(`<p class="when">No review yet.</p>`);
      } else {
        box.innerHTML = errorView(result, request);
        setDetails(packetDetails(result.packet) || `<p class="when">No review.</p>`);
      }
    } catch (error) {
      console.error(error);
      box.innerHTML = `<p class="unavailable">The review could not be displayed.</p>`;
    }
  }

  function start() {
    const request = context();
    if (!request) return;
    run(request);
  }

  function onClick(event) {
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
    else if (action.dataset.coach === "cancel" && controller) controller.abort("cancel");
  }
  box && box.addEventListener("click", onClick);
  detailsBox && detailsBox.addEventListener("click", onClick);
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
      box.innerHTML = `<p class="when">Click “Analyze this situation” to review the selected squad at the time shown. Nothing is sent until you click.</p>`;
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
    setDetails(`<p class="when">No review yet.</p>`);
    if (box) loadStatus();
  }

  return { init, update, reset, helpers: { esc, clock, chips, itemsById, withoutCondition, firstSentence, nextMove } };
})();
