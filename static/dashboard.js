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
let activeLoad = null;
// Confirmed in the local replay: team 20 is EQT, both alive at 18:47, NAGGI downed at 18:48.
const START_GAME = "123633";
const START_TEAM = "20";
const START_ELAPSED = 18 * 60 + 47;
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
const NEW_MATCH = "new_match";

// Throws on an HTTP error, so an error page is never parsed as data.
async function getJSON(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

// Connection detail is shown in Model details, not in the header.
async function loadBackend() {
  try {
    backendInfo = await (await fetch("/api/backend")).json();
  } catch (error) {
    backendInfo = null;
  }
}

function qualityValue(match) {
  const score = match.quality_score;
  return typeof score === "number" && Number.isFinite(score) ? score : null;
}

// Highest numeric quality first. Missing scores last. Ties break by game id descending.
function compareMatches(a, b) {
  const aq = qualityValue(a);
  const bq = qualityValue(b);
  if (aq === null || bq === null) {
    if (aq === null && bq === null) return Number(b.game_id) - Number(a.game_id);
    return aq === null ? 1 : -1;
  }
  if (aq !== bq) return bq - aq;
  return Number(b.game_id) - Number(a.game_id);
}

async function loadMatches(preferId) {
  const previous = preferId != null ? String(preferId) : matchSelect.value;
  const matches = (await (await fetch("/api/matches")).json()).sort(compareMatches);
  matchSelect.innerHTML = "";
  for (const match of matches) {
    const option = document.createElement("option");
    option.value = match.game_id;
    // Expansion games are never held-out and never in a frozen split.
    option.dataset.listed = "1";
    option.textContent = matchOptionLabel(match);
    matchSelect.append(option);
  }
  // Sorting does not change the game already on screen. A link from Performance
  // selects one game. Otherwise the first held-out test game is the demo default.
  const requested = new URLSearchParams(location.search).get("game");
  const first = matches.find((match) => String(match.game_id) === previous)
    || matches.find((match) => String(match.game_id) === requested)
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

function usesNewMatch() {
  return Boolean(summary && summary.inference && summary.inference.role === "new_match");
}

// Held-out for an original test game, the fitted model for the other original
// games, and the saved-model forward pass for everything outside that set.
function primaryKind() {
  if (usesNewMatch()) return NEW_MATCH;
  return heldout && heldout.available ? HELD_OUT : FINAL;
}

function kindLabel(kind) {
  if (kind === HELD_OUT) return "Held-out model";
  if (kind === NEW_MATCH) return (summary && summary.inference && summary.inference.label) || "New-match prediction";
  return "Fitted model (in-sample: trained on this match)";
}

// Verified Overstat short names (static/map_names.json, copied from map_config.SHORT_NAMES).
const MAP_NAMES = {};

function readableMap(id) {
  if (id == null || id === "") return "Unknown map";
  if (Object.prototype.hasOwnProperty.call(MAP_NAMES, id)) return MAP_NAMES[id];
  if (/^(mp_|draft_|kc_)/.test(id) || String(id).includes("_")) return "Unknown map";
  return String(id);
}

function matchOptionLabel(match) {
  const heldoutFlag = match.heldout === true || (match.heldout && match.heldout.available === true);
  const collection = match.collection;
  const expansion = collection && collection !== "original" && collection !== "preview";
  const tag = heldoutFlag ? "[held-out] " : collection === "user-import" ? "[imported] " : expansion ? "[expansion] " : "";
  return `${tag}${match.event || "Untitled"} · ${readableMap(match.map_name)} · quality ${num(match.quality_score, 2)}`;
}

function isPreview(match) {
  const info = (match && match.inference) || {};
  return info.storage === "preview" || match.storage === "preview" || match.collection === "preview";
}

// The preview tooltip names the specific reason the replay was not saved when
// it is recorded; otherwise it falls back to what the score shows.
function previewCopy(match) {
  const reason = match.acceptance_reason || match.reason;
  const detail = match.acceptance_detail ? ` (${match.acceptance_detail})` : "";
  const score = qualityValue(match);
  const prefix = "Available for 24 hours. Not saved because ";
  if (reason === "below_quality_threshold" || (reason == null && score !== null && score < 7)) {
    return `${prefix}quality ${score !== null ? score.toFixed(2) + " " : ""}is below 7.0.`;
  }
  if (reason === "quality_unknown" || (reason == null && score === null)) return `${prefix}quality is unknown.`;
  if (reason === "score_scope_unresolved") return `${prefix}the quality score could not be matched to this game.`;
  if (reason === "outcome_unknown") return `${prefix}the winner is not verified${detail}.`;
  if (reason === "insufficient_position_data") return `${prefix}the replay has too little position data${detail}.`;
  if (reason === "reconstruction_failed") return `${prefix}the replay could not be fully reconstructed${detail}.`;
  if (reason) return `${prefix}the acceptance check "${reason}" failed${detail}.`;
  return `${prefix}an acceptance check failed.`;
}

function predictionBadge(kind) {
  if (kind === HELD_OUT && heldout && heldout.available) return { text: "Held-out", className: "heldout" };
  if (kind === NEW_MATCH) return { text: "New match", className: "newmatch" };
  const role = summary && summary.inference && summary.inference.role;
  if (kind === FINAL && role !== "unavailable" && role !== "new_match") return { text: "Fitted", className: "fitted" };
  return null;
}

function predictionCopy(kind) {
  const info = (summary && summary.inference) || {};
  if (kind === HELD_OUT) {
    return "This game is in the frozen held-out test split. The estimate uses the evaluation model fit on the other sessions, not on this game.";
  }
  if (kind === NEW_MATCH) {
    let text = info.membership === "verified_unseen"
      ? "This replay is not one of the games the saved model was trained on. The estimate is a forward pass of that model. It is not a held-out evaluation and was not fitted on this game."
      : (info.reason || "Training membership cannot be verified for this replay.");
    if (info.membership !== "verified_unseen" && !/independent evidence/i.test(text)) {
      text += " The estimate is not used as independent evidence.";
    }
    if (info.storage === "preview") text += " Performance on low-quality preview matches has not been established.";
    return text;
  }
  return "This game was part of the model’s training. The estimate is in-sample, not a held-out evaluation, and is not independent evidence.";
}

function helpHtml(id, label, text) {
  return `<span class="help"><button type="button" class="help-btn" aria-expanded="false" aria-describedby="${esc(id)}" aria-label="${esc(label)}">?</button><span id="${esc(id)}" class="help-bubble" role="tooltip" hidden>${esc(text)}</span></span>`;
}

function renderIdentity() {
  if (!summary) return;
  const preview = isPreview(summary);
  const badge = predictionBadge(primaryKind());
  const status = preview
    ? `<span class="badge preview">Temporary preview</span>${helpHtml("tip-status", "About temporary preview", previewCopy(summary))}`
    : `<span class="badge saved">Saved</span>`;
  const prediction = badge
    ? `<span class="badge ${badge.className}">${esc(badge.text)}</span>${helpHtml("tip-prediction", "About this prediction", predictionCopy(primaryKind()))}`
    : "";
  meta.innerHTML = `<span class="identity-event">${textOrUnknown(summary.event)}</span><span class="identity-map">${esc(readableMap(summary.map_name))}</span><span class="identity-status">${status}</span><span class="identity-pred">${prediction}</span>`;
}

function syncUnlistedOption() {
  const option = [...matchSelect.options].find((item) => item.value === String(summary.game_id));
  if (!option || option.dataset.listed === "1") return;
  option.textContent = matchOptionLabel(summary);
}

function membershipWarning() {
  const info = (summary && summary.inference) || {};
  if (info.role === "new_match" && info.membership && info.membership !== "verified_unseen") {
    return `<p class="unavailable small">Training membership is not verified, so this estimate is not independent evidence.</p>`;
  }
  return "";
}

function setStartStatus(message, isError) {
  const status = document.querySelector("#start-status");
  if (!status) return;
  status.hidden = !message;
  status.textContent = message || "";
  status.classList.toggle("unavailable", Boolean(isError && message));
}

function showStartHint() {
  const hint = document.querySelector("#start-hint");
  if (hint) hint.hidden = false;
}

// `t` is match-relative slider seconds, and only for the linked game.
function linkedSliderTime(gameId) {
  const params = new URLSearchParams(location.search);
  if (params.get("game") !== String(gameId)) return null;
  const raw = params.get("t");
  if (raw == null || !/^\d+$/.test(raw)) return null;
  return Number(raw);
}

function rememberStartLink() {
  const params = new URLSearchParams(location.search);
  params.set("game", START_GAME);
  params.set("team", START_TEAM);
  params.set("t", String(START_ELAPSED));
  history.replaceState(null, "", `${location.pathname}?${params.toString()}`);
}

// Opens the checked demo moment. It never requests a situation or decision review.
// A match that is already loaded is only reselected and seeked, so the map
// camera stays where it is. map_view.js resets the camera itself when the map changes.
async function startHere() {
  showStartHint();
  const listed = [...matchSelect.options].some((option) => option.value === START_GAME);
  if (!listed) {
    setStartStatus("Demo match 123633 is not available.", true);
    return;
  }
  rememberStartLink();
  const ready = summary && String(summary.game_id) === START_GAME && !timeInput.disabled;
  if (!ready) {
    if (activeLoad && matchSelect.value === START_GAME) await activeLoad;
    else {
      matchSelect.value = START_GAME;
      await loadMatch(START_GAME);
    }
  }
  if (!summary || String(summary.game_id) !== START_GAME) {
    setStartStatus("Demo match 123633 could not be loaded.", true);
    timeInput.disabled = false;
    return;
  }
  if (![...squadSelect.options].some((option) => option.value === START_TEAM)) {
    setStartStatus("EQT (team 20) is not in this match.", true);
    return;
  }
  const maxElapsed = Number(timeInput.max);
  if (!(maxElapsed >= START_ELAPSED)) {
    setStartStatus("18:47 is outside this replay.", true);
    return;
  }
  squadSelect.value = START_TEAM;
  applySquadSelection();
  timeInput.value = String(START_ELAPSED);
  seek(START_ELAPSED);
  setStartStatus("");
}

async function loadMatch(gameId) {
  const run = loadMatchBody(gameId);
  activeLoad = run;
  try {
    return await run;
  } finally {
    if (activeLoad === run) activeLoad = null;
  }
}

async function loadMatchBody(gameId) {
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
    timeInput.disabled = false;
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
  renderIdentity();
  syncUnlistedOption();
  // The transform note is in Model details; only a missing map is shown here.
  note.hidden = Boolean(mapConfig && mapConfig.supported);
  note.textContent = note.hidden ? "" : (mapConfig && mapConfig.message) || "Map background unavailable.";
  squadSelect.innerHTML = "";
  const seenNames = new Map();
  for (const squad of summary.squads) {
    const name = squad.teamName || "Unknown";
    seenNames.set(name, (seenNames.get(name) || 0) + 1);
  }
  const used = new Map();
  for (const squad of summary.squads) {
    const option = document.createElement("option");
    option.value = String(squad.teamId);
    const name = squad.teamName || "Unknown";
    const count = (used.get(name) || 0) + 1;
    used.set(name, count);
    option.textContent = seenNames.get(name) > 1 ? `${name} (${count})` : name;
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
  const maxElapsed = Math.max(0, (summary.last_t ?? replayStart()) - replayStart());
  const requestedTime = linkedSliderTime(gameId);
  const initialElapsed = requestedTime == null ? 0 : Math.min(requestedTime, maxElapsed);
  timeInput.min = "0";
  timeInput.max = String(maxElapsed);
  timeInput.value = String(initialElapsed);
  timeInput.disabled = false;
  frame = null;
  currentRing = null;
  clock.textContent = mmss(initialElapsed);
  renderDetails();
  loadHighlights();
  if (requestedTime != null && requestedTime > maxElapsed) {
    setStartStatus(`That time is past the end of this replay. Showing ${mmss(initialElapsed)}.`);
  }
  if (String(gameId) === START_GAME && squadSelect.value === START_TEAM && initialElapsed === START_ELAPSED) showStartHint();
  await loadFrame(initialElapsed);
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
    syncTimeline();
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

// A team name with its map color, resolved by team id (the map's colorFor).
function teamTagFor(teamId, fallbackName) {
  return window.UI.teamTag(squadName(teamId) || fallbackName, colorFor(teamId));
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
  const heading = document.querySelector("#squad-tag");
  if (heading) heading.innerHTML = teamTagFor(team);
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

// Clickable squad comparison. The top TOP_SQUADS squads in contention by
// unrounded probability come first; the selected squad is always shown, under
// them with its actual rank when it is not among them (eliminated: 0%, no
// rank). Other squads in contention and eliminated squads are folded into
// their own expandable lists. Display only: every value is the model's own,
// never renormalized over the visible rows.
const TOP_SQUADS = 5;
function comparisonTable(squads, valueKey) {
  const team = selectedTeam();
  const isSelected = (squad) => String(squad.teamId) === team;
  const row = (squad, rank) => `<tr class="${isSelected(squad) ? "selected" : ""} ${squad.eliminated ? "out" : ""}">
      <td class="rank">${rank || ""}</td>
      <td><button type="button" class="squad-name" data-team-id="${esc(squad.teamId)}">${teamTagFor(squad.teamId, squad.team_name)}</button>${squad.eliminated && isSelected(squad) ? '<span class="when"> · eliminated</span>' : ""}</td>
      <td class="num">${pct(squad[valueKey])}</td>
    </tr>`;
  const sorted = [...squads].sort((a, b) => b[valueKey] - a[valueKey] || String(a.teamId).localeCompare(String(b.teamId)));
  const live = sorted.filter((squad) => !squad.eliminated).map((squad, index) => ({ squad, rank: index + 1 }));
  const top = live.slice(0, TOP_SQUADS);
  const pinned = sorted.find((squad) => isSelected(squad) && (squad.eliminated || !top.some((item) => item.squad === squad)));
  const pinnedRow = pinned ? row(pinned, pinned.eliminated ? null : live.find((item) => item.squad === pinned).rank) : "";
  const others = live.slice(TOP_SQUADS).filter((item) => item.squad !== pinned);
  const out = sorted.filter((squad) => squad.eliminated && squad !== pinned);
  const outLabel = `${out.length}${pinned && pinned.eliminated ? " other" : ""} eliminated squad${out.length === 1 ? "" : "s"} (0.0%)`;
  return `<table class="probs compact"><tbody>${top.map((item) => row(item.squad, item.rank)).join("")}</tbody>
      ${pinnedRow ? `<tbody class="pinned">${pinnedRow}</tbody>` : ""}</table>
    ${others.length ? `<details class="sub" ${window.UI.keep("compare-other")}><summary>Other squads (${others.length})</summary><table class="probs compact"><tbody>${others.map((item) => row(item.squad, item.rank)).join("")}</tbody></table></details>` : ""}
    ${out.length ? `<details class="sub" ${window.UI.keep("compare-out")}><summary>${outLabel}</summary><table class="probs compact"><tbody>${out.map((squad) => row(squad, null)).join("")}</tbody></table></details>` : ""}`;
}

// The full per-squad table used in Model details, with the baseline column.
function probabilityTable(squads, valueKey, valueLabel, baselineKey) {
  const team = selectedTeam();
  const rows = [...squads]
    .sort((a, b) => b[valueKey] - a[valueKey])
    .map((squad) => `<tr class="${String(squad.teamId) === team ? "selected" : ""} ${squad.eliminated ? "out" : ""}">
        <td><button type="button" class="squad-name" data-team-id="${esc(squad.teamId)}">${teamTagFor(squad.teamId, squad.team_name)}${squad.eliminated ? " · eliminated" : ""}</button></td>
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
  if (kind === NEW_MATCH) {
    const info = (summary && summary.inference) || {};
    const live = ((frame && frame.live) || {}).new_match || {};
    const outcomePart = frame && frame.finished ? recordedOutcome() : "";
    const livePart = live.status === "loading"
      ? `<p class="when">Computing win estimates from the saved model.</p>`
      : live.point
        ? livePanel(live, info.label || "New-match prediction")
        : `<p class="unavailable">${esc(live.reason || info.reason || "No estimate at this second.")}</p>`;
    return `<p><span class="tag newmatch">new match</span>${esc(info.label || info.reason || "")}</p>${info.limitation ? `<p class="when">${esc(info.limitation)}</p>` : ""}${outcomePart}${livePart}`;
  }
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

function estimateValue(live) {
  const point = live.point;
  const team = selectedTeam();
  const row = point.squads.find((squad) => String(squad.teamId) === team);
  const ranked = point.squads.filter((squad) => !squad.eliminated).sort((a, b) => b.model - a.model);
  const rank = row && !row.eliminated ? ranked.findIndex((squad) => String(squad.teamId) === team) + 1 : null;
  return { row, rank, ranked };
}

function renderNewMatchEstimate() {
  const info = summary.inference || {};
  const live = ((frame && frame.live) || {}).new_match || {};
  const warn = membershipWarning();
  if (live.status === "loading") {
    estimateBox.innerHTML = `<div class="estimate-head"><div><span class="when">Computing win estimates from the saved model.</span></div></div>${warn}`;
    scheduleEstimatePoll();
    return;
  }
  if (live.status === "unavailable" || live.available === false) {
    estimateBox.innerHTML = `<p class="unavailable">No estimate is available for this match.</p><p class="when">${esc(live.reason || info.reason || "")}</p>${warn}`;
    return;
  }
  if (frame && frame.finished) {
    estimateBox.innerHTML = `<div class="estimate-head"><div><span class="when">Match finished; no estimate after the result.</span></div></div>${recordedOutcome()}${warn}`;
    return;
  }
  if (!live.point) {
    const reason = live.reason ? `: ${esc(live.reason)}` : "";
    estimateBox.innerHTML = `<div class="estimate-head"><div><span class="unavailable">No estimate at ${frame ? mmss(frame.elapsed) : "this time"}${reason}.</span></div></div>${warn}`;
    return;
  }
  const { row, rank, ranked } = estimateValue(live);
  estimateBox.innerHTML = `<div class="estimate-head">
      <div class="estimate-value">${row ? pct(row.model) : "Unknown"}</div>
      <div class="estimate-text">
        <strong>${teamTagFor(selectedTeam())}</strong>${row && row.eliminated ? " · eliminated" : rank ? ` · rank ${rank} of ${ranked.length} in contention` : ""}<br>
        <span class="when">at ${mmss(live.point.elapsed)} · baseline ${row ? pct(row.uniform_contention) : "Unknown"}</span>
      </div>
    </div>
    ${warn}
    ${live.point.eligibility === "unverified" ? `<p class="unavailable small">${eligibilityText(live.point)}</p>` : ""}
    ${comparisonTable(live.point.squads, "model")}`;
}

let estimateTimer = null;
function scheduleEstimatePoll() {
  clearTimeout(estimateTimer);
  const elapsed = Number(timeInput.value);
  estimateTimer = setTimeout(() => {
    if (!usesNewMatch() || !frame) return;
    const live = frame.live && frame.live.new_match;
    if (!live || live.status !== "loading") return;
    if (inFlight || pendingSeek !== null || Number(timeInput.value) !== elapsed) {
      scheduleEstimatePoll();
      return;
    }
    loadFrame(elapsed);
  }, 1200);
}

// The one primary estimate on the page.
function renderEstimate() {
  const kind = primaryKind();
  if (kind === NEW_MATCH) {
    renderNewMatchEstimate();
    return;
  }
  const view = kind === HELD_OUT ? heldout : curve;
  const live = ((frame && frame.live) || {})[kind === HELD_OUT ? "heldout" : "final"];
  const role = summary && summary.inference && summary.inference.role;
  const tag = kind === FINAL && role !== "unavailable" && role !== "new_match" ? `<span class="tag fitted">in-sample</span> ` : "";
  if (!view || !view.available) {
    estimateBox.innerHTML = `<p class="unavailable">No estimate is available for this match.</p><p class="when">${esc((view && (view.reason || view.message)) || "")}</p>`;
    return;
  }
  const info = "";
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
        <strong>${teamTagFor(team)}</strong>${row && row.eliminated ? " · eliminated" : rank ? ` · rank ${rank} of ${ranked.length} in contention` : ""}<br>
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
  const alt = kind === HELD_OUT ? FINAL : kind === NEW_MATCH ? FINAL : HELD_OUT;
  const squadLine = (summary.squads || []).map((squad) => `${esc(squad.teamName || "Unknown")} (${esc(squad.teamId)})`).join(", ") || "Unknown";
  document.querySelector("#backend-details").innerHTML = backendInfo
    ? `<dl class="facts"><dt>Backend</dt><dd>${esc(backendInfo.label || backendInfo.backend || "Unknown")}</dd><dt>Connection</dt><dd>${esc(backendInfo.detail || "Unknown")}</dd></dl>`
    : `<p class="when">Data backend: Unknown</p>`;
  document.querySelector("#match-details").innerHTML = `<dl class="facts">
      <dt>Game id</dt><dd>${esc(summary.game_id)}</dd>
      <dt>Session id</dt><dd>${textOrUnknown(summary.session_id)}</dd>
      <dt>Event</dt><dd>${textOrUnknown(summary.event)}</dd>
      <dt>Map</dt><dd>${esc(readableMap(summary.map_name))}</dd>
      <dt>Map id</dt><dd>${textOrUnknown(summary.map_name)}</dd>
      <dt>Quality</dt><dd>${num(summary.quality_score, 2)}. An import is saved at 7.0 or above only when every acceptance check passes.</dd>
      <dt>Squads</dt><dd>${squadLine}</dd>
      <dt>Winner</dt><dd>${summary.winner && summary.winner.teamName ? `${esc(summary.winner.teamName)} (${textOrUnknown(summary.winner.teamId)})` : "Unknown"}</dd>
    </dl>
    <p class="when">${frame ? `Showing ${mmss(frame.elapsed)} (replay t ${frame.t}); Playing started at replay t ${replayStart()}.` : ""}</p>
    <p class="when">${mapConfig && mapConfig.supported ? "Positions use the Overstat mapCord transform on the matching 2048 map. z is omitted. Only the current ring is shown." : esc((mapConfig && mapConfig.message) || "Map background unavailable.")}</p>
    <p class="when">${mapConfig && mapConfig.supported ? "" : `${esc(summary.coordinate_note || "")} `}Distances and heights elsewhere on this page are in unverified game units.</p>`;
  provenanceBox.innerHTML = frame ? predictionPanel(kind) : `<p class="when">Loading…</p>`;
  document.querySelector("#alt-title").textContent = alt === HELD_OUT ? "Alternate estimate: held-out model" : "Alternate estimate: fitted model (in-sample)";
  if (kind === NEW_MATCH) {
    document.querySelector("#alt-title").textContent = "Frozen fitted model (not used for this match)";
  }
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
  const team = teamId == null ? "" : ` (${teamTagFor(teamId)})`;
  return `${name ? esc(name) : "a player"}${team}`;
}

// One recorded event as a short clause. Recorded facts only; no cause is implied.
function eventClause(event) {
  if (event.type === "knock") return `${who(event.actor_name, event.actor_team_id)} knocked ${who(event.victim_name, event.victim_team_id)}`;
  if (event.type === "revive") return `${who(event.actor_name, event.actor_team_id)} revived ${event.victim_name ? who(event.victim_name, event.victim_team_id) : "a teammate"}`;
  if (event.type === "respawn") return `${who(event.victim_name || event.actor_name, event.victim_team_id ?? event.actor_team_id)} respawned`;
  if (event.type === "squad_elimination") {
    return event.ambiguous || event.victim_team_id == null ? "a squad was eliminated (team unclear)" : `${teamTagFor(event.victim_team_id)} was eliminated`;
  }
  return "";
}

// One short sentence for the card: the most relevant recorded event in the
// representative window, preferring the highlighted squad. It states what was
// recorded; it does not say the event caused the change. The rest is in Details.
function eventSentence(item) {
  const all = (Array.isArray(item.events) ? item.events : []).filter((event) => EVENT_TYPES.has(event.type));
  if (!all.length) return "Win estimate changed; no matching event was identified.";
  const squad = item.squad_id != null ? String(item.squad_id) : null;
  const victim = (event) => squad !== null ? String(event.victim_team_id) === squad : event.side === "squad";
  const actor = (event) => squad !== null ? String(event.actor_team_id) === squad : false;
  const rank = (event) => {
    if (victim(event) && event.type === "squad_elimination") return 0;
    if (victim(event) && event.type === "knock") return 1;
    if (victim(event) || actor(event) || event.side === "squad") return 2;
    return event.type === "squad_elimination" ? 3 : 4;
  };
  const lead = [...all].sort((a, b) => rank(a) - rank(b) || (Number(a.t) || 0) - (Number(b.t) || 0))[0];
  return shortEvent(lead, victim(lead), actor(lead));
}

function shortEvent(event, onSquad, bySquad) {
  const name = (text, start) => (text ? esc(text) : start ? "A player" : "a player");
  const team = (id) => (id == null ? "an unknown squad" : teamTagFor(id));
  if (event.type === "knock") {
    if (onSquad) return `${name(event.victim_name, true)} was knocked by ${team(event.actor_team_id)}.`;
    if (bySquad) return `${name(event.actor_name, true)} knocked ${name(event.victim_name)} of ${team(event.victim_team_id)}.`;
    return `${team(event.actor_team_id)} knocked ${name(event.victim_name)} of ${team(event.victim_team_id)}.`;
  }
  if (event.type === "squad_elimination") {
    return event.ambiguous || event.victim_team_id == null ? "A squad was eliminated (team unclear)." : `${team(event.victim_team_id)} was eliminated.`;
  }
  if (event.type === "revive") return `${name(event.actor_name, true)} revived ${event.victim_name ? name(event.victim_name) : "a teammate"}.`;
  if (event.type === "respawn") return `${name(event.victim_name || event.actor_name, true)} respawned.`;
  return "Win estimate changed; no matching event was identified.";
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
  const count = (Array.isArray(item.events) ? item.events : []).length;
  return `<details class="sub" ${window.UI.keep(key)}><summary>Details${count ? ` · ${count} recorded event${count === 1 ? "" : "s"}` : ""}</summary>
    <p class="when">Events listed here coincide with the representative window. They are not identified as causes of the change.</p>
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
    if (body.status === "loading") {
      box.innerHTML = `${head(0, 0)}<p class="when">Computing win estimates from the saved model.</p>`;
      return;
    }
    const reason = body.error || (body.missing || []).join("; ") || (result.status ? `HTTP ${result.status}` : "");
    box.innerHTML = `${head(0, 0)}${unavailableLine(reason)}`;
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

function unavailableLine(reason) {
  const text = reason ? `: ${esc(reason)}` : "";
  const stop = reason && /[.!?]$/.test(reason) ? "" : ".";
  return `<p class="unavailable">Not available${text}${stop}</p>`;
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
      box.innerHTML = unavailableLine(reason);
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

function highlightKinds() {
  return usesNewMatch() ? [NEW_MATCH] : [HELD_OUT, FINAL];
}

function watchHighlights(token, gameId, kind, team) {
  fetchHighlights(gameId, kind, team).then((result) => {
    if (token !== highlightToken) return;
    highlightResults[kind] = result;
    if (kind === primaryKind()) {
      renderCarousel("up");
      renderCarousel("down");
    } else if (detailsDialog.open) {
      renderHighlightList(altHighlights, result);
    }
    if (result.body && result.body.status === "loading") {
      setTimeout(() => watchHighlights(token, gameId, kind, team), 1200);
    }
  });
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
  for (const kind of highlightKinds()) watchHighlights(token, gameId, kind, team);
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
  syncTimeline();
}

function syncTimeline() {
  if (!window.EventTimeline || !summary) return;
  safely(() => window.EventTimeline.sync({
    gameId: summary.game_id,
    team: selectedTeam(),
    elapsed: frame ? frame.elapsed : Number(timeInput.value) || 0,
    duration: Number(timeInput.max) || 0,
    seek: seekHighlight,
  }));
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
const startButton = document.querySelector("#start-here");
if (startButton) {
  startButton.addEventListener("click", () => {
    startHere().catch((error) => {
      console.error(error);
      timeInput.disabled = false;
      setStartStatus("Demo match 123633 could not be loaded.", true);
    });
  });
}
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
    // This match's teamId -> name/color mapping, captured with the request so a
    // review keeps its own colors after the selection changes.
    squads: (summary.squads || []).map(({ teamId, teamName, color }) => ({ teamId, teamName, color })),
  } : null;
}
if (window.Coach) safely(() => window.Coach.init({ context: currentMoment, seek: seekHighlight }));
if (window.DecisionReview) safely(() => window.DecisionReview.init({ context: currentMoment, seek: seekHighlight, redraw: () => { if (frame) draw(); } }));
function ensureOption(gameId, label) {
  const value = String(gameId);
  if ([...matchSelect.options].some((option) => option.value === value)) {
    matchSelect.value = value;
    return;
  }
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  matchSelect.append(option);
  matchSelect.value = value;
}

function setOpenStatus(text, tone) {
  const status = document.querySelector("#open-status");
  status.hidden = !text;
  status.textContent = text || "";
  status.classList.toggle("unavailable", tone === "error");
}

async function openOverstat(url, gameNumber) {
  const choices = document.querySelector("#open-choices");
  setOpenStatus("Opening…");
  choices.hidden = true;
  choices.innerHTML = "";
  const body = { url };
  if (gameNumber != null) body.game_number = gameNumber;
  let result = null;
  try {
    const response = await fetch("/api/open-match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    result = await response.json();
  } catch (error) {
    setOpenStatus("The match could not be opened.", "error");
    return;
  }
  if (result.status === "choose") {
    setOpenStatus(result.message || "This session has more than one game. Choose one to open.");
    choices.hidden = false;
    for (const game of result.games || []) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "action quiet small";
      const quality = game.quality_score == null ? "quality unknown" : `quality ${num(game.quality_score, 2)}`;
      const gameNumberText = game.game_number == null || game.game_number === "" ? "Unknown" : String(game.game_number);
      button.textContent = `Game ${gameNumberText} · ${readableMap(game.map_name)} · ${quality}`;
      button.addEventListener("click", () => openOverstat(url, game.game_number));
      choices.append(button);
    }
    return;
  }
  if (result.status === "retry") {
    setOpenStatus(result.message || "Overstat rate-limited this request. Wait and try the same URL again.", "error");
    return;
  }
  if (result.status !== "ready" && result.status !== "exists") {
    setOpenStatus(result.message || "This URL could not be opened.", "error");
    return;
  }
  const saved = result.storage !== "preview";
  setOpenStatus(result.message || (saved ? "Saved." : "Temporary preview."));
  if (!saved) {
    ensureOption(result.game_id, "Opening…");
    await loadMatch(result.game_id);
    return;
  }
  await loadMatches(result.game_id);
  if (matchSelect.value !== String(result.game_id)) {
    ensureOption(result.game_id, "Opening…");
    await loadMatch(result.game_id);
  }
}

document.querySelector("#open-match").addEventListener("submit", (event) => {
  event.preventDefault();
  const url = document.querySelector("#overstat-url").value.trim();
  if (!url) {
    setOpenStatus("Paste a public Overstat match, session, or replay URL.", "error");
    return;
  }
  openOverstat(url);
});

// Help bubbles: hover and keyboard focus show them, click or tap pins them,
// and Escape dismisses. The bubble stays open while the pointer moves into it.
function installHelp() {
  const ESSENTIAL = "#match, #squad, #time, #overstat-url, #open-match button, #plot, #details-open, #start-here, #start-hint, #start-status, #event-timeline, #ring-status, #coach-run, .topbar a, .topbar h1";
  let hideTimer = null;

  function bubbleOf(help) { return help.querySelector(".help-bubble"); }
  function buttonOf(help) { return help.querySelector(".help-btn"); }

  function hide(help) {
    const bubble = bubbleOf(help);
    const button = buttonOf(help);
    if (bubble) bubble.hidden = true;
    if (button) button.setAttribute("aria-expanded", "false");
    help.classList.remove("is-pinned");
  }

  function overlapArea(rect, boxes) {
    let area = 0;
    for (const box of boxes) {
      const x = Math.max(0, Math.min(rect.right, box.right) - Math.max(rect.left, box.left));
      const y = Math.max(0, Math.min(rect.bottom, box.bottom) - Math.max(rect.top, box.top));
      area += x * y;
    }
    return area;
  }

  function place(help) {
    const bubble = bubbleOf(help);
    const button = buttonOf(help);
    if (!bubble || !button) return;
    bubble.hidden = false;
    const margin = 8;
    // On a narrow screen the bubble uses the full width so it is short enough
    // to sit under the map instead of on the selectors.
    const width = window.innerWidth < 720
      ? Math.max(160, window.innerWidth - 16)
      : Math.min(320, window.innerWidth - margin * 2);
    bubble.style.width = `${width}px`;
    const height = bubble.offsetHeight;
    const anchor = button.getBoundingClientRect();
    const essentials = [...document.querySelectorAll(ESSENTIAL)].map((el) => el.getBoundingClientRect());
    const clamp = (left, top) => ({
      left: Math.min(Math.max(0, left), Math.max(0, window.innerWidth - width)),
      top: Math.min(Math.max(0, top), Math.max(0, window.innerHeight - height)),
    });
    let best = null;
    const consider = (left, top) => {
      const pos = clamp(left, top);
      const rect = { left: pos.left, top: pos.top, right: pos.left + width, bottom: pos.top + height };
      const overlap = overlapArea(rect, essentials);
      const dist = Math.hypot(rect.left - anchor.right, rect.top - anchor.top);
      const score = overlap * 1000 + dist;
      if (!best || score < best.score) best = { ...pos, score, overlap };
    };
    const gap = 8;
    consider(anchor.right + gap, anchor.top);
    consider(anchor.left, anchor.bottom + gap);
    consider(anchor.left - width - gap, anchor.top);
    consider(anchor.left, anchor.top - height - gap);
    const plot = document.querySelector("#plot");
    const controls = document.querySelector(".controls");
    if (plot && controls) {
      const map = plot.getBoundingClientRect();
      const bar = controls.getBoundingClientRect();
      consider(map.right + gap, bar.bottom + gap);
      consider(map.right + gap, anchor.top);
    }
    for (const box of essentials) {
      consider(margin, box.bottom + 4);
      consider(margin, box.top - height - 4);
    }
    if (!best || best.overlap > 0) {
      for (let top = margin; top <= window.innerHeight - height - margin; top += 28) {
        for (let left = margin; left <= window.innerWidth - width - margin; left += 28) consider(left, top);
      }
    }
    bubble.style.left = `${best.left}px`;
    bubble.style.top = `${best.top}px`;
    button.setAttribute("aria-expanded", "true");
  }

  function show(help) {
    clearTimeout(hideTimer);
    document.querySelectorAll(".help").forEach((other) => {
      if (other !== help && !other.classList.contains("is-pinned")) hide(other);
    });
    place(help);
  }

  function scheduleHide(help) {
    if (!help || help.classList.contains("is-pinned")) return;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      if (!help.classList.contains("is-pinned") && !help.matches(":hover") && !help.contains(document.activeElement)) hide(help);
    }, 700);
  }

  document.addEventListener("pointerover", (event) => {
    const help = event.target.closest(".help");
    if (!help || help.dataset.suppress === "1") return;
    show(help);
  });
  document.addEventListener("pointerout", (event) => {
    const help = event.target.closest(".help");
    if (!help) return;
    if (event.relatedTarget && help.contains(event.relatedTarget)) return;
    delete help.dataset.suppress;
    scheduleHide(help);
  });
  document.addEventListener("focusin", (event) => {
    const button = event.target.closest(".help-btn");
    if (!button) return;
    const help = button.closest(".help");
    if (!help || help.dataset.suppress === "1") return;
    show(help);
  });
  document.addEventListener("focusout", (event) => {
    const help = event.target.closest(".help");
    if (help) scheduleHide(help);
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest(".help-btn");
    if (button) {
      event.preventDefault();
      event.stopPropagation();
      const help = button.closest(".help");
      if (help.classList.contains("is-pinned")) {
        help.dataset.suppress = "1";
        hide(help);
      } else {
        document.querySelectorAll(".help.is-pinned").forEach((other) => hide(other));
        delete help.dataset.suppress;
        help.classList.add("is-pinned");
        show(help);
      }
      return;
    }
    if (event.target.closest(".help-bubble")) return;
    document.querySelectorAll(".help.is-pinned").forEach((help) => hide(help));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const open = [...document.querySelectorAll(".help")].filter((help) => {
      const bubble = bubbleOf(help);
      return bubble && !bubble.hidden;
    });
    for (const help of open) {
      help.dataset.suppress = "1";
      hide(help);
    }
  });
  const reposition = () => {
    document.querySelectorAll(".help").forEach((help) => {
      const bubble = bubbleOf(help);
      if (bubble && !bubble.hidden) place(help);
    });
  };
  window.addEventListener("scroll", reposition, true);
  window.addEventListener("resize", reposition);
}

async function start() {
  try {
    Object.assign(MAP_NAMES, await (await fetch("/static/map_names.json")).json());
  } catch (error) {
    console.error(error);
  }
  installHelp();
  loadBackend();
  loadMatches();
}

start();
