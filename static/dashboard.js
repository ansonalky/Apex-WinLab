const matchSelect = document.querySelector("#match");
const squadSelect = document.querySelector("#squad");
const timeInput = document.querySelector("#time");
const clock = document.querySelector("#clock");
const shown = document.querySelector("#shown");
const meta = document.querySelector("#match-meta");
const note = document.querySelector("#coord-note");
const roster = document.querySelector("#roster");
const curveBox = document.querySelector("#curve");
const heldoutBox = document.querySelector("#heldout");
const evalBox = document.querySelector("#evaluation");
const heldoutHighlights = document.querySelector("#highlights-heldout");
const finalHighlights = document.querySelector("#highlights-final");
const outcome = document.querySelector("#outcome");
const canvas = document.querySelector("#plot");
const ctx = canvas.getContext("2d");

let summary = null;
let frame = null;
let curve = null;
let heldout = null;
let mapConfig = null;
let currentRing = null;
let requestToken = 0;
// At most one frame request runs at a time. Slider input that arrives while
// it runs only replaces pendingSeek, so a fast scrub asks for its newest value
// next and skips the ones in between.
let inFlight = null;
let pendingSeek = null;
let highlightToken = 0;

function mmss(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

// Names come from telemetry; never insert them as HTML.
function esc(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function textOrUnknown(value) {
  return value === null || value === undefined || value === "" ? "unknown" : esc(value);
}

function pct(value) {
  return `${(value * 100).toFixed(1)}%`;
}

// Throws on an HTTP error, so an error page is never parsed as data.
async function getJSON(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

async function loadBackend() {
  const box = document.querySelector("#backend");
  try {
    const info = await (await fetch("/api/backend")).json();
    box.className = `backend ${info.backend === "tiger" ? "tiger" : "files"}`;
    box.innerHTML = `Data backend: <strong>${esc(info.label || info.backend)}</strong> <span class="when">${esc(info.detail || "")}</span>`;
  } catch (error) {
    box.textContent = "Data backend: unknown";
  }
}

async function loadMatches() {
  const matches = await (await fetch("/api/matches")).json();
  matchSelect.innerHTML = "";
  for (const match of matches) {
    const option = document.createElement("option");
    option.value = match.game_id;
    const quality = match.quality_score === null ? "unknown" : Number(match.quality_score).toFixed(2);
    const tag = match.heldout ? "[held-out test] " : "";
    option.textContent = `${tag}${match.event || "Untitled"} · ${match.map_name || "map unknown"} · quality ${quality}`;
    matchSelect.append(option);
  }
  // Demo default: the first held-out test game, so the primary view has evidence.
  // A link from Performance selects one game and does not change seeking.
  const requested = new URLSearchParams(location.search).get("game");
  const first = matches.find((match) => String(match.game_id) === requested)
    || matches.find((match) => match.heldout)
    || matches[0];
  if (first) {
    matchSelect.value = String(first.game_id);
    await loadMatch(first.game_id);
  }
}

// The slider value is seconds since Playing start. The requested replay
// second is playing start + that value; it is never a frame-array index.
// Every seek and match selection takes a new token; a response that comes
// back after a newer one started is dropped.
function replayStart() {
  return summary.playing_start ?? summary.first_t ?? 0;
}

async function loadMatch(gameId) {
  const token = ++requestToken;
  if (inFlight) inFlight.controller.abort();
  inFlight = null;
  pendingSeek = null;
  highlightToken += 1;
  if (window.Coach) safely(() => window.Coach.reset());
  if (window.DecisionReview) safely(() => window.DecisionReview.reset());
  heldoutHighlights.innerHTML = `<p class="when">Loading…</p>`;
  finalHighlights.innerHTML = `<p class="when">Loading…</p>`;
  timeInput.disabled = true;
  let nextSummary, nextCurve, nextHeldout, evaluation;
  try {
    [nextSummary, nextCurve, nextHeldout, evaluation] = await Promise.all([
      getJSON(`/api/matches/${gameId}`),
      getJSON(`/api/matches/${gameId}/curve`),
      getJSON(`/api/matches/${gameId}/heldout`),
      getJSON("/api/evaluation"),
    ]);
  } catch (error) {
    if (token !== requestToken) return;
    meta.textContent = `Match ${gameId} could not be loaded.`;
    heldoutHighlights.innerHTML = `<p class="unavailable">Could not load these highlights.</p>`;
    finalHighlights.innerHTML = `<p class="unavailable">Could not load these highlights.</p>`;
    console.error(error);
    return;
  }
  const map = await getJSON(`/api/map-config?map=${encodeURIComponent(nextSummary.map_name || "")}`)
    .catch(() => ({ supported: false, message: "Map background unavailable: map configuration could not be loaded." }));
  if (token !== requestToken) return;
  summary = nextSummary;
  curve = nextCurve;
  heldout = nextHeldout;
  mapConfig = map;
  meta.textContent = [
    summary.event,
    summary.map_name,
    `quality ${summary.quality_score}`,
    `session ${summary.session_id}`,
    summary.winner && summary.winner.teamName ? `verified winner ${summary.winner.teamName}` : "winner unknown",
  ].filter(Boolean).join(" · ");
  note.textContent = mapConfig && mapConfig.supported
    ? "Positions use the Overstat mapCord transform on the matching 2048 map. z is omitted. Only the current ring is shown."
    : (mapConfig && mapConfig.message) || "Map background unavailable.";
  squadSelect.innerHTML = "";
  for (const squad of summary.squads) {
    const option = document.createElement("option");
    option.value = String(squad.teamId);
    option.textContent = `${squad.teamName} (${squad.teamId})`;
    squadSelect.append(option);
  }
  const linkedTeam = new URLSearchParams(location.search).get("team");
  const linkedGame = new URLSearchParams(location.search).get("game");
  if (linkedTeam && String(gameId) === linkedGame && summary.squads.some((squad) => String(squad.teamId) === linkedTeam)) {
    squadSelect.value = linkedTeam;
  } else if (summary.winner && summary.squads.some((squad) => String(squad.teamId) === String(summary.winner.teamId))) {
    squadSelect.value = String(summary.winner.teamId);
  }
  // Bounds are set once per match, from its first and last stored replay second.
  timeInput.min = "0";
  timeInput.max = String(Math.max(0, (summary.last_t ?? replayStart()) - replayStart()));
  timeInput.value = "0";
  timeInput.disabled = false;
  frame = null;
  currentRing = null;
  renderEvaluation(evaluation);
  clock.textContent = mmss(0);
  loadHighlights();
  await loadFrame(0);
}

// The selected time shows at once; the displayed frame's own time is labeled
// separately until the response for the selection arrives.
function seek(elapsed) {
  clock.textContent = mmss(elapsed);
  shown.textContent = frame ? `loading · showing ${mmss(frame.elapsed)}` : "loading";
  if (inFlight) {
    pendingSeek = elapsed;
    return;
  }
  loadFrame(elapsed);
}

// One request returns everything for one second: frame, ring and the
// predictions made at that second, so they always share a timestamp.
async function loadFrame(elapsed) {
  const token = ++requestToken;
  const controller = new AbortController();
  inFlight = { token, controller };
  const t = replayStart() + elapsed;
  let next = null;
  try {
    next = await getJSON(`/api/matches/${summary.game_id}/at/${t}`, { signal: controller.signal });
  } catch (error) {
    if (error.name !== "AbortError" && token === requestToken) {
      shown.textContent = `frame at ${mmss(elapsed)} unavailable`;
      console.error(error);
    }
  } finally {
    if (inFlight && inFlight.token === token) inFlight = null;
  }
  if (token !== requestToken) return;
  if (next) {
    frame = next;
    currentRing = next.ring || { state: "unavailable" };
    renderOutcome();
    draw();
    renderRoster();
    renderPredictions();
    if (window.Coach) safely(() => window.Coach.update());
    if (window.DecisionReview) safely(() => window.DecisionReview.update());
  }
  if (pendingSeek !== null) {
    const latest = pendingSeek;
    pendingSeek = null;
    loadFrame(latest);
    if (frame) shown.textContent = `loading · showing ${mmss(frame.elapsed)}`;
  } else if (frame) {
    shown.textContent = frame.elapsed === Number(timeInput.value) ? `showing ${mmss(frame.elapsed)}` : `showing ${mmss(frame.elapsed)} (latest stored second)`;
    loadTactical();
  }
}

// Explanatory panel, loaded once seeking has been still for 250 ms.
let tacticalTimer = null;
function loadTactical() {
  clearTimeout(tacticalTimer);
  tacticalTimer = setTimeout(() => {
    if (window.Tactical && frame && summary) window.Tactical.load(summary.game_id, frame.t, selectedTeam(), summary.squads);
  }, 250);
}

// A recorded result from the replay, not a model output.
function renderOutcome() {
  const last = summary.last_t ?? frame.t;
  const winner = summary.winner && summary.winner.teamName;
  if (frame.finished) {
    outcome.hidden = false;
    outcome.className = "outcome";
    outcome.innerHTML = `<strong>Match finished</strong> at ${mmss(summary.conclusion_elapsed)} (replay t ${summary.conclusion_t}). ` +
      `Verified winner: <strong>${winner ? esc(winner) : "unknown"}</strong>. This is the recorded result, not a model prediction.`;
  } else if (summary.conclusion_t == null && frame.t >= last) {
    outcome.hidden = false;
    outcome.className = "outcome cut";
    outcome.textContent = "Replay ends before match conclusion. No frames after this one are recorded.";
  } else {
    outcome.hidden = true;
  }
}

function selectedTeam() {
  return squadSelect.value;
}

function colorFor(teamId) {
  const squad = (summary.squads || []).find((item) => String(item.teamId) === String(teamId));
  return squad ? squad.color : "#888";
}

function squadName(teamId) {
  const squad = (summary.squads || []).find((item) => String(item.teamId) === String(teamId));
  return squad ? squad.teamName : String(teamId);
}

// AI panels are optional: a failure there must never stop the replay.
function safely(fn, fallback) {
  try {
    return fn();
  } catch (error) {
    console.error(error);
    return fallback;
  }
}

function draw() {
  if (!frame) return;
  const markers = window.DecisionReview ? safely(() => window.DecisionReview.markers(summary.game_id), []) : [];
  window.ApexMap.draw({
    markers,
    canvas,
    ctx,
    frame,
    selectedTeam: selectedTeam(),
    colorFor,
    squadName,
    ring: currentRing,
    mapConfig,
  });
}

function bar(value, max, className) {
  if (value == null || max == null || !(max > 0)) return `<div class="bar ${className}"></div>`;
  const width = Math.max(0, Math.min(100, (100 * value) / max));
  return `<div class="bar ${className}"><span style="width:${width}%"></span></div>`;
}

function renderRoster() {
  const team = selectedTeam();
  const players = (frame.players || []).filter((player) => String(player.teamId) === team);
  if (!players.length) {
    roster.innerHTML = `<p class="unavailable">No players for this squad at this time.</p>`;
    return;
  }
  const squadOut = (frame.eliminated_squads || {})[team];
  roster.innerHTML = players.map((player) => {
    // Display only: a confirmed-eliminated player shows 0 health and shields
    // with their maximums kept. Telemetry and model inputs are unchanged.
    // Downed players and "dead" (respawnable) players show their telemetry.
    const playerOut = player.status === "eliminated";
    const zeroed = (playerOut || squadOut != null) && player.status !== "downed";
    const health = zeroed ? 0 : player.health;
    const shields = zeroed ? 0 : player.shields;
    const status = !playerOut && zeroed
      ? `${textOrUnknown(player.status)} · squad eliminated at ${mmss(squadOut - replayStart())}`
      : textOrUnknown(player.status);
    return `
    <article class="player${zeroed ? " out" : ""}">
      <header><strong>${textOrUnknown(player.name)}</strong><span>${status}</span></header>
      <div>Health ${textOrUnknown(health)} / ${textOrUnknown(player.healthMax)}</div>
      ${bar(health, player.healthMax, "hp")}
      <div>Shields ${textOrUnknown(shields)} / ${textOrUnknown(player.shieldMax)}</div>
      ${bar(shields, player.shieldMax, "sh")}
    </article>
  `;
  }).join("");
}

// Predictions are made at every replay second before the cut-off, at the same
// second as the frame on screen (frame.live). Nothing is interpolated.
function probabilityTable(squads, valueKey, valueLabel, baselineKey) {
  const team = selectedTeam();
  const rows = [...squads]
    .sort((a, b) => b[valueKey] - a[valueKey])
    .map((squad) => `<tr class="${String(squad.teamId) === team ? "selected" : ""} ${squad.eliminated ? "out" : ""}">
        <td><button type="button" class="squad-name" data-team-id="${esc(squad.teamId)}">${textOrUnknown(squad.team_name)}${squad.eliminated ? " · eliminated" : ""}</button></td>
        <td class="num">${pct(squad[valueKey])}</td>
        ${baselineKey ? `<td class="num">${pct(squad[baselineKey])}</td>` : ""}
      </tr>`).join("");
  return `<table class="probs"><thead><tr><th>Squad</th><th class="num">${valueLabel}</th>${baselineKey ? '<th class="num">Baseline</th>' : ""}</tr></thead><tbody>${rows}</tbody></table>`;
}

// The verified winner from the manifest, after WinnerDetermined. Not a model output.
function recordedOutcome() {
  const winner = summary.winner && summary.winner.teamId;
  const squads = (summary.squads || []).map((squad) => ({
    teamId: squad.teamId,
    team_name: squad.teamName,
    outcome: String(squad.teamId) === String(winner) ? 1 : 0,
  }));
  return `<p><span class="tag outcome-tag">recorded outcome</span>Match finished at ${mmss(summary.conclusion_elapsed)}. The verified winner is 100% and every other squad 0%. This is the recorded result, not a model prediction.</p>` +
    probabilityTable(squads, "outcome", "Recorded result");
}

function inputsFor(point) {
  const squad = point.squads.find((item) => String(item.teamId) === selectedTeam());
  if (!squad || !squad.features) return "";
  const cells = Object.entries(squad.features)
    .map(([name, value]) => `<tr><td>${esc(name)}</td><td class="num">${value === null ? "unknown" : Number(value).toFixed(3)}</td></tr>`).join("");
  return `<details><summary>Model inputs for the selected squad at ${mmss(point.elapsed)}</summary><table class="probs">${cells}</table>
    <p class="when">hp_frac sums health and shields over alive and downed players; a downed player's health is the bleed-out pool, which the replay reports as full.</p></details>`;
}

function livePanel(live, kindLabel) {
  if (!live || !live.available) return "";
  if (!live.point) {
    return `<p class="unavailable">No model prediction at ${mmss(frame.elapsed)}: ${esc(live.reason || "")}.</p>`;
  }
  const point = live.point;
  const provenance = live.provenance || {};
  const eligibility = point.eligibility === "unverified"
    ? `<p class="unavailable">Eligibility unverified at this second${(point.eligibility_reasons || []).length ? ` (${esc(point.eligibility_reasons.join(", "))})` : ""}: no squad is excluded.</p>`
    : `<p class="when">Eligibility verified: squads eliminated by ${mmss(point.elapsed)} have probability 0; the rest are renormalized.</p>`;
  return `<p class="when"><strong>${kindLabel} at ${mmss(point.elapsed)}</strong> (replay t ${point.t}), the same second as the map and roster. Sampled every second; ${esc(provenance.file || "")}, sha256 ${esc((provenance.sha256 || "").slice(0, 12))}.</p>
    ${eligibility}
    <p class="when">${point.in_contention} squads in contention.</p>
    ${probabilityTable(point.squads, "model", "Model (constrained)", "uniform_contention")}
    ${inputsFor(point)}`;
}

// The frozen evaluation's own 30-second checkpoints, up to the slider time.
function checkpointHistory(view, selection) {
  if (!view || !view.available || !selection) return "";
  const team = selectedTeam();
  const history = view.points.slice(0, selection.index + 1).map((past) => {
    const squad = past.squads.find((item) => String(item.teamId) === team);
    return squad ? `<div>${mmss(past.elapsed)} · model ${pct(squad.model)} · baseline ${pct(squad.uniform_contention)}${squad.eliminated ? " · eliminated" : ""}</div>` : "";
  }).join("");
  return `<details class="history"><summary>Selected squad at the 30-second evaluation checkpoints (historical)</summary>${history || "<div>No checkpoint for this squad.</div>"}</details>`;
}

function predictionPanel(view, live, selection, label, kindLabel) {
  if (!view || !view.available) {
    return `<p class="unavailable">Not available</p><p>${esc((view && (view.reason || view.limitation)) || "")}</p>`;
  }
  const outcomePart = frame && frame.finished ? recordedOutcome() : "";
  const livePart = frame && frame.finished
    ? (live && live.available && live.cutoff_t != null
      ? `<p class="when">No model prediction is made after the result. The last per-second prediction was at ${mmss(live.cutoff_t - 1 - replayStart())} (replay t ${live.cutoff_t - 1}); move the slider there to see it.</p>`
      : "")
    : livePanel(live, kindLabel);
  return `${label}${outcomePart}${livePart}${checkpointHistory(view, selection)}`;
}

function renderPredictions() {
  const checkpoints = (frame && frame.checkpoints) || {};
  const live = (frame && frame.live) || {};
  let heldLabel = "";
  if (heldout && heldout.available) {
    const fit = heldout.evaluation_model || {};
    const metrics = heldout.game_metrics;
    heldLabel = `<p><span class="tag heldout">held-out</span>Test session. Frozen evaluation model fit on ${fit.train_sessions} train + ${fit.validation_sessions} validation sessions (l2 ${fit.l2}); not refit.</p>` +
      (metrics ? `<p class="when">This game, at the 30-second checkpoints: log loss ${metrics.model.log_loss.toFixed(3)} vs contention baseline ${metrics.uniform_contention.log_loss.toFixed(3)}.</p>` : "");
  }
  heldoutBox.innerHTML = predictionPanel(heldout, live.heldout, checkpoints.heldout, heldLabel, "Held-out prediction");
  let fitLabel = "";
  if (curve && curve.available) {
    fitLabel = `<p><span class="tag fitted">fitted on these games</span>Not held-out evidence.</p><p class="when">${esc(curve.limitation)}</p>`;
  }
  curveBox.innerHTML = predictionPanel(curve, live.final, checkpoints.final, fitLabel, "Final-model prediction");
}

function renderEvaluation(evaluation) {
  if (!evaluation.available) {
    evalBox.innerHTML = `<p class="unavailable">Not available</p><p>${esc(evaluation.reason || "")}</p>`;
    return;
  }
  const metrics = evaluation.metrics || {};
  const order = ["model_constrained", "model", "uniform_contention", "most_players", "uniform"];
  const names = {
    model: "Model, raw (before eligibility rule)",
    model_constrained: "Model, eligibility-constrained",
    uniform_contention: "Contention uniform (primary baseline)",
    most_players: "Most standing players",
    uniform: "Lobby uniform",

  };
  const methods = [...order.filter((m) => m in metrics), ...Object.keys(metrics).filter((m) => !order.includes(m))];
  const lines = methods.map((method) => {
    const values = metrics[method];
    return `<div>${esc(names[method] || method)}: log loss ${values.log_loss.toFixed(3)}, Brier ${values.brier.toFixed(3)}, top-1 ${values.top1.toFixed(3)}</div>`;
  }).join("");
  evalBox.innerHTML = `
    <p>Report ${esc(evaluation.generated_at || "")}. Method ${esc(evaluation.prediction_type)}. Held out by session: ${evaluation.held_out ? "yes" : "no"}. Scored at 30-second checkpoints; the per-second predictions above are not part of this report.</p>
    ${evaluation.warning ? `<p>${esc(evaluation.warning)}</p>` : ""}
    <p>${esc(evaluation.limitation)}</p>
    ${lines}
    <p>Calibration: ${esc((evaluation.calibration && evaluation.calibration.status) || "Not available")}</p>
  `;
}

// One list per prediction kind. A click sets the slider, then uses the same
// seek() path as dragging it, so the map, roster, ring, and both probability
// panels stay on that second.
function points(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "unknown";
  return `${n > 0 ? "+" : ""}${n.toFixed(1)}`;
}

function kindName(kind) {
  if (kind === "held_out") return "held-out";
  if (kind === "final_model_fitted") return "final model, fitted on these games";
  return kind || "unknown model";
}

function rangeLine(label, startElapsed, endElapsed, startT, endT, extra) {
  const clock = startElapsed == null || endElapsed == null ? "time unknown" : `${mmss(startElapsed)}–${mmss(endElapsed)}`;
  const replay = startT == null || endT == null ? "" : ` · replay t ${startT}–${endT}`;
  return `<span class="line"><span class="tag">${esc(label)}</span> ${clock}${extra}${replay}</span>`;
}

function eventElapsed(item, event) {
  if (event.elapsed != null && Number.isFinite(Number(event.elapsed))) return Number(event.elapsed);
  if (item.start_elapsed == null || item.start_t == null || event.t == null) return null;
  return Number(item.start_elapsed) + (Number(event.t) - Number(item.start_t));
}

function eventLine(item, event) {
  const elapsed = eventElapsed(item, event);
  const types = { knock: "knock", revive: "revive", squad_elimination: "squad elimination", respawn: "respawn" };
  const sides = { squad: "this squad", opponent: "other squads", unattributed: "unattributed" };
  const actor = event.actor_name || "";
  const victim = event.victim_name || "";
  const who = actor && victim ? `${actor} → ${victim}` : actor || victim;
  const when = elapsed == null ? "time unknown" : mmss(elapsed);
  return `<span class="line">${when} · ${esc(types[event.type] || event.type || "event")} · ${esc(sides[event.side] || event.side || "unattributed")}${who ? ` · ${esc(who)}` : ""}</span>`;
}

function highlightCard(item) {
  const direction = item.direction === "down" ? "decrease" : item.direction === "up" ? "increase" : "change";
  const prov = item.provenance || {};
  const full = typeof prov.sha256 === "string" ? prov.sha256 : "";
  const windows = Number(item.windows_merged);
  const windowExtra = Number.isFinite(Number(item.window_s)) ? ` · ${Number(item.window_s)} seconds` : "";
  const episodeExtra = Number.isFinite(windows) ? ` · ${windows} overlapping window${windows === 1 ? "" : "s"}` : "";
  const events = (Array.isArray(item.events) ? item.events : []).map((event) => eventLine(item, event)).join("");
  const elapsed = item.seek && item.seek.elapsed;
  const clickable = elapsed != null && Number.isFinite(Number(elapsed));
  const open = clickable
    ? `<button type="button" class="highlight" data-elapsed="${Number(elapsed)}">`
    : `<div class="highlight">`;
  return `${open}
    <strong>${esc(item.squad_name || "Squad")} · ${direction} ${points(item.change_pp)} percentage points</strong>
    ${rangeLine("Representative window", item.start_elapsed, item.end_elapsed, item.start_t, item.end_t, windowExtra)}
    <span class="line">Before ${pct(item.before)} → after ${pct(item.after)}</span>
    ${rangeLine("Full episode", item.episode_start_elapsed, item.episode_end_elapsed, item.episode_start_t, item.episode_end_t, episodeExtra)}
    <span class="line when" title="${esc(full)}">${esc(kindName(item.prediction_kind))} · ${esc(prov.file || "model file unknown")} · sha256 ${esc(full.slice(0, 12) || "unknown")}</span>
    <span class="line">Coincident events in the representative window</span>
    <span class="line when">${esc(item.description || "")}</span>
    ${events}
  ${clickable ? "</button>" : "</div>"}`;
}

// A downward card gets a "Review decision" button beside it (not inside it).
function reviewRow(index) {
  return `<div class="review-row"><button type="button" class="action quiet" data-review="${index}">Review decision</button></div>`;
}

function renderHighlights(box, result) {
  try {
    if (!result.ok || !result.body) {
      const status = result.status ? ` (HTTP ${result.status})` : "";
      const reason = result.body && (result.body.error || (result.body.missing || []).join("; "));
      box.innerHTML = `<p class="unavailable">Could not load these highlights${status}.</p>${reason ? `<p>${esc(reason)}</p>` : ""}`;
      return;
    }
    const data = result.body;
    if (data.available !== true) {
      const reasons = (data.missing || []).map((item) => `<p>${esc(item)}</p>`).join("");
      const fallback = data.error ? `<p>${esc(data.error)}</p>` : "";
      box.innerHTML = `<p class="unavailable">Not available</p>${reasons || fallback}`;
      return;
    }
    const up = Array.isArray(data.up) ? data.up : [];
    const down = Array.isArray(data.down) ? data.down : [];
    const label = esc(data.label || "Largest changes in model estimate.");
    if (!up.length && !down.length) {
      box.innerHTML = `<p><strong>${label}</strong></p><p>No move of 5 percentage points or more for this squad. A window is kept only when every replay second in it has a prediction. The recorded result is not included.</p>`;
      return;
    }
    const section = (title, items, review) => items.length
      ? `<h3>${title}</h3>${items.map((item, i) => highlightCard(item) + (review ? reviewRow(i) : "")).join("")}`
      : "";
    box.downItems = down;
    box.innerHTML = `<p><strong>${label}</strong></p>
      <p class="when">Click a row to move the slider to the start of that representative window. The change is in the model estimate. Listed events are coincident with the representative window, not a cause, and not the rest of the episode. The recorded result is not included.</p>
      ${section("Increases", up)}
      ${section("Decreases", down, Boolean(window.DecisionReview))}`;
  } catch (error) {
    box.innerHTML = `<p class="unavailable">Could not load these highlights.</p>`;
    console.error(error);
  }
}

async function fetchHighlights(gameId, kind, team) {
  const url = `/api/matches/${gameId}/highlights?kind=${encodeURIComponent(kind)}&team=${encodeURIComponent(team)}`;
  try {
    const response = await fetch(url);
    let body = null;
    try {
      body = await response.json();
    } catch (error) {
      body = null;
    }
    return { ok: response.ok, status: response.status, body };
  } catch (error) {
    return { ok: false, status: 0, body: null };
  }
}

async function loadOneHighlight(box, kind, token, gameId, team) {
  const result = await fetchHighlights(gameId, kind, team);
  if (token !== highlightToken) return;
  renderHighlights(box, result);
}

function loadHighlights() {
  if (!summary) return;
  const token = ++highlightToken;
  const gameId = summary.game_id;
  const team = selectedTeam();
  heldoutHighlights.innerHTML = `<p class="when">Loading…</p>`;
  finalHighlights.innerHTML = `<p class="when">Loading…</p>`;
  loadOneHighlight(heldoutHighlights, "held_out", token, gameId, team);
  loadOneHighlight(finalHighlights, "final_model_fitted", token, gameId, team);
}

function seekHighlight(elapsed) {
  const numeric = Number(elapsed);
  if (!Number.isFinite(numeric)) return;
  timeInput.value = String(numeric);
  seek(Number(timeInput.value));
}

for (const box of [heldoutHighlights, finalHighlights]) {
  box.addEventListener("click", (event) => {
    const review = event.target.closest("button[data-review]");
    if (review && box.contains(review)) {
      const item = (box.downItems || [])[Number(review.dataset.review)];
      if (item && window.DecisionReview) safely(() => window.DecisionReview.open(item));
      return;
    }
    const button = event.target.closest("button.highlight");
    if (!button || !box.contains(button) || button.dataset.elapsed === "") return;
    seekHighlight(button.dataset.elapsed);
  });
}

// Map dots, probability tables, and the squad dropdown share this path.
// It does not seek, reset the map camera, or request an AI review.
function applySquadSelection() {
  loadHighlights();
  if (!frame) return;
  draw();
  renderRoster();
  renderPredictions();
  loadTactical();
  if (window.Coach) safely(() => window.Coach.update());
  if (window.DecisionReview) safely(() => window.DecisionReview.update());
}

let selectionScroll = null;

function selectSquad(teamId) {
  const id = String(teamId);
  if (![...squadSelect.options].some((option) => option.value === id)) return;
  const x = selectionScroll ? selectionScroll.x : window.scrollX;
  const y = selectionScroll ? selectionScroll.y : window.scrollY;
  selectionScroll = null;
  const changed = squadSelect.value !== id;
  squadSelect.value = id;
  if (changed) applySquadSelection();
  const restore = () => window.scrollTo(x, y);
  restore();
  requestAnimationFrame(restore);
}

window.ApexMap.onSelect = selectSquad;
window.addEventListener("map-image", () => { if (frame) draw(); });
matchSelect.addEventListener("change", () => loadMatch(matchSelect.value));
squadSelect.addEventListener("change", applySquadSelection);
for (const box of [heldoutBox, curveBox]) {
  if (!box) continue;
  box.addEventListener("pointerdown", (event) => {
    const button = event.target.closest("button.squad-name");
    if (!button || !box.contains(button)) return;
    selectionScroll = { x: window.scrollX, y: window.scrollY };
    if (event.pointerType !== "mouse") return;
    event.preventDefault();
  });
  box.addEventListener("click", (event) => {
    const button = event.target.closest("button.squad-name");
    if (!button || !box.contains(button)) return;
    selectSquad(button.dataset.teamId);
  });
}
timeInput.addEventListener("input", () => seek(Number(timeInput.value)));
// The AI review asks for the moment on screen when clicked, and seeks through
// the same path as the highlights.
function currentMoment() {
  return summary && frame ? {
    game: summary.game_id,
    t: frame.t,
    elapsed: frame.elapsed,
    start: replayStart(),
    team: selectedTeam(),
    squadName: squadSelect.selectedOptions[0] ? squadSelect.selectedOptions[0].textContent : selectedTeam(),
  } : null;
}
if (window.Coach) safely(() => window.Coach.init({ context: currentMoment, seek: seekHighlight }));
if (window.DecisionReview) safely(() => window.DecisionReview.init({ context: currentMoment, seek: seekHighlight, redraw: () => { if (frame) draw(); } }));
loadBackend();
loadMatches();
