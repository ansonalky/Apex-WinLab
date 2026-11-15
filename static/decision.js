// Decision review of one downward highlight. Opening a highlight only fills
// the panel; the review is requested only by "Review decision". The decision
// time is editable, defaults to shortly before the episode, and stays pinned
// beside the slider while the replay moves elsewhere. Any failure here is
// shown in this panel and never reaches replay startup or seeking.
window.DecisionReview = (() => {
  const box = document.querySelector("#decision");
  const pin = document.querySelector("#decision-pin");
  const LEAD_S = 10;
  let context = () => null;
  let seekTo = () => {};
  let redraw = () => {};
  let state = null; // { game, team, squadName, kind, item, start, decisionElapsed, suggestedElapsed }
  let result = null;
  let lastRequest = null;
  let token = 0;
  let controller = null;

  const h = () => window.Coach.helpers;
  const esc = (v) => h().esc(v);
  const clock = (s) => h().clock(s);
  const kindName = (kind) => (kind === "held_out" ? "held-out" : "final model, fitted on these games (in-sample)");
  const optionName = (o) => ({ hold: "Hold", regroup: "Regroup", disengage: "Disengage", rotate_to_verified_position: "Rotate to a verified position", abstain: "Abstain" }[o] || o);
  const attributionName = (a) => ({ own_squad_deterioration: "Deterioration in this squad", changes_elsewhere: "Changes elsewhere", both: "Both", insufficient_evidence: "Insufficient evidence" }[a] || a);

  function parseClock(text) {
    const match = /^\s*(\d{1,3}):([0-5]\d)\s*$/.exec(text || "");
    return match ? Number(match[1]) * 60 + Number(match[2]) : null;
  }

  function seekChip(elapsed, label) {
    return `<button type="button" class="evidence" data-elapsed="${Number(elapsed)}" title="Seek to ${clock(elapsed)}">${esc(label)}</button>`;
  }

  function allItems(packet) {
    return packet ? [...packet.items, ...((packet.retrospective && packet.retrospective.items) || [])] : [];
  }

  function chips(ids, packet) {
    const items = Object.fromEntries(allItems(packet).map((item) => [item.id, item]));
    // Retrospective evidence (R-*) is styled apart from decision-time evidence.
    return h().chips(ids, items).replace(/class="evidence"(?= data-elapsed="[^"]*" title="[^"]*">R-)/g, 'class="evidence retro"');
  }

  function episodeView() {
    const item = state.item;
    const U = window.UI;
    return `<p><strong>${esc(item.squad_name || state.squadName)}</strong> · <span class="tag ${state.kind === "held_out" ? "heldout" : "fitted"}">${esc(kindName(state.kind))}</span></p>
      <dl class="episode">
        <dt>Representative window</dt><dd>${seekChip(item.start_elapsed, U.span(item.start_elapsed, item.end_elapsed))} ${U.finite(item.window_s) ? `${item.window_s} s · ` : ""}estimate ${U.pct(item.before)} → ${U.pct(item.after)} (${U.pp(item.change_pp)})</dd>
        <dt>Full episode</dt><dd>${seekChip(item.episode_start_elapsed, U.span(item.episode_start_elapsed, item.episode_end_elapsed))} ${U.text(item.windows_merged)} overlapping window${item.windows_merged === 1 ? "" : "s"}; the change above is for the representative window only</dd>
      </dl>
      ${state.kind !== "held_out" ? `<p class="when">This highlight comes from the final model, which was fitted on this match. Its estimates appear only in the retrospective part of the review.</p>` : ""}`;
  }

  function controlsView() {
    return `<div class="decision-controls">
        <label>Decision time (mm:ss)
          <input id="decision-time" type="text" inputmode="numeric" value="${clock(state.decisionElapsed)}" size="6" aria-describedby="decision-note">
        </label>
        <button type="button" class="action quiet" data-decision="seek">Seek to it</button>
        <button type="button" class="action quiet" data-decision="suggested">Reset to ${clock(state.suggestedElapsed)}</button>
        <button type="button" class="action" data-decision="run">Review decision</button>
      </div>
      <p id="decision-note" class="when">Suggested review time, not a proven mistake time. The default is ${LEAD_S} s before the episode begins, never before replay start. Evidence for recommendations is limited to the 60 s up to this time; anything later appears only in a separate retrospective section.</p>
      <p class="decision-error unavailable" hidden></p>`;
  }

  function render(body) {
    if (!box || !state) return;
    box.innerHTML = `${episodeView()}${controlsView()}<div class="decision-body">${body || ""}</div>`;
    updatePin();
  }

  function setBody(html) {
    const body = box && box.querySelector(".decision-body");
    if (body) body.innerHTML = html;
  }

  function packetView(packet) {
    if (!packet) return "";
    const row = (item, retro) => `<details class="packet-item"><summary>${chips([item.id], packet)} ${esc(item.label)}${item.window ? ` <span class="when">(${esc(item.window.start_clock)}–${esc(item.window.end_clock)})</span>` : ""}</summary><pre>${esc(JSON.stringify(item.data, null, 1))}</pre></details>`;
    const retro = packet.retrospective || { items: [] };
    return `<details><summary>Evidence at or before ${esc(packet.clock)}: ${packet.items.length} items (recommendation inputs)</summary>${packet.items.map((i) => row(i)).join("")}</details>
      <details class="retro"><summary>Retrospective, after the decision time (${esc(retro.from_clock || "")}–${esc(retro.to_clock || "")}): ${retro.items.length} items, not used for recommendations</summary>${retro.items.map((i) => row(i, true)).join("")}</details>`;
  }

  function facts(list, packet) {
    return `<ul>${(list || []).map((o) => `<li>${esc(o.fact)} ${chips(o.evidence_ids, packet)}</li>`).join("")}</ul>`;
  }

  function refLabel(packet, ref) {
    const item = packet.items.find((i) => i.id === ref);
    return item ? `${item.label} (${item.clock})` : ref;
  }

  function resultView(r) {
    const a = r.analysis;
    const p = r.packet;
    const meta = r.provider_meta || {};
    const served = meta.served_model && meta.served_model !== r.model ? ` (served by ${esc(meta.served_model)})` : "";
    const rec = a.recommendation || {};
    const weaknesses = (a.weaknesses || []).map((w) => `<article class="factor disadvantage">
        <header><strong>${esc(w.name)}</strong></header>
        <div class="facts"><span class="section-label">Observed facts at or before ${esc(p.clock)}</span>${facts(w.observed, p)}</div>
        <div class="interp"><span class="section-label">Interpretation (AI)</span><p>${esc(w.interpretation)}</p></div>
        ${w.uncertainty ? `<div class="uncert"><span class="section-label">Uncertainty</span><p>${esc(w.uncertainty)}</p></div>` : ""}
      </article>`).join("") || `<p class="when">No weakness is supported by the evidence at this time.</p>`;
    const options = (a.options || []).map((o) => {
      const chosen = rec.choice === o.option;
      const destination = o.destination_ref && o.destination_ref !== "none"
        ? `<p class="when">Reference position: ${chips([o.destination_ref], p)} ${esc(refLabel(p, o.destination_ref))}. Known coordinates; the route there is not verified.</p>`
        : `<p class="when">No destination position.</p>`;
      const observer = o.observer_only || {};
      return `<article class="factor option${chosen ? " chosen" : ""}">
        <header><strong>${esc(optionName(o.option))}</strong>${chosen ? '<span class="tag ai">recommended</span>' : ""}</header>
        ${destination}
        <div class="facts"><span class="section-label">Evidence</span>${facts(o.evidence, p)}</div>
        <div class="interp"><span class="section-label">Expected tactical benefit (AI)</span><p>${esc(o.expected_benefit)}</p></div>
        <div class="interp"><span class="section-label">Cost (AI)</span><p>${esc(o.cost)}</p></div>
        <div class="uncert"><span class="section-label">Uncertainty</span><p>${esc(o.uncertainty)}</p></div>
        <p class="when"><span class="tag">${observer.depends ? "relies on observer-only information" : "uses information the squad could perceive"}</span>${esc(observer.explanation || "")}</p>
      </article>`;
    }).join("");
    const attribution = a.drop_attribution || {};
    const missing = (a.missing_information || []).map((m) => `<li>${esc(m)}</li>`).join("");
    const count = (a.options || []).length;
    return `<div class="stale-slot"></div>
      <p class="review-head"><span class="tag ai">AI-generated · reviewable</span><strong>Decision review at ${esc(r.clock)}</strong> <span class="when">· evidence ${esc(r.window.start_clock)}–${esc(r.window.end_clock)} · retrospective ${esc(p.retrospective.from_clock)}–${esc(p.retrospective.to_clock)}</span></p>
      <p class="when small">${esc(r.label || "")} ${esc(r.provider)} · ${esc(r.model)}${served} · effort ${esc(r.effort || "default")} · prompt ${esc(r.prompt_version)} · ${r.cached ? "from cache, " : ""}generated ${esc(r.generated_at || "")}.</p>
      <span class="section-label">Recommendation (AI, conditional)</span>
      <ul class="recs"><li class="rec"><span class="tag">${esc(optionName(rec.choice))}</span>${rec.condition ? `<em>${esc(rec.condition)}</em> — ` : ""}${esc(rec.rationale)} ${chips(rec.evidence_ids, p)}</li></ul>
      <span class="section-label">Review or practice takeaway</span>
      <p>${esc(a.practice_takeaway)}</p>
      <span class="section-label">Estimate drop, retrospective (association, not cause)</span>
      <div class="retro-block"><p><span class="tag">${esc(attributionName(attribution.category))}</span>${esc(attribution.explanation)} ${chips(attribution.evidence_ids, p)}</p></div>
      <details class="sub"><summary>Weaknesses at the decision time (${(a.weaknesses || []).length})</summary>${weaknesses}</details>
      <details class="sub"><summary>Alternatives compared (${count}) · no win probability is given for unplayed options</summary>${options}
        ${missing ? `<span class="section-label">Missing information that could change this</span><ul class="missing">${missing}</ul>` : ""}
      </details>
      ${packetView(p)}`;
  }

  function setupView(r) {
    const s = r.setup || {};
    return `<p class="unavailable"><strong>AI review is not set up.</strong> No review was requested and none is shown.</p>
      <p class="when">Provider ${esc(s.provider || "none")} · model ${esc(s.model || "none")} · prompt ${esc(r.prompt_version || "")}</p>
      <ul>${(s.missing || []).map((m) => `<li>${esc(m)}</li>`).join("")}</ul>
      <details><summary>How to set it up</summary><ol>${(s.setup || []).map((m) => `<li>${esc(m)}</li>`).join("")}</ol></details>
      <p class="when">The evidence that would be sent:</p>${packetView(r.packet)}`;
  }

  function errorView(r, request) {
    const problems = (r.problems || []).map((p) => `<li>${esc(p)}</li>`).join("");
    return `<p class="unavailable"><strong>No review for ${clock(request.elapsed)}.</strong> ${esc(r.error || r.reason || "The request failed.")}</p>
      ${problems ? `<details open><summary>Checks that failed</summary><ul>${problems}</ul></details>` : ""}
      <p class="when">${r.kind ? `Kind: ${esc(r.kind)}. ` : ""}No earlier review is shown in its place.</p>
      ${r.retryable !== false ? `<p><button type="button" class="action" data-decision="retry">Retry ${clock(request.elapsed)}</button></p>` : ""}
      ${packetView(r.packet)}`;
  }

  function markers(gameId) {
    if (!result || !state || String(gameId) !== String(state.game)) return [];
    const p = result.packet;
    const choice = (result.analysis.recommendation || {}).choice;
    return (result.analysis.options || []).flatMap((o) => {
      const item = o.destination_ref && o.destination_ref !== "none" ? p.items.find((i) => i.id === o.destination_ref) : null;
      const pos = item && item.data && item.data.position;
      if (!pos) return [];
      return [{ x: pos.x, y: pos.y, strong: o.option === choice, label: `${optionName(o.option)}: ${item.label.replace(/^Reference: /, "")} at ${item.clock} · route not verified` }];
    });
  }

  function updatePin() {
    if (!pin) return;
    if (!state) {
      pin.hidden = true;
      return;
    }
    const now = context();
    const here = now && now.elapsed === state.decisionElapsed && String(now.team) === String(state.team);
    pin.hidden = false;
    pin.innerHTML = `Decision review: <strong>${esc(state.squadName)}</strong> at <strong>${clock(state.decisionElapsed)}</strong> <span class="when">(suggested review time, not a proven mistake time)</span>${here ? "" : ` ${seekChip(state.decisionElapsed, `Go to ${clock(state.decisionElapsed)}`)}`}`;
  }

  function update() {
    updatePin();
    if (!result || !box) return;
    const slot = box.querySelector(".stale-slot");
    const now = context();
    if (!slot || !now) return;
    if (String(now.team) !== String(state.team)) {
      slot.innerHTML = `<p class="stale">This review is for ${esc(state.squadName)} at ${esc(result.clock)}; another squad is selected now.</p>`;
    } else if (now.t !== result.t) {
      slot.innerHTML = now.t > result.t
        ? `<p class="stale viewing">Viewing ${clock(now.elapsed)}, after the decision time ${esc(result.clock)}. The review's recommendations use only evidence up to ${esc(result.clock)}.</p>`
        : `<p class="stale">This review is for ${esc(result.clock)}. The replay now shows ${clock(now.elapsed)}.</p>`;
    } else {
      slot.innerHTML = "";
    }
  }

  function showError(message) {
    const el = box && box.querySelector(".decision-error");
    if (!el) return;
    el.hidden = !message;
    el.textContent = message || "";
  }

  function readTime() {
    const input = box.querySelector("#decision-time");
    const elapsed = parseClock(input && input.value);
    if (elapsed === null) {
      showError("Enter the decision time as mm:ss.");
      return null;
    }
    if (elapsed < 0 || elapsed >= state.item.episode_end_elapsed) {
      showError(`The decision time must be between 00:00 and the episode end, ${clock(state.item.episode_end_elapsed)}.`);
      return null;
    }
    showError("");
    state.decisionElapsed = elapsed;
    updatePin();
    return elapsed;
  }

  async function run(request) {
    lastRequest = request;
    const mine = ++token;
    if (controller) controller.abort("cancel");
    controller = new AbortController();
    const timer = setTimeout(() => controller.abort("timeout"), 105000);
    result = null;
    redraw();
    setBody(`<p class="when">Reviewing ${esc(state.squadName)} at ${clock(request.elapsed)}. The replay stays usable while this runs.</p><p><button type="button" class="action quiet" data-decision="cancel">Cancel</button></p>`);
    let r;
    try {
      const response = await fetch(`/api/matches/${request.game}/decision-review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: request.kind, team: request.team, start_t: request.start_t, episode_start_t: request.episode_start_t, decision_t: request.decision_t }),
        signal: controller.signal,
      });
      r = await response.json().catch(() => ({ status: "error", error: `HTTP ${response.status}`, retryable: true }));
    } catch (error) {
      r = controller.signal.reason === "timeout"
        ? { status: "error", kind: "timeout", error: "The review timed out.", retryable: true }
        : { status: "error", kind: "cancelled", error: "The review was cancelled.", retryable: true };
    } finally {
      clearTimeout(timer);
    }
    if (mine !== token || !state) return;
    try {
      if (r.status === "ok") {
        result = r;
        setBody(resultView(r));
        update();
        redraw();
      } else if (r.status === "not_configured") {
        setBody(setupView(r));
      } else if (["finished", "squad_eliminated", "invalid", "unavailable"].includes(r.status)) {
        setBody(`<p class="unavailable">${esc(r.reason || "Not available.")}</p>`);
      } else {
        setBody(errorView(r, request));
      }
    } catch (error) {
      console.error(error);
      setBody(`<p class="unavailable">The review could not be displayed.</p>`);
    }
  }

  function startReview() {
    const elapsed = readTime();
    if (elapsed === null) return;
    seekTo(elapsed);
    run({
      game: state.game, team: state.team, kind: state.kind, elapsed,
      start_t: state.item.start_t, episode_start_t: state.item.episode_start_t, decision_t: state.start + elapsed,
    });
  }

  function open(item) {
    const now = context();
    if (!now || !box) return;
    token += 1;
    result = null;
    const suggested = Math.max(0, Number(item.episode_start_elapsed) - LEAD_S);
    state = {
      game: now.game, team: String(item.squad_id ?? now.team), squadName: item.squad_name || now.squadName,
      kind: item.prediction_kind, item, start: now.start, decisionElapsed: suggested, suggestedElapsed: suggested,
    };
    render(`<p class="when">Nothing has been requested. Adjust the decision time if needed, then click “Review decision”.</p>`);
    redraw();
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  box && box.addEventListener("click", (event) => {
    try {
      const chip = event.target.closest("button.evidence");
      if (chip) {
        const elapsed = Number(chip.dataset.elapsed);
        if (Number.isFinite(elapsed)) seekTo(elapsed);
        return;
      }
      const action = event.target.closest("button[data-decision]");
      if (!action || !state) return;
      const what = action.dataset.decision;
      if (what === "run") startReview();
      else if (what === "seek") { const e = readTime(); if (e !== null) seekTo(e); }
      else if (what === "suggested") { state.decisionElapsed = state.suggestedElapsed; box.querySelector("#decision-time").value = clock(state.suggestedElapsed); showError(""); updatePin(); }
      else if (what === "retry" && lastRequest) { seekTo(lastRequest.elapsed); run(lastRequest); }
      else if (what === "cancel" && controller) controller.abort("cancel");
    } catch (error) {
      console.error(error);
    }
  });
  box && box.addEventListener("change", (event) => { if (event.target.id === "decision-time") readTime(); });
  pin && pin.addEventListener("click", (event) => {
    const chip = event.target.closest("button.evidence");
    if (chip) seekTo(Number(chip.dataset.elapsed));
  });

  function init(options) {
    context = options.context;
    seekTo = options.seek;
    redraw = options.redraw;
  }

  function reset() {
    token += 1;
    if (controller) controller.abort("cancel");
    state = null;
    result = null;
    lastRequest = null;
    updatePin();
    if (box) box.innerHTML = `<p class="when">Choose “Review decision” on a “Win estimate fell” moment to review the time before it.</p>`;
  }

  return { init, open, update, reset, markers };
})();
