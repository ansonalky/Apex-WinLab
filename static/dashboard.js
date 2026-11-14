const matchSelect = document.querySelector("#match");
const squadSelect = document.querySelector("#squad");
const timeInput = document.querySelector("#time");
const clock = document.querySelector("#clock");
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
async function getJSON(url) {
  const response = await fetch(url);
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
  await loadFrame(0);
}

async function loadFrame(elapsed) {
  const token = ++requestToken;
  const game = summary.game_id;
  const t = replayStart() + elapsed;
  let next;
  let ring;
  try {
    [next, ring] = await Promise.all([
      getJSON(`/api/matches/${game}/at/${t}`),
      getJSON(`/api/matches/${game}/ring?t=${t}`).catch(() => ({ state: "unavailable" })),
    ]);
    if (token === requestToken && next.t !== t) {
      ring = await getJSON(`/api/matches/${game}/ring?t=${next.t}`).catch(() => ({ state: "unavailable" }));
    }
  } catch (error) {
    if (token !== requestToken) return;
    clock.textContent = `${mmss(elapsed)} · frame unavailable`;
    console.error(error);
    return;
  }
  if (token !== requestToken) return;
  frame = next;
  currentRing = ring;
  clock.textContent = mmss(frame.elapsed);
  renderOutcome();
  draw();
  renderRoster();
  renderPredictions();
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

// The server picks the checkpoint for the current second: the most recent one
// at or before it (frame.checkpoints). The page never looks ahead or interpolates.
function predictionPanel(view, selection, label) {
  if (!view || !view.available) {
    return `<p class="unavailable">Not available</p><p>${esc((view && (view.reason || view.limitation)) || "")}</p>`;
  }
  if (!selection) {
    return `${label}<p class="unavailable">No prediction yet at this slider time.</p>`;
  }
  const point = view.points[selection.index];
  const team = selectedTeam();
  const age = selection.age_s;
  const when = `Prediction time ${mmss(point.elapsed)} (replay t ${point.t}${point.replay_time_utc ? `, ${esc(point.replay_time_utc)}` : ""}).` +
    (age > 0 ? ` Slider time is ${mmss(point.elapsed + age)}, ${age}s later` : " Slider is on this checkpoint.") +
    (age > 0 && frame && frame.finished ? "; no prediction is made after the result." : age > 0 ? `; next prediction at ${mmss(point.elapsed + 30)} if the match is still undecided.` : "");
  const asOf = `Probabilities and eliminations are as of the prediction time ${mmss(point.elapsed)}; the map and roster show the slider time.`;
  const eligibilityLine = point.eligibility === "unverified"
    ? `<p class="unavailable">Eligibility unverified at this checkpoint${(point.eligibility_reasons || []).length ? ` (${esc(point.eligibility_reasons.join(", "))})` : ""}: no squad is excluded and probabilities are unconstrained.</p>`
    : `<p class="when">Eligibility verified: squads eliminated by ${mmss(point.elapsed)} have probability 0; the rest are renormalized.</p>`;
  const baselineName = point.baseline === "uniform_lobby_fallback" || point.elimination_verified === false
    ? "lobby uniform (elimination unverified)" : "contention uniform";
  const rows = [...point.squads]
    .sort((a, b) => b.model - a.model)
    .map((squad) => `<tr class="${String(squad.teamId) === team ? "selected" : ""} ${squad.eliminated ? "out" : ""}">
        <td>${textOrUnknown(squad.team_name)}${squad.eliminated ? " · eliminated" : ""}</td>
        <td class="num">${pct(squad.model)}</td>
        <td class="num">${pct(squad.uniform_contention)}</td>
      </tr>`).join("");
  const history = view.points.slice(0, selection.index + 1).map((past) => {
    const squad = past.squads.find((item) => String(item.teamId) === team);
    return squad ? `<div>${mmss(past.elapsed)} · model ${pct(squad.model)} · baseline ${pct(squad.uniform_contention)}${squad.eliminated ? " · eliminated" : ""}</div>` : "";
  }).join("");
  return `${label}
    <p class="when">${when}</p>
    <p class="when">${asOf}</p>
    ${eligibilityLine}
    ${frame && frame.finished ? `<p class="when">The match has finished at the slider time. This prediction is from ${mmss(point.elapsed)}, before the result, and the squad counts below are as of then.</p>` : ""}
    <p class="when">${point.in_contention} squads in contention at ${mmss(point.elapsed)}. Baseline: ${baselineName}.</p>
    <table class="probs"><thead><tr><th>Squad</th><th class="num">Model (constrained)</th><th class="num">Baseline</th></tr></thead><tbody>${rows}</tbody></table>
    <div class="history"><strong>Selected squad, checkpoints up to now</strong>${history || "<div>No prediction for this squad.</div>"}</div>`;
}

function renderPredictions() {
  const checkpoints = (frame && frame.checkpoints) || {};
  let heldLabel = "";
  if (heldout && heldout.available) {
    const fit = heldout.evaluation_model || {};
    const metrics = heldout.game_metrics;
    heldLabel = `<p><span class="tag heldout">held-out</span>Test session. Model fit on ${fit.train_sessions} train + ${fit.validation_sessions} validation sessions (l2 ${fit.l2}).</p>` +
      (metrics ? `<p class="when">This game: log loss ${metrics.model.log_loss.toFixed(3)} vs contention baseline ${metrics.uniform_contention.log_loss.toFixed(3)}.</p>` : "") +
      `<p class="when">${esc(heldout.limitation)}</p>`;
  }
  heldoutBox.innerHTML = predictionPanel(heldout, checkpoints.heldout, heldLabel);
  let fitLabel = "";
  if (curve && curve.available) {
    fitLabel = `<p><span class="tag fitted">fitted on these games</span>Not held-out evidence.</p><p class="when">${esc(curve.limitation)}</p>`;
  }
  curveBox.innerHTML = predictionPanel(curve, checkpoints.final, fitLabel);
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
    <p>Report ${esc(evaluation.generated_at || "")}. Method ${esc(evaluation.prediction_type)}. Held out by session: ${evaluation.held_out ? "yes" : "no"}.</p>
    ${evaluation.warning ? `<p>${esc(evaluation.warning)}</p>` : ""}
    <p>${esc(evaluation.limitation)}</p>
    ${lines}
    <p>Calibration: ${esc((evaluation.calibration && evaluation.calibration.status) || "Not available")}</p>
  `;
}

window.addEventListener("map-image", () => { if (frame) draw(); });
matchSelect.addEventListener("change", () => loadMatch(matchSelect.value));
squadSelect.addEventListener("change", () => { if (!frame) return; draw(); renderRoster(); renderPredictions(); });
timeInput.addEventListener("input", () => loadFrame(Number(timeInput.value)));
loadBackend();
loadMatches();
