const matchSelect = document.querySelector("#match");
const squadSelect = document.querySelector("#squad");
const timeInput = document.querySelector("#time");
const clock = document.querySelector("#clock");
const shown = document.querySelector("#shown");
const meta = document.querySelector("#match-meta");
const note = document.querySelector("#coord-note");
const roster = document.querySelector("#roster");
const estimateBox = document.querySelector("#estimate");
const momentBoxes = { up: document.querySelector("#moment-up"), down: document.querySelector("#moment-down") };
const detailsDialog = document.querySelector("#model-details");
const altBox = document.querySelector("#alt-estimate");
const provenanceBox = document.querySelector("#provenance");
const evalBox = document.querySelector("#evaluation");
const altHighlights = document.querySelector("#alt-highlights");
const outcome = document.querySelector("#outcome");
const canvas = document.querySelector("#plot");
const ctx = canvas.getContext("2d");

const { esc, mmss, pct, pp, num } = window.UI;
const textOrUnknown = window.UI.text;

let summary = null;
let frame = null;
let curve = null;
let heldout = null;
let evaluation = null;
let backendInfo = null;
let mapConfig = null;
let currentRing = null;
let requestToken = 0;
// At most one frame request runs at a time. Slider input that arrives while
// it runs only replaces pendingSeek, so a fast scrub asks for its newest value
// next and skips the ones in between.
let inFlight = null;
let pendingSeek = null;
let highlightToken = 0;
// Highlights per prediction kind, and the carousel position per direction.
let highlightResults = {};
const carousel = { up: 0, down: 0 };

const HELD_OUT = "held_out";
const FINAL = "final_model_fitted";

