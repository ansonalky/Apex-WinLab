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
const canvas = document.querySelector("#plot");
const ctx = canvas.getContext("2d");

let summary = null;
let frame = null;
let curve = null;
let heldout = null;
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

async function loadMatch(gameId) {
  summary = await (await fetch(`/api/matches/${gameId}`)).json();
  curve = await (await fetch(`/api/matches/${gameId}/curve`)).json();
  heldout = await (await fetch(`/api/matches/${gameId}/heldout`)).json();
  const evaluation = await (await fetch("/api/evaluation")).json();
  meta.textContent = [
    summary.event,
    summary.map_name,
    `quality ${summary.quality_score}`,
    `session ${summary.session_id}`,
    summary.winner && summary.winner.teamName ? `verified winner ${summary.winner.teamName}` : "winner unknown",
  ].filter(Boolean).join(" · ");
  note.textContent = summary.coordinate_note;
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
  timeInput.max = String(Math.max(0, summary.frame_count - 1));
  timeInput.value = "0";
  frame = null;
  renderEvaluation(evaluation);
  await loadFrame(0);
}

async function loadFrame(index) {
  const token = ++requestToken;
  const next = await (await fetch(`/api/matches/${summary.game_id}/frames/${index}`)).json();
  if (token !== requestToken) return;
  frame = next;
  clock.textContent = mmss(frame.elapsed);
  draw();
  renderRoster();
  renderPredictions();
}

function selectedTeam() {
  return squadSelect.value;
}

function colorFor(teamId) {
  const squad = (summary.squads || []).find((item) => String(item.teamId) === String(teamId));
  return squad ? squad.color : "#888";
}

function draw() {
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#1b1f27";
  ctx.fillRect(0, 0, width, height);
  const points = (frame.players || []).filter((player) => player.x !== null && player.y !== null);
  ctx.fillStyle = "#9aa3b2";
  ctx.font = "14px sans-serif";
  if (!points.length) {
    ctx.fillText("No positions at this time", 24, 36);
    return;
  }
  const xs = points.map((player) => player.x);
  const ys = points.map((player) => player.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const pad = 36;
  const spanX = maxX - minX || 1;
  const spanY = maxY - minY || 1;
  const scale = Math.min((width - pad * 2) / spanX, (height - pad * 2) / spanY);
  const project = (x, y) => [
    pad + (x - minX) * scale,
    height - pad - (y - minY) * scale,
  ];
  const selected = selectedTeam();
  for (const player of points) {
    const [px, py] = project(player.x, player.y);
    const mine = String(player.teamId) === selected;
    ctx.globalAlpha = mine ? 1 : 0.35;
    ctx.fillStyle = colorFor(player.teamId);
    ctx.beginPath();
    ctx.arc(px, py, mine ? 7 : 4.5, 0, Math.PI * 2);
    ctx.fill();
    if (mine) {
      ctx.globalAlpha = 1;
      ctx.fillText(player.name || "", px + 8, py - 8);
    }
  }
  ctx.globalAlpha = 1;
  ctx.fillStyle = "#9aa3b2";
  ctx.fillText("x (unverified units)", pad, height - 12);
  ctx.save();
  ctx.translate(14, height - pad);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText("y (unverified units)", 0, 0);
  ctx.restore();
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
    (age > 0 ? ` Slider time is ${mmss(point.elapsed + age)}, ${age}s later; next prediction at ${mmss(point.elapsed + 30)} if the match is still undecided.` : " Slider is on this checkpoint.");
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
    <p class="when">${point.in_contention} squads in contention. Baseline: ${baselineName}.</p>
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

matchSelect.addEventListener("change", () => loadMatch(matchSelect.value));
squadSelect.addEventListener("change", () => { draw(); renderRoster(); renderPredictions(); });
timeInput.addEventListener("input", () => loadFrame(Number(timeInput.value)));
loadMatches();
