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
  const first = matches.find((match) => match.heldout) || matches[0];
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
  if (summary.winner && summary.squads.some((squad) => String(squad.teamId) === String(summary.winner.teamId))) {
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
  }
  if (pendingSeek !== null) {
    const latest = pendingSeek;
    pendingSeek = null;
    loadFrame(latest);
    if (frame) shown.textContent = `loading · showing ${mmss(frame.elapsed)}`;
  } else if (frame) {
    shown.textContent = frame.elapsed === Number(timeInput.value) ? `showing ${mmss(frame.elapsed)}` : `showing ${mmss(frame.elapsed)} (latest stored second)`;
  }
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

function draw() {
  if (!frame) return;
  window.ApexMap.draw({
    canvas,
    ctx,
    frame,
    selectedTeam: selectedTeam(),
    colorFor,
    ring: currentRing,
    mapConfig,
  });
}

function bar(value, max, className) {
  if (value === null || max === null || max <= 0) return `<div class="bar ${className}"></div>`;
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
  roster.innerHTML = players.map((player) => `
    <article class="player">
      <header><strong>${textOrUnknown(player.name)}</strong><span>${textOrUnknown(player.status)}</span></header>
      <div>Health ${textOrUnknown(player.health)} / ${textOrUnknown(player.healthMax)}</div>
      ${bar(player.health, player.healthMax, "hp")}
      <div>Shields ${textOrUnknown(player.shields)} / ${textOrUnknown(player.shieldMax)}</div>
      ${bar(player.shields, player.shieldMax, "sh")}
    </article>
  `).join("");
}

// Predictions are made at every replay second before the cut-off, at the same
// second as the frame on screen (frame.live). Nothing is interpolated.
function probabilityTable(squads, valueKey, valueLabel, baselineKey) {
  const team = selectedTeam();
  const rows = [...squads]
    .sort((a, b) => b[valueKey] - a[valueKey])
    .map((squad) => `<tr class="${String(squad.teamId) === team ? "selected" : ""} ${squad.eliminated ? "out" : ""}">
        <td>${textOrUnknown(squad.team_name)}${squad.eliminated ? " · eliminated" : ""}</td>
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

window.addEventListener("map-image", () => { if (frame) draw(); });
matchSelect.addEventListener("change", () => loadMatch(matchSelect.value));
squadSelect.addEventListener("change", () => { if (!frame) return; draw(); renderRoster(); renderPredictions(); });
timeInput.addEventListener("input", () => seek(Number(timeInput.value)));
loadBackend();
loadMatches();
