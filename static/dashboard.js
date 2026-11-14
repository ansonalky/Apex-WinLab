const matchSelect = document.querySelector("#match");
const squadSelect = document.querySelector("#squad");
const timeInput = document.querySelector("#time");
const clock = document.querySelector("#clock");
const meta = document.querySelector("#match-meta");
const note = document.querySelector("#coord-note");
const roster = document.querySelector("#roster");
const curveBox = document.querySelector("#curve");
const evalBox = document.querySelector("#evaluation");
const canvas = document.querySelector("#plot");
const ctx = canvas.getContext("2d");

let summary = null;
let frame = null;
let curve = null;
let requestToken = 0;

function mmss(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function textOrUnknown(value) {
  return value === null || value === undefined || value === "" ? "unknown" : value;
}

async function loadMatches() {
  const matches = await (await fetch("/api/matches")).json();
  matchSelect.innerHTML = "";
  for (const match of matches) {
    const option = document.createElement("option");
    option.value = match.game_id;
    const quality = match.quality_score === null ? "unknown" : Number(match.quality_score).toFixed(2);
    option.textContent = `${match.event || "Untitled"} · ${match.map_name || "map unknown"} · quality ${quality}`;
    matchSelect.append(option);
  }
  if (matches.length) await loadMatch(matches[0].game_id);
}

async function loadMatch(gameId) {
  summary = await (await fetch(`/api/matches/${gameId}`)).json();
  curve = await (await fetch(`/api/matches/${gameId}/curve`)).json();
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
  renderCurve();
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

function renderCurve() {
  if (!curve || !curve.available) {
    curveBox.innerHTML = `<p class="unavailable">Not available</p><p>${(curve && curve.limitation) || "Predictions are not available for this match."}</p>`;
    return;
  }
  const team = selectedTeam();
  const rows = (curve.points || []).map((point) => {
    const squad = point.squads.find((item) => String(item.teamId) === team);
    if (!squad) return "";
    return `<div>${mmss(point.elapsed)} model ${(squad.model * 100).toFixed(1)}% · uniform ${(squad.uniform * 100).toFixed(1)}% · most standing ${(squad.most_players * 100).toFixed(1)}%</div>`;
  }).join("");
  curveBox.innerHTML = `
    <p>Type: ${curve.prediction_type}. Held out: ${curve.held_out ? "yes" : "no"}. In the final fit: ${curve.in_sample_game ? "yes" : "no"}.</p>
    <p>Model: l2 ${curve.model_version && curve.model_version.l2}, step ${curve.model_version && curve.model_version.checkpoint_step_s}s, converged ${curve.model_version && curve.model_version.converged}.</p>
    <p>${curve.limitation}</p>
    ${rows || "<p class=\"unavailable\">Not available</p>"}
  `;
}

function renderEvaluation(evaluation) {
  if (!evaluation.available) {
    evalBox.innerHTML = `<p class="unavailable">Not available</p><p>${evaluation.reason || ""}</p>`;
    return;
  }
  const metrics = evaluation.metrics || {};
  const lines = Object.entries(metrics).map(([method, values]) => {
    return `<div>${method}: log loss ${values.log_loss.toFixed(3)}, Brier ${values.brier.toFixed(3)}, top-1 ${values.top1.toFixed(3)}</div>`;
  }).join("");
  evalBox.innerHTML = `
    <p>Report ${evaluation.generated_at || ""}. Method ${evaluation.prediction_type}. Held out by session: ${evaluation.held_out ? "yes" : "no"}.</p>
    ${evaluation.warning ? `<p>${evaluation.warning}</p>` : ""}
    <p>${evaluation.limitation}</p>
    ${lines}
    <p>Calibration: ${(evaluation.calibration && evaluation.calibration.status) || "Not available"}</p>
  `;
}

matchSelect.addEventListener("change", () => loadMatch(matchSelect.value));
squadSelect.addEventListener("change", () => { draw(); renderRoster(); renderCurve(); });
timeInput.addEventListener("input", () => loadFrame(Number(timeInput.value)));
loadMatches();
