// AI tactical review: runs only on an explicit Analyze or Start demo click, for
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
  // The reviewed match's teamId -> name/color, captured with the request.
  let teams = { tag: (id, name) => window.UI.teamTag(name ?? `Team ${id}`), color: () => null };
  let running = false;

  const esc = (value) => String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const clock = (s) => window.UI.mmss(s);

  function itemsById(packet) {
    return Object.fromEntries(((packet && packet.items) || []).map((item) => [item.id, item]));
  }

  function teamsFor(squads) {
    const tag = window.UI.teamIndex(squads);
    const colors = new Map((squads || []).map((squad) => [String(squad.teamId), squad.color]));
    return { tag, color: (id) => colors.get(String(id)) || null };
  }

  function chips(ids, items) {
    return (ids || []).map((id) => {
      const item = items[id];
      if (!item) return `<span class="unavailable">${esc(id)}?</span>`;
      // An opposing squad's evidence carries that squad's map color.
      const team = item.kind === "opponent" && item.data ? item.data.team : null;
      const color = team != null && /^#[0-9a-f]{3,8}$/i.test(String(teams.color(team) || "")) ? teams.color(team) : null;
      const swatch = color ? `<span class="team-swatch" style="background:${color}" aria-hidden="true"></span> ` : "";
      return `<button type="button" class="evidence" data-elapsed="${Number(item.elapsed)}" title="${esc(item.label)} · ${esc(item.clock)} — click to seek">${swatch}${esc(id)} · ${esc(item.clock)}</button>`;
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
    // coach-v4 and later: the short validated sentence. Earlier cached reviews
    // have no next_action and keep their recommendation block.
    const short = typeof a.next_action === "string" && a.next_action.trim();
    const move = short
      ? `<div class="next-move${/^Insufficient information/i.test(short) ? " none" : ""}"><span class="section-label">Recommended action</span><p class="action">${esc(short)}</p></div>`
      : lead
        ? nextMove({ action: esc(withoutCondition(lead.text, lead.condition)), condition: esc(lead.condition || "") })
        : nextMove({ check: question ? `${question.condition ? `${esc(question.condition)} — ` : ""}${esc(withoutCondition(question.text, question.condition))}` : esc((a.missing_information || [])[0] || "") });
    const why = lead ? whyFor(lead, factors) : "";
    const others = short ? recs : recs.filter((rec) => rec !== lead);
    return `<div class="stale-slot"></div>
      <p class="review-for">${teams.tag(r.team, r.squad_name)} · reviewed at <strong>${esc(r.clock)}</strong></p>
      ${move}
      <details class="sub"><summary>Details</summary>
        ${why ? `<span class="section-label">Why</span><p>${why}</p>` : ""}
        <span class="section-label">Situation</span><p class="summary">${esc(a.situation_summary)}</p>
        ${others.length ? `<span class="section-label">${short ? "Recommendations and review questions" : "Alternatives and review questions"}</span><ul class="recs">${others.map(recLine).join("")}</ul>` : ""}
        <p class="when">Evidence, factors, uncertainty and provenance are in Review details.</p>
      </details>`;
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
  // A short notice; the full explanation shows on hover or keyboard focus.
  function staleNotice(text, explanation, viewing) {
    return `<p class="stale tip${viewing ? " viewing" : ""}" tabindex="0" data-tip="${esc(explanation)}">${text}<span class="sr-only"> ${esc(explanation)}</span></p>`;
  }

  function update() {
    if (!shownResult || !box) return;
    const slot = box.querySelector(".stale-slot");
    if (!slot) return;
    const now = context();
    const r = shownResult;
    if (!now || String(now.game) !== String(r.game_id)) {
      slot.innerHTML = staleNotice("Review: another match", "This review is for another match; it does not describe what is shown.");
      return;
    }
    if (String(now.team) !== String(r.team)) {
      slot.innerHTML = staleNotice(
        `Review: ${teams.tag(r.team, r.squad_name)} ${esc(r.clock)} · Viewing: ${teams.tag(now.team)} ${clock(now.elapsed)}`,
        `This review is for ${r.squad_name} at ${r.clock}. Another squad is selected now, so it does not review the current selection.`,
      );
    } else if (now.t !== r.t) {
      const inWindow = now.t >= r.window.start_t && now.t < r.t;
      slot.innerHTML = staleNotice(
        `Review: ${esc(r.clock)} · Viewing: ${clock(now.elapsed)}`,
        inWindow
          ? `Viewing ${clock(now.elapsed)}, inside this review's evidence window. The review is for ${r.clock}.`
          : `This review is for ${r.clock}. The replay now shows ${clock(now.elapsed)}, so it is not a review of the current moment.`,
        inWindow,
      );
    } else {
      slot.innerHTML = "";
    }
  }

  async function run(request) {
    if (running || !box || !request) return;
    lastRequest = request;
    teams = teamsFor(request.squads);
    const mine = ++token;
    running = true;
    if (button) button.disabled = true;
    box.setAttribute("aria-busy", "true");
    controller = new AbortController();
    const timeoutMs = 1000 * ((status && status.timeout_s) || 90) + 15000;
    const timer = setTimeout(() => controller.abort("timeout"), timeoutMs);
    shownResult = null;
    box.innerHTML = `${ReviewStatus.loadingMarkup(esc(request.squadName), clock(request.elapsed))}
      <p><button type="button" class="action quiet" data-coach="cancel">Cancel</button></p>`;
    setDetails(`<p class="when">Waiting for the review of ${esc(request.squadName)} at ${clock(request.elapsed)}.</p>`);
    const stopClock = ReviewStatus.startClock(box, ReviewStatus.announce);
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
      result = ReviewStatus.classifyFailure(controller.signal);
    } finally {
      clearTimeout(timer);
      stopClock();
      if (mine === token) {
        running = false;
        if (button) button.disabled = false;
        box.setAttribute("aria-busy", "false");
      }
    }
    if (mine !== token) return;
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
      ReviewStatus.announce(ReviewStatus.completionAnnouncement(result));
    } catch (error) {
      console.error(error);
      box.innerHTML = `<p class="unavailable">The review could not be displayed.</p>`;
      ReviewStatus.announce("Review failed");
    }
  }

  async function start(expected = null) {
    if (running) return false;
    const request = context();
    if (!request) return false;
    if (expected && expected.game != null && (String(request.game) !== String(expected.game)
      || String(request.team) !== String(expected.team) || request.t !== expected.t)) return false;
    await run(request);
    return true;
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
    if (action.dataset.coach === "retry") {
      if (!running) run(lastRequest);
    } else if (action.dataset.coach === "cancel" && controller) controller.abort("cancel");
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

  // A finished review is cleared for a new match. An in-flight review keeps its
  // original match, squad and time, and is labeled stale if the selection moves.
  function reset() {
    if (running) return;
    token += 1;
    if (controller) controller.abort("cancel");
    shownResult = null;
    setDetails(`<p class="when">No review yet.</p>`);
    if (button) button.disabled = false;
    if (box) {
      box.setAttribute("aria-busy", "false");
      loadStatus();
    }
  }

  return { init, update, reset, start, helpers: { esc, clock, chips, itemsById, withoutCondition, firstSentence, nextMove, teamsFor } };
})();
