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
  let running = false;

  const h = () => window.Coach.helpers;
  const esc = (v) => h().esc(v);
  const clock = (s) => h().clock(s);
  const kindName = (kind) => (
    kind === "held_out" ? "held-out"
      : kind === "new_match" ? "new-match prediction · model trained on the original 51 games"
        : "final model, fitted on these games (in-sample)"
  );
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
    const tagClass = state.kind === "held_out" ? "heldout" : state.kind === "new_match" ? "newmatch" : "fitted";
    const fittedNote = state.kind === "final_model_fitted"
      ? `<p class="when">This highlight comes from the final model, which was fitted on this match. Its estimates appear only in the retrospective part of the review.</p>`
      : state.kind === "new_match"
        ? `<p class="when">This highlight is a new-match prediction from the model trained on the original 51 games. It was not fitted on this match and is not a held-out evaluation.</p>`
        : "";
    return `<p><strong>${state.teamTag(state.team, item.squad_name || state.squadName)}</strong> · <span class="tag ${tagClass}">${esc(kindName(state.kind))}</span></p>
      <dl class="episode">
        <dt>Representative window</dt><dd>${seekChip(item.start_elapsed, U.span(item.start_elapsed, item.end_elapsed))} ${U.finite(item.window_s) ? `${item.window_s} s · ` : ""}estimate ${U.pct(item.before)} → ${U.pct(item.after)} (${U.pp(item.change_pp)})</dd>
        <dt>Full episode</dt><dd>${seekChip(item.episode_start_elapsed, U.span(item.episode_start_elapsed, item.episode_end_elapsed))} ${U.text(item.windows_merged)} overlapping window${item.windows_merged === 1 ? "" : "s"}; the change above is for the representative window only</dd>
      </dl>
      ${fittedNote}`;
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
        <div class="interp"><span class="section-label">Interpretation</span><p>${esc(w.interpretation)}</p></div>
        ${w.uncertainty ? `<div class="uncert"><span class="section-label">Uncertainty</span><p>${esc(w.uncertainty)}</p></div>` : ""}
      </article>`).join("") || `<p class="when">No weakness is supported by the evidence at this time.</p>`;
    const options = (a.options || []).map((o) => {
      const chosen = rec.choice === o.option;
      const destination = o.destination_ref && o.destination_ref !== "none"
        ? `<p class="when">Reference position: ${chips([o.destination_ref], p)} ${esc(refLabel(p, o.destination_ref))}. Known coordinates; the route there is not verified.</p>`
        : `<p class="when">No destination position.</p>`;
      const observer = o.observer_only || {};
      return `<article class="factor option${chosen ? " chosen" : ""}">
        <header><strong>${esc(optionName(o.option))}</strong>${chosen ? '<span class="tag">recommended</span>' : ""}</header>
        ${destination}
        <div class="facts"><span class="section-label">Evidence</span>${facts(o.evidence, p)}</div>
        <div class="interp"><span class="section-label">Expected tactical benefit</span><p>${esc(o.expected_benefit)}</p></div>
        <div class="interp"><span class="section-label">Cost</span><p>${esc(o.cost)}</p></div>
        <div class="uncert"><span class="section-label">Uncertainty</span><p>${esc(o.uncertainty)}</p></div>
        <p class="when"><span class="tag">${observer.depends ? "relies on observer-only information" : "uses information the squad could perceive"}</span>${esc(observer.explanation || "")}</p>
      </article>`;
    }).join("");
    const attribution = a.drop_attribution || {};
    const missing = (a.missing_information || []).map((m) => `<li>${esc(m)}</li>`).join("");
    const count = (a.options || []).length;
    // Lead with the chosen option, its condition once, and why.
    const h = window.Coach.helpers;
    const chosen = (a.options || []).find((o) => o.option === rec.choice);
    const destination = chosen && chosen.destination_ref && chosen.destination_ref !== "none" ? (p.items.find((i) => i.id === chosen.destination_ref) || {}).label?.replace(/^Reference: /, "") || "" : "";
    const move = rec.choice && rec.choice !== "abstain"
      ? h.nextMove({
        action: `${esc(optionName(rec.choice))}${destination ? ` <span class="when">toward ${esc(destination)}; route not verified</span>` : ""}`,
        condition: esc(rec.condition || ""),
      })
      : h.nextMove({ check: esc(rec.condition || (a.missing_information || [])[0] || h.firstSentence(rec.rationale)) });
    const usage = meta.usage || {};
    const details = [
      ["Review", `${esc(r.squad_name)} at ${esc(r.clock)} (replay t ${esc(r.t)}); evidence ${esc(r.window.start_clock)}–${esc(r.window.end_clock)}; retrospective ${esc(p.retrospective.from_clock)}–${esc(p.retrospective.to_clock)}`],
      ["Provider", esc(r.provider || "Unknown")],
      ["Model", `${esc(r.model || "Unknown")}${served}`],
      ["Thinking level", esc(r.effort || "model default")],
      ["Prompt", esc(r.prompt_version || "Unknown")],
      ["Generated", esc(r.generated_at || "Unknown")],
      ["Latency", r.cached ? "served from cache" : r.latency_s != null ? `${esc(r.latency_s)} s` : "Unknown"],
      ["Cache", r.cached ? "from cache (same evidence, provider, model and prompt)" : "new response, now cached"],
      ["Tokens", usage.input_tokens != null ? `${esc(usage.input_tokens)} in · ${esc(usage.output_tokens)} out${usage.thinking_tokens != null ? ` · ${esc(usage.thinking_tokens)} thinking` : ""}` : "Unknown"],
    ];
    // decision-v5 and later: the short validated sentence leads; earlier cached
    // reviews keep their option block. Reasoning and alternatives are in Details.
    const short = typeof a.next_action === "string" && a.next_action.trim();
    const visible = short
      ? `<div class="next-move${/^Insufficient information/i.test(short) ? " none" : ""}"><span class="section-label">Recommended action</span><p class="action">${esc(short)}</p></div>`
      : move;
    const why = rec.choice && rec.choice !== "abstain" ? esc(h.firstSentence(h.withoutCondition(rec.rationale, rec.condition))) : "";
    return `<div class="stale-slot"></div>
      <p class="review-for">${state.teamTag(state.team, r.squad_name)} · decision reviewed at <strong>${esc(r.clock)}</strong></p>
      ${visible}
      <details class="sub"><summary>Details</summary>
        ${short ? `<span class="section-label">Recommended option</span><p>${esc(optionName(rec.choice))}${destination ? ` <span class="when">toward ${esc(destination)}; route not verified</span>` : ""}${rec.condition ? ` — <em>${esc(rec.condition)}</em>` : ""}</p>` : ""}
        ${why ? `<span class="section-label">Why</span><p>${why}</p>` : ""}
        <span class="section-label">Practice</span><p>${esc(a.practice_takeaway)}</p>
        <span class="section-label">Why the estimate fell <span class="when">(retrospective; association, not cause)</span></span>
        <div class="retro-block"><p><span class="tag">${esc(attributionName(attribution.category))}</span>${esc(attribution.explanation)} ${chips(attribution.evidence_ids, p)}</p></div>
        <details class="sub"><summary>Weaknesses at the decision time (${(a.weaknesses || []).length})</summary>${weaknesses}</details>
        <details class="sub"><summary>Alternatives compared (${count}) · no win probability for unplayed options</summary>${options}
          ${missing ? `<span class="section-label">Missing information that could change this</span><ul class="missing">${missing}</ul>` : ""}
        </details>
      </details>
      <details class="sub"><summary>Review details</summary>
        <p class="when">${esc(r.label || "")}</p>
        <dl class="facts">${details.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>
        <span class="section-label">Evidence cited by the recommendation</span><p>${chips(rec.evidence_ids, p) || '<span class="when">none</span>'}</p>
        ${packetView(p)}
      </details>`;
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
    const who = request && request.squadName ? `${esc(request.squadName)} at ` : "";
    return `<p class="unavailable"><strong>No review for ${who}${clock(request.elapsed)}.</strong> ${esc(r.error || r.reason || "The request failed.")}</p>
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
    const notice = (text, explanation, viewing) =>
      `<p class="stale tip${viewing ? " viewing" : ""}" tabindex="0" data-tip="${esc(explanation)}">${text}<span class="sr-only"> ${esc(explanation)}</span></p>`;
    if (String(now.game) !== String(state.game)) {
      slot.innerHTML = notice("Review: another match", "This review is for another match; it does not describe what is shown.");
    } else if (String(now.team) !== String(state.team)) {
      slot.innerHTML = notice(
        `Review: ${state.teamTag(state.team, state.squadName)} ${esc(result.clock)} · Viewing: ${state.teamTag(now.team)} ${clock(now.elapsed)}`,
        `This review is for ${state.squadName} at ${result.clock}. Another squad is selected now.`,
      );
    } else if (now.t !== result.t) {
      slot.innerHTML = notice(
        `Review: ${esc(result.clock)} · Viewing: ${clock(now.elapsed)}`,
        now.t > result.t
          ? `Viewing ${clock(now.elapsed)}, after the decision time ${result.clock}. The review's recommendations use only evidence up to ${result.clock}.`
          : `This review is for ${result.clock}. The replay now shows ${clock(now.elapsed)}.`,
        now.t > result.t,
      );
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

  function setRunEnabled(enabled) {
    const runButton = box && box.querySelector("[data-decision='run']");
    if (runButton) runButton.disabled = !enabled;
  }

  async function run(request) {
    if (running || !request || !state) return;
    lastRequest = request;
    const mine = ++token;
    running = true;
    setRunEnabled(false);
    controller = new AbortController();
    const timer = setTimeout(() => controller.abort("timeout"), 105000);
    result = null;
    redraw();
    setBody(`${ReviewStatus.loadingMarkup(esc(request.squadName), clock(request.elapsed))}<p><button type="button" class="action quiet" data-decision="cancel">Cancel</button></p>`);
    const stopClock = ReviewStatus.startClock(box.querySelector(".decision-body"), ReviewStatus.announce);
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
      r = ReviewStatus.classifyFailure(controller.signal);
    } finally {
      clearTimeout(timer);
      stopClock();
      if (mine === token) {
        running = false;
        setRunEnabled(true);
      }
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
      ReviewStatus.announce(ReviewStatus.completionAnnouncement(r));
    } catch (error) {
      console.error(error);
      setBody(`<p class="unavailable">The review could not be displayed.</p>`);
      ReviewStatus.announce("Review failed");
    }
  }

  function startReview() {
    if (running) return;
    const elapsed = readTime();
    if (elapsed === null) return;
    seekTo(elapsed);
    run(reviewRequest(elapsed));
  }

  function reviewRequest(elapsed) {
    return { game: state.game, team: state.team, squadName: state.squadName, kind: state.kind, elapsed,
      start_t: state.item.start_t, episode_start_t: state.item.episode_start_t, decision_t: state.start + elapsed };
  }

  // The key-moment action has already sought and synchronized the replay.
  async function start(expected) {
    if (running || !state) return false;
    const now = context();
    if (!now || String(now.game) !== String(expected.game) || String(now.team) !== String(expected.team)
      || now.t !== expected.t || String(state.game) !== String(expected.game) || String(state.team) !== String(expected.team)) return false;
    const elapsed = readTime();
    if (elapsed === null || state.start + elapsed !== expected.t) return false;
    await run(reviewRequest(elapsed));
    return true;
  }

  function open(item) {
    if (running) return;
    const now = context();
    if (!now || !box) return;
    token += 1;
    result = null;
    if (!Number.isFinite(item.episode_start_elapsed) || !Number.isFinite(item.episode_end_elapsed)
      || item.episode_start_elapsed < 0 || item.episode_end_elapsed <= item.episode_start_elapsed) return;
    if (item.squad_id != null && String(item.squad_id) !== String(now.team)) return;
    const suggested = Math.max(0, item.episode_start_elapsed - LEAD_S);
    state = {
      game: now.game, team: String(item.squad_id ?? now.team), squadName: item.squad_name || now.squadName,
      kind: item.prediction_kind, item, start: now.start, decisionElapsed: suggested, suggestedElapsed: suggested,
      // This match's teamId -> name/color, kept with the review.
      teamTag: window.UI.teamIndex(now.squads),
    };
    render(`<p class="when">Nothing has been requested. Adjust the decision time if needed, then click “Review decision”.</p>`);
    redraw();
    box.scrollIntoView({ behavior: "smooth", block: "start" });
    return suggested;
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
      else if (what === "retry" && lastRequest && !running) { seekTo(lastRequest.elapsed); run(lastRequest); }
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
    if (running) return;
    token += 1;
    if (controller) controller.abort("cancel");
    state = null;
    result = null;
    lastRequest = null;
    updatePin();
    if (box) box.innerHTML = `<p class="when">Choose “Review decision” on a “Win estimate fell” moment to review the time before it.</p>`;
  }

  return { init, open, start, update, reset, markers };
})();