// Throws on an HTTP error, so an error page is never parsed as data.
async function getJSON(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

// A small badge on the page; the connection detail is in Model details.
async function loadBackend() {
  const box = document.querySelector("#backend");
  try {
    backendInfo = await (await fetch("/api/backend")).json();
    box.className = `badge ${backendInfo.backend === "tiger" ? "tiger" : "files"}`;
    box.textContent = backendInfo.backend === "tiger" ? "Tiger Data" : "Local files";
    box.title = `${backendInfo.label || backendInfo.backend}: ${backendInfo.detail || ""}`;
  } catch (error) {
    box.className = "badge";
    box.textContent = "Data source unknown";
  }
}

async function loadMatches() {
  const matches = await (await fetch("/api/matches")).json();
  matchSelect.innerHTML = "";
  for (const match of matches) {
    const option = document.createElement("option");
    option.value = match.game_id;
    const tag = match.heldout ? "[held-out test] " : "";
    option.textContent = `${tag}${match.event || "Untitled"} · ${match.map_name || "map unknown"} · quality ${num(match.quality_score, 2)}`;
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

// One primary view: held-out when this match is a held-out test game,
// otherwise the fitted final model, labeled as in-sample.
function primaryKind() {
  return heldout && heldout.available ? HELD_OUT : FINAL;
}

function kindLabel(kind) {
  return kind === HELD_OUT ? "Held-out model" : "Fitted model (in-sample: trained on this match)";
}

async function loadMatch(gameId) {
  const token = ++requestToken;
  if (inFlight) inFlight.controller.abort();
  inFlight = null;
  pendingSeek = null;
  highlightToken += 1;
  if (window.Coach) safely(() => window.Coach.reset());
  if (window.DecisionReview) safely(() => window.DecisionReview.reset());
  for (const box of Object.values(momentBoxes)) box.innerHTML = `<p class="when">Loading…</p>`;
  timeInput.disabled = true;
  let nextSummary, nextCurve, nextHeldout, nextEvaluation;
  try {
    [nextSummary, nextCurve, nextHeldout, nextEvaluation] = await Promise.all([
      getJSON(`/api/matches/${gameId}`),
      getJSON(`/api/matches/${gameId}/curve`),
      getJSON(`/api/matches/${gameId}/heldout`),
      getJSON("/api/evaluation"),
    ]);
  } catch (error) {
    if (token !== requestToken) return;
    meta.textContent = `Match ${gameId} could not be loaded.`;
    for (const box of Object.values(momentBoxes)) box.innerHTML = `<p class="unavailable">Could not load key moments.</p>`;
    console.error(error);
    return;
  }
  const map = await getJSON(`/api/map-config?map=${encodeURIComponent(nextSummary.map_name || "")}`)
    .catch(() => ({ supported: false, message: "Map background unavailable: map configuration could not be loaded." }));
  if (token !== requestToken) return;
  summary = nextSummary;
  curve = nextCurve;
  heldout = nextHeldout;
  evaluation = nextEvaluation;
  mapConfig = map;
  meta.textContent = [summary.event, summary.map_name, `quality ${num(summary.quality_score, 2)}`].filter(Boolean).join(" · ");
  // The transform note is in Model details; only a missing map is shown here.
  note.hidden = Boolean(mapConfig && mapConfig.supported);
  note.textContent = note.hidden ? "" : (mapConfig && mapConfig.message) || "Map background unavailable.";
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
  clock.textContent = mmss(0);
  renderDetails();
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
    shown.textContent = frame.elapsed === Number(timeInput.value) ? "" : `showing ${mmss(frame.elapsed)} (latest stored second)`;
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
    outcome.innerHTML = `<strong>Match finished</strong> at ${mmss(summary.conclusion_elapsed)}. ` +
      `Verified winner: <strong>${winner ? esc(winner) : "Unknown"}</strong>. This is the recorded result, not a model prediction.`;
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

function meter(label, value, max, className) {
  return `<div class="meter" title="${label}"><span class="meter-label">${label}</span>${bar(value, max, className)}<span class="meter-value">${textOrUnknown(value)}/${textOrUnknown(max)}</span></div>`;
}

// One compact row per player.
function renderRoster() {
  const team = selectedTeam();
  const players = (frame.players || []).filter((player) => String(player.teamId) === team);
  if (!players.length) {
    roster.innerHTML = `<p class="unavailable">No players for this squad at this time.</p>`;
    return;
  }
  const squadOut = (frame.eliminated_squads || {})[team];
  roster.innerHTML = `<div class="roster">${players.map((player) => {
    // Display only: a confirmed-eliminated player shows 0 health and shields
    // with their maximums kept. Telemetry and model inputs are unchanged.
    // Downed players and "dead" (respawnable) players show their telemetry.
    const playerOut = player.status === "eliminated";
    const zeroed = (playerOut || squadOut != null) && player.status !== "downed";
    const health = zeroed ? 0 : player.health;
    const shields = zeroed ? 0 : player.shields;
    const status = !playerOut && zeroed
      ? `${textOrUnknown(player.status)} · squad out ${mmss(squadOut - replayStart())}`
      : textOrUnknown(player.status);
    return `<div class="player-row${zeroed ? " out" : ""}">
      <span class="player-name" title="${esc(player.name || "")}">${textOrUnknown(player.name)}</span>
      <span class="player-status status-${esc(player.status || "unknown")}">${status}</span>
      ${meter("HP", health, player.healthMax, "hp")}
      ${meter("SH", shields, player.shieldMax, "sh")}
    </div>`;
  }).join("")}</div>`;
}

// Clickable squad comparison. Squads in contention are listed; eliminated
// squads are folded into one expandable row.
function comparisonTable(squads, valueKey) {
  const team = selectedTeam();
  const row = (squad) => `<tr class="${String(squad.teamId) === team ? "selected" : ""} ${squad.eliminated ? "out" : ""}">
      <td><button type="button" class="squad-name" data-team-id="${esc(squad.teamId)}">${textOrUnknown(squad.team_name)}</button></td>
      <td class="num">${pct(squad[valueKey])}</td>
    </tr>`;
  const sorted = [...squads].sort((a, b) => b[valueKey] - a[valueKey]);
  const live = sorted.filter((squad) => !squad.eliminated);
  const out = sorted.filter((squad) => squad.eliminated);
  return `<table class="probs compact"><tbody>${live.map(row).join("")}</tbody></table>
    ${out.length ? `<details class="sub" ${window.UI.keep("compare-out")}><summary>${out.length} eliminated squad${out.length === 1 ? "" : "s"} (0.0%)</summary><table class="probs compact"><tbody>${out.map(row).join("")}</tbody></table></details>` : ""}`;
}

// The full per-squad table used in Model details, with the baseline column.
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
    .map(([name, value]) => `<tr><td>${esc(name)}</td><td class="num">${value === null ? "Unknown" : Number(value).toFixed(3)}</td></tr>`).join("");
  return `<details ${window.UI.keep("inputs")}><summary>Model inputs for the selected squad at ${mmss(point.elapsed)}</summary><table class="probs">${cells}</table>
    <p class="when">hp_frac sums health and shields over alive and downed players; a downed player's health is the bleed-out pool, which the replay reports as full.</p></details>`;
}

function eligibilityText(point) {
  return point.eligibility === "unverified"
    ? `Eligibility unverified at this second${(point.eligibility_reasons || []).length ? ` (${esc(point.eligibility_reasons.join(", "))})` : ""}: no squad is excluded.`
    : `Eligibility verified: squads eliminated by ${mmss(point.elapsed)} have probability 0; the rest are renormalized.`;
}

// Full per-second panel (Model details): table with baseline, provenance, inputs.
function livePanel(live, label) {
  if (!live || !live.available) return "";
  if (!live.point) {
    return `<p class="unavailable">No model prediction at ${mmss(frame.elapsed)}: ${esc(live.reason || "")}.</p>`;
  }
  const point = live.point;
  const provenance = live.provenance || {};
  return `<p class="when"><strong>${label} at ${mmss(point.elapsed)}</strong> (replay t ${point.t}), the same second as the map and roster. Sampled every second; ${esc(provenance.file || "")}, sha256 ${esc(provenance.sha256 || "")}.</p>
    <p class="${point.eligibility === "unverified" ? "unavailable" : "when"}">${eligibilityText(point)}</p>
    <p class="when">${point.in_contention} squads in contention.</p>
    ${probabilityTable(point.squads, "model", "Model (constrained)", "uniform_contention")}
    ${inputsFor(point)}`;
}

// The frozen evaluation's own 30-second checkpoints, up to the slider time.
function checkpointHistory(view, selection, key) {
  if (!view || !view.available || !selection) return "";
  const team = selectedTeam();
  const history = view.points.slice(0, selection.index + 1).map((past) => {
    const squad = past.squads.find((item) => String(item.teamId) === team);
    return squad ? `<div>${mmss(past.elapsed)} · model ${pct(squad.model)} · baseline ${pct(squad.uniform_contention)}${squad.eliminated ? " · eliminated" : ""}</div>` : "";
  }).join("");
  return `<details class="history" ${window.UI.keep(key)}><summary>Selected squad at the 30-second evaluation checkpoints (historical)</summary>${history || "<div>No checkpoint for this squad.</div>"}</details>`;
}

// Only the bundle fields this backend supplies.
function bundleText() {
  const parts = [
    heldout.bundle ? `bundle ${esc(heldout.bundle)}` : "",
    heldout.bundle_generated_at ? `generated ${esc(heldout.bundle_generated_at)}` : "",
    heldout.code_commit ? `code ${esc(String(heldout.code_commit).slice(0, 12))}` : "",
  ].filter(Boolean);
  return parts.length ? ` ${parts.join(", ")}.` : "";
}

function viewHeader(kind) {
  if (kind === HELD_OUT && heldout && heldout.available) {
    const fit = heldout.evaluation_model || {};
    const metrics = heldout.game_metrics;
    return `<p><span class="tag heldout">held-out</span>Test session. Frozen evaluation model fit on ${textOrUnknown(fit.train_sessions)} train + ${textOrUnknown(fit.validation_sessions)} validation sessions (l2 ${textOrUnknown(fit.l2)}); not refit.${bundleText()}</p>` +
      (metrics ? `<p class="when">This game, at the 30-second checkpoints: log loss ${num(metrics.model.log_loss, 3)} vs contention baseline ${num(metrics.uniform_contention.log_loss, 3)}.</p>` : "") +
      `<p class="when">${esc(heldout.limitation || "")}</p>`;
  }
  if (kind === FINAL && curve && curve.available) {
    const version = curve.model_version || {};
    return `<p><span class="tag fitted">fitted on these games</span>Not held-out evidence. Trained on ${textOrUnknown(version.trained_on_game_count)} games (l2 ${textOrUnknown(version.l2)}, checkpoints every ${textOrUnknown(version.checkpoint_step_s)} s).</p><p class="when">${esc(curve.limitation || "")}</p>`;
  }
  return "";
}

function predictionPanel(kind) {
  const view = kind === HELD_OUT ? heldout : curve;
  const live = ((frame && frame.live) || {})[kind === HELD_OUT ? "heldout" : "final"];
  const selection = ((frame && frame.checkpoints) || {})[kind === HELD_OUT ? "heldout" : "final"];
  if (!view || !view.available) {
    return `<p class="unavailable">Not available</p><p>${esc((view && (view.reason || view.limitation || view.message)) || "")}</p>`;
  }
  const outcomePart = frame && frame.finished ? recordedOutcome() : "";
  const livePart = frame && frame.finished
    ? (live && live.available && live.cutoff_t != null
      ? `<p class="when">No model prediction is made after the result. The last per-second prediction was at ${mmss(live.cutoff_t - 1 - replayStart())} (replay t ${live.cutoff_t - 1}).</p>`
      : "")
    : livePanel(live, kind === HELD_OUT ? "Held-out prediction" : "Final-model prediction");
  return `${viewHeader(kind)}${outcomePart}${livePart}${checkpointHistory(view, selection, `history-${kind}`)}`;
}

// The one primary estimate on the page.
function renderEstimate() {
  const kind = primaryKind();
  const view = kind === HELD_OUT ? heldout : curve;
  const live = ((frame && frame.live) || {})[kind === HELD_OUT ? "heldout" : "final"];
  const tag = kind === HELD_OUT
    ? `<span class="tag heldout">held-out</span>`
    : `<span class="tag fitted">fitted · in-sample</span>`;
  if (!view || !view.available) {
    estimateBox.innerHTML = `<p class="unavailable">No estimate is available for this match.</p><p class="when">${esc((view && (view.reason || view.message)) || "")}</p>`;
    return;
  }
  const info = `<details class="info" ${window.UI.keep("estimate-info")}><summary title="About this estimate" aria-label="About this estimate">ⓘ</summary>
      <div class="info-body">
        <p><strong>${kindLabel(kind)}.</strong> One estimate per replay second from the frozen model, at the same second as the map and roster; nothing is interpolated.</p>
        ${live && live.provenance ? `<p class="when">Model file ${esc(live.provenance.file || "Unknown")}, sha256 ${esc((live.provenance.sha256 || "").slice(0, 12) || "Unknown")}. Fit on ${esc(live.provenance.fit_on || "Unknown")}.</p>` : ""}
        <p class="when">${esc(view.limitation || "")}</p>
        <p class="when">Baseline: 1 ÷ squads still in contention. Full tables, inputs and evaluation are in Model details.</p>
      </div></details>`;
  if (frame && frame.finished) {
    estimateBox.innerHTML = `<div class="estimate-head"><div>${tag}<span class="when">Match finished; no estimate after the result.</span></div>${info}</div>${recordedOutcome()}`;
    return;
  }
  if (!live || !live.available || !live.point) {
    const reason = live && live.reason ? `: ${esc(live.reason)}` : "";
    estimateBox.innerHTML = `<div class="estimate-head"><div>${tag}<span class="unavailable">No estimate at ${frame ? mmss(frame.elapsed) : "this time"}${reason}.</span></div>${info}</div>`;
    return;
  }
  const point = live.point;
  const team = selectedTeam();
  const row = point.squads.find((squad) => String(squad.teamId) === team);
  // Rank among squads still in contention (eliminated squads are 0%).
  const ranked = point.squads.filter((squad) => !squad.eliminated).sort((a, b) => b.model - a.model);
  const rank = row && !row.eliminated ? ranked.findIndex((squad) => String(squad.teamId) === team) + 1 : null;
  estimateBox.innerHTML = `<div class="estimate-head">
      <div class="estimate-value">${row ? pct(row.model) : "Unknown"}</div>
      <div class="estimate-text">
        <strong>${esc(squadName(team))}</strong>${row && row.eliminated ? " · eliminated" : rank ? ` · rank ${rank} of ${ranked.length} in contention` : ""}<br>
        <span class="when">${tag}at ${mmss(point.elapsed)} · baseline ${row ? pct(row.uniform_contention) : "Unknown"}</span>
      </div>
      ${info}
    </div>
    ${point.eligibility === "unverified" ? `<p class="unavailable small">${eligibilityText(point)}</p>` : ""}
    ${comparisonTable(point.squads, "model")}`;
}

function renderPredictions() {
  renderEstimate();
  if (detailsDialog.open) renderDetails();
}

// ---------------------------------------------------------------- Model details

function renderDetails() {
  if (!summary) return;
  const kind = primaryKind();
  const alt = kind === HELD_OUT ? FINAL : HELD_OUT;
  document.querySelector("#backend-details").innerHTML = backendInfo
    ? `<p>Data backend: <strong>${esc(backendInfo.label || backendInfo.backend)}</strong> <span class="when">${esc(backendInfo.detail || "")}</span></p>`
    : `<p class="when">Data backend: Unknown</p>`;
  document.querySelector("#match-details").innerHTML = `<p class="when">Game ${esc(summary.game_id)} · session ${textOrUnknown(summary.session_id)} · ${textOrUnknown(summary.event)} · ${textOrUnknown(summary.map_name)} · quality ${num(summary.quality_score, 2)}${summary.winner && summary.winner.teamName ? ` · verified winner ${esc(summary.winner.teamName)}` : " · winner unknown"}.</p>
    <p class="when">${frame ? `Showing ${mmss(frame.elapsed)} (replay t ${frame.t}); Playing started at replay t ${replayStart()}.` : ""}</p>
    <p class="when">${mapConfig && mapConfig.supported ? "Positions use the Overstat mapCord transform on the matching 2048 map. z is omitted. Only the current ring is shown." : esc((mapConfig && mapConfig.message) || "Map background unavailable.")}</p>
    <p class="when">${mapConfig && mapConfig.supported ? "" : `${esc(summary.coordinate_note || "")} `}Distances and heights elsewhere on this page are in unverified game units.</p>`;
  provenanceBox.innerHTML = frame ? predictionPanel(kind) : `<p class="when">Loading…</p>`;
  document.querySelector("#alt-title").textContent = alt === HELD_OUT ? "Alternate estimate: held-out model" : "Alternate estimate: fitted model (in-sample)";
  altBox.innerHTML = frame ? predictionPanel(alt) : `<p class="when">Loading…</p>`;
  document.querySelector("#alt-highlights-title").textContent = `Key moments from the ${alt === HELD_OUT ? "held-out" : "fitted"} model`;
  renderHighlightList(altHighlights, highlightResults[alt]);
  renderEvaluation(evaluation);
}

function renderEvaluation(report) {
  if (!report || !report.available) {
    evalBox.innerHTML = `<p class="unavailable">Not available</p><p>${esc((report && report.reason) || "")}</p>`;
    return;
  }
  const metrics = report.metrics || {};
  const order = ["model_constrained", "model", "uniform_contention", "most_players", "uniform"];
  const names = {
    model: "Model, raw (before eligibility rule)",
    model_constrained: "Model, eligibility-constrained",
    uniform_contention: "Contention uniform (primary baseline)",
    most_players: "Most standing players",
    uniform: "Lobby uniform",
  };
  const methods = [...order.filter((m) => m in metrics), ...Object.keys(metrics).filter((m) => !order.includes(m))];
  const rows = methods.map((method) => {
    const values = metrics[method];
    return `<tr><td>${esc(names[method] || method)}</td><td class="num">${num(values.log_loss, 3)}</td><td class="num">${num(values.brier, 3)}</td><td class="num">${pct(values.top1)}</td></tr>`;
  }).join("");
  evalBox.innerHTML = `
    <p class="when">Report ${esc(report.generated_at || "")}. Method ${esc(report.prediction_type)}. Held out by session: ${report.held_out ? "yes" : "no"}. Scored at 30-second checkpoints; the per-second estimates are not part of this report.</p>
    ${report.warning ? `<p>${esc(report.warning)}</p>` : ""}
    <p class="when">${esc(report.limitation)}</p>
    <table class="probs"><thead><tr><th>Method</th><th class="num">Log loss</th><th class="num">Brier</th><th class="num">Top-1</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="when">Calibration: ${esc((report.calibration && report.calibration.status) || "Not available")}</p>`;
}

// ---------------------------------------------------------------- key moments

const EVENT_TYPES = new Set(["knock", "revive", "squad_elimination", "respawn"]);

function eventElapsed(item, event) {
  if (event.elapsed != null && Number.isFinite(Number(event.elapsed))) return Number(event.elapsed);
  if (item.start_elapsed == null || item.start_t == null || event.t == null) return null;
  return Number(item.start_elapsed) + (Number(event.t) - Number(item.start_t));
}

function who(name, teamId) {
  const team = teamId == null ? "" : ` (${esc(squadName(teamId))})`;
  return `${name ? esc(name) : "a player"}${team}`;
}

// One recorded event as a short clause. Recorded facts only; no cause is implied.
function eventClause(event) {
  if (event.type === "knock") return `${who(event.actor_name, event.actor_team_id)} knocked ${who(event.victim_name, event.victim_team_id)}`;
  if (event.type === "revive") return `${who(event.actor_name, event.actor_team_id)} revived ${event.victim_name ? who(event.victim_name, event.victim_team_id) : "a teammate"}`;
  if (event.type === "respawn") return `${who(event.victim_name || event.actor_name, event.victim_team_id ?? event.actor_team_id)} respawned`;
  if (event.type === "squad_elimination") {
    return event.ambiguous || event.victim_team_id == null ? "a squad was eliminated (team unclear)" : `${esc(squadName(event.victim_team_id))} was eliminated`;
  }
  return "";
}

// One short sentence from the recorded events in the representative window.
// Events involving the highlighted squad come first, then the rest, each in time order.
function eventSentence(item) {
  const all = (Array.isArray(item.events) ? item.events : []).filter((event) => EVENT_TYPES.has(event.type));
  const events = [...all.filter((event) => event.side === "squad"), ...all.filter((event) => event.side !== "squad")];
  if (!events.length) return "Win estimate changed; no matching event was identified.";
  const clauses = events.slice(0, 2).map(eventClause).filter(Boolean);
  const more = events.length - clauses.length;
  const text = `${clauses.join("; ")}${more > 0 ? `; and ${more} more recorded event${more === 1 ? "" : "s"}` : ""}.`;
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function eventLine(item, event) {
  const elapsed = eventElapsed(item, event);
  const sides = { squad: "this squad", opponent: "other squads", unattributed: "unattributed" };
  const label = eventClause(event) || esc(event.type || "event");
  const time = elapsed == null ? "Unknown" : `<button type="button" class="link" data-elapsed="${elapsed}">${mmss(elapsed)}</button>`;
  return `<li>${time} · ${label} <span class="when">(${esc(sides[event.side] || event.side || "unattributed")})</span></li>`;
}

function momentDetails(item, key) {
  const prov = item.provenance || {};
  const windows = Number(item.windows_merged);
  const events = (Array.isArray(item.events) ? item.events : []).map((event) => eventLine(item, event)).join("");
  return `<details class="sub" ${window.UI.keep(key)}><summary>Details</summary>
    <dl class="facts">
      <dt>Representative window</dt><dd>${UI.span(item.start_elapsed, item.end_elapsed)}${Number.isFinite(Number(item.window_s)) ? ` (${Number(item.window_s)} s)` : ""}: estimate ${pct(item.before)} → ${pct(item.after)}, ${pp(item.change_pp)}</dd>
      <dt>Full episode</dt><dd>${UI.span(item.episode_start_elapsed, item.episode_end_elapsed)}${Number.isFinite(windows) ? ` · ${windows} overlapping window${windows === 1 ? "" : "s"}` : ""}. The change above is for the representative window only, not the whole episode.</dd>
      <dt>Model</dt><dd>${esc(kindLabel(item.prediction_kind))} · ${esc(prov.file || "Unknown")} · sha256 ${esc((prov.sha256 || "").slice(0, 12) || "Unknown")}</dd>
    </dl>
    <p class="when">${esc(item.description || "")}</p>
    ${events ? `<ul class="events">${events}</ul>` : `<p class="when">No knock, revive, respawn or squad elimination was recorded in the representative window.</p>`}
  </details>`;
}

function renderCarousel(direction) {
  const box = momentBoxes[direction];
  const title = direction === "up" ? "Win estimate rose" : "Win estimate fell";
  const result = highlightResults[primaryKind()];
  const head = (index, count) => `<div class="carousel-head"><strong>${title}</strong>
      <span class="carousel-nav">
        <button type="button" class="arrow" data-step="-1" aria-label="Previous ${title.toLowerCase()} moment"${index <= 0 ? " disabled" : ""}>‹</button>
        <span class="count">${count ? `${index + 1} / ${count}` : "0 / 0"}</span>
        <button type="button" class="arrow" data-step="1" aria-label="Next ${title.toLowerCase()} moment"${index >= count - 1 ? " disabled" : ""}>›</button>
      </span></div>`;
  if (!result) {
    box.innerHTML = `${head(0, 0)}<p class="when">Loading…</p>`;
    return;
  }
  if (!result.ok || !result.body || result.body.available !== true) {
    const body = result.body || {};
    const reason = body.error || (body.missing || []).join("; ") || (result.status ? `HTTP ${result.status}` : "");
    box.innerHTML = `${head(0, 0)}<p class="unavailable">Not available${reason ? `: ${esc(reason)}` : ""}.</p>`;
    return;
  }
  const items = (Array.isArray(result.body[direction]) ? result.body[direction] : []).slice(0, 3);
  if (!items.length) {
    box.innerHTML = `${head(0, 0)}<p class="when">No ${direction === "up" ? "rise" : "fall"} of 5 percentage points or more was detected for this squad.</p>`;
    return;
  }
  const index = Math.min(carousel[direction], items.length - 1);
  carousel[direction] = index;
  const item = items[index];
  const elapsed = item.seek && item.seek.elapsed;
  const canSeek = elapsed != null && Number.isFinite(Number(elapsed));
  const review = direction === "down" && window.DecisionReview
    ? `<button type="button" class="action quiet small" data-review="${index}">Review decision</button>` : "";
  box.innerHTML = `${head(index, items.length)}
    ${canSeek ? `<button type="button" class="moment" data-elapsed="${Number(elapsed)}">` : `<div class="moment">`}
      <span class="moment-time">${UI.span(item.start_elapsed, item.end_elapsed)}</span>
      <span class="moment-text">${eventSentence(item)}</span>
    ${canSeek ? "</button>" : "</div>"}
    <div class="moment-actions">${review}</div>
    ${momentDetails(item, `moment-${direction}-${item.start_t}`)}`;
}

// The full list (Model details), for the alternate prediction kind.
function highlightCard(item) {
  const direction = item.direction === "down" ? "Fell" : item.direction === "up" ? "Rose" : "Changed";
  const elapsed = item.seek && item.seek.elapsed;
  const clickable = elapsed != null && Number.isFinite(Number(elapsed));
  return `<div class="list-moment">
    ${clickable ? `<button type="button" class="moment" data-elapsed="${Number(elapsed)}">` : `<div class="moment">`}
      <span class="moment-time">${direction} · ${UI.span(item.start_elapsed, item.end_elapsed)}</span>
      <span class="moment-text">${eventSentence(item)}</span>
    ${clickable ? "</button>" : "</div>"}
    ${momentDetails(item, `alt-${item.direction}-${item.start_t}`)}
  </div>`;
}

function renderHighlightList(box, result) {
  try {
    if (!result) {
      box.innerHTML = `<p class="when">Loading…</p>`;
      return;
    }
    if (!result.ok || !result.body || result.body.available !== true) {
      const body = result.body || {};
      const reason = body.error || (body.missing || []).join("; ") || (result.status ? `HTTP ${result.status}` : "");
      box.innerHTML = `<p class="unavailable">Not available${reason ? `: ${esc(reason)}` : ""}.</p>`;
      return;
    }
    const up = Array.isArray(result.body.up) ? result.body.up : [];
    const down = Array.isArray(result.body.down) ? result.body.down : [];
    box.downItems = down;
    if (!up.length && !down.length) {
      box.innerHTML = `<p class="when">No move of 5 percentage points or more for this squad. A window is kept only when every replay second in it has a prediction. The recorded result is not included.</p>`;
      return;
    }
    box.innerHTML = `<p class="when">The change is in the model estimate. Listed events coincide with the representative window; they are not a cause, and not the rest of the episode.</p>
      ${up.map(highlightCard).join("")}
      ${down.map((item, i) => highlightCard(item) + (window.DecisionReview ? `<div class="moment-actions"><button type="button" class="action quiet small" data-review="${i}">Review decision</button></div>` : "")).join("")}`;
  } catch (error) {
    box.innerHTML = `<p class="unavailable">Could not load these key moments.</p>`;
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

function loadHighlights() {
  if (!summary) return;
  const token = ++highlightToken;
  const gameId = summary.game_id;
  const team = selectedTeam();
  highlightResults = {};
  carousel.up = 0;
  carousel.down = 0;
  renderCarousel("up");
  renderCarousel("down");
  for (const kind of [HELD_OUT, FINAL]) {
    fetchHighlights(gameId, kind, team).then((result) => {
      if (token !== highlightToken) return;
      highlightResults[kind] = result;
      if (kind === primaryKind()) {
        renderCarousel("up");
        renderCarousel("down");
      } else if (detailsDialog.open) {
        renderHighlightList(altHighlights, result);
      }
    });
  }
}

function seekHighlight(elapsed) {
  const numeric = Number(elapsed);
  if (!Number.isFinite(numeric)) return;
  timeInput.value = String(numeric);
  seek(Number(timeInput.value));
}

function openDecision(items, index) {
  const item = (items || [])[Number(index)];
  if (item && window.DecisionReview) safely(() => window.DecisionReview.open(item));
}

// Arrows change the displayed card only: no seek, no request.
for (const [direction, box] of Object.entries(momentBoxes)) {
  box.addEventListener("click", (event) => {
    const arrow = event.target.closest("button.arrow");
    if (arrow && !arrow.disabled) {
      const step = arrow.dataset.step;
      carousel[direction] += Number(step);
      renderCarousel(direction);
      // Keep keyboard focus on an arrow after the card is redrawn.
      const same = box.querySelector(`button.arrow[data-step="${step}"]`);
      const other = box.querySelector(`button.arrow[data-step="${-Number(step)}"]`);
      (same && !same.disabled ? same : other)?.focus({ preventScroll: true });
      return;
    }
    const review = event.target.closest("button[data-review]");
    if (review) {
      const result = highlightResults[primaryKind()];
      openDecision(result && result.body && result.body[direction], review.dataset.review);
      return;
    }
    const target = event.target.closest("[data-elapsed]");
    if (target && box.contains(target) && target.dataset.elapsed !== "") seekHighlight(target.dataset.elapsed);
  });
}

altHighlights.addEventListener("click", (event) => {
  const review = event.target.closest("button[data-review]");
  if (review) {
    detailsDialog.close();
    openDecision(altHighlights.downItems, review.dataset.review);
    return;
  }
  const target = event.target.closest("[data-elapsed]");
  if (target && target.dataset.elapsed !== "") {
    detailsDialog.close();
    seekHighlight(target.dataset.elapsed);
  }
});

// ---------------------------------------------------------------- selection

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
for (const box of [estimateBox, provenanceBox, altBox]) {
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

// Model details opens over the page; the page, slider and camera stay as they are.
document.querySelector("#details-open").addEventListener("click", () => {
  renderDetails();
  detailsDialog.showModal();
});
detailsDialog.addEventListener("click", (event) => {
  if (event.target.closest("[data-close]") || event.target === detailsDialog) detailsDialog.close();
});

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
