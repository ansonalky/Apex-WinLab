const statusBox = document.querySelector("#stats-status");
const noteBox = document.querySelector("#stats-note");
const table = document.querySelector("#stats-table");
const detail = document.querySelector("#detail");
const mapSelect = document.querySelector("#map");
const sessionSelect = document.querySelector("#session");
const formatSelect = document.querySelector("#format");
const scoringSelect = document.querySelector("#scoring");
const minInput = document.querySelector("#min-games");

let mode = "teams";
let teamRows = [];
let playerRows = [];
let sortKey = "rank";
let sortDir = 1;

function filters() {
  const params = new URLSearchParams();
  if (mapSelect.value) params.set("map", mapSelect.value);
  if (sessionSelect.value) params.set("session", sessionSelect.value);
  params.set("format", formatSelect.value);
  params.set("scoring", scoringSelect.value);
  params.set("min_games", minInput.value || "5");
  return params;
}

function num(value, digits) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toFixed(digits);
}

function pct(value) {
  if (value === null || value === undefined) return "—";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

function header(label, key) {
  const th = document.createElement("th");
  th.textContent = sortKey === key ? `${label}${sortDir > 0 ? " ↑" : " ↓"}` : label;
  th.addEventListener("click", () => {
    sortDir = sortKey === key ? -sortDir : (key === "team_name" || key === "player_name" ? 1 : -1);
    sortKey = key;
    renderTable();
  });
  return th;
}

async function loadOptions() {
  const response = await fetch("/api/stats/options");
  const body = await response.json();
  if (!response.ok || !body.ok) throw new Error(body.error || "Filters could not be loaded.");
  for (const map of body.maps) {
    const option = document.createElement("option");
    option.value = map.id;
    option.textContent = map.label === map.id ? map.id : `${map.label} (${map.id})`;
    mapSelect.append(option);
  }
  for (const session of body.sessions) {
    const option = document.createElement("option");
    option.value = String(session.id);
    option.textContent = `${session.label} · ${session.id}`;
    sessionSelect.append(option);
  }
}

async function loadTable() {
  statusBox.textContent = "Loading collected results…";
  table.replaceChildren();
  const path = mode === "teams" ? "/api/stats/teams" : "/api/stats/players";
  const response = await fetch(`${path}?${filters()}`);
  const body = await response.json();
  if (!response.ok || !body.ok) {
    statusBox.textContent = "";
    noteBox.textContent = "";
    table.replaceChildren();
    const row = document.createElement("p");
    row.className = "unavailable";
    row.textContent = body.error || "Tiger Data is not available. Results stay hidden until the database can be queried.";
    statusBox.append(row);
    return;
  }
  noteBox.textContent = body.note || "";
  if (mode === "teams") {
    teamRows = body.rows;
    sortKey = body.rank_by === "avg_points" ? "avg_points" : "avg_placement";
    sortDir = body.rank_by === "avg_points" ? -1 : 1;
  } else {
    playerRows = body.rows;
    sortKey = "games";
    sortDir = -1;
  }
  const count = mode === "teams" ? teamRows.length : playerRows.length;
  statusBox.textContent = count ? `${count} ${mode === "teams" ? "teams" : "players"}` : "";
  renderTable();
}

function sorted(rows) {
  const copy = rows.slice();
  copy.sort((a, b) => {
    const left = a[sortKey];
    const right = b[sortKey];
    if (left == null && right == null) return 0;
    if (left == null) return 1;
    if (right == null) return -1;
    if (typeof left === "string") return left.localeCompare(right) * sortDir;
    return (left - right) * sortDir;
  });
  return copy;
}

function renderTable() {
  table.replaceChildren();
  const rows = mode === "teams" ? sorted(teamRows) : sorted(playerRows);
  if (!rows.length && !statusBox.querySelector(".unavailable")) {
    statusBox.textContent = mode === "teams" ? "No teams meet these filters." : "No players meet these filters.";
  }
  const head = document.createElement("tr");
  if (mode === "teams") {
    [
      ["Team", "team_name"],
      ["Games", "games"],
      ["Sessions", "sessions"],
      ["Avg points", "avg_points"],
      ["Avg place", "avg_placement"],
      ["Kills / game", "kills_per_game"],
      ["Damage / game", "damage_per_game"],
      ["Win rate", "win_rate"],
      ["Top 5", "top5_rate"],
    ].forEach(([label, key]) => head.append(header(label, key)));
  } else {
    [
      ["Player", "player_name"],
      ["Recorded teams", "team_names"],
      ["Games", "games"],
      ["Kills / game", "kills_per_game"],
      ["Damage / game", "damage_per_game"],
      ["Assists / game", "assists_per_game"],
    ].forEach(([label, key]) => head.append(header(label, key)));
  }
  table.append(head);
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    if (mode === "teams") {
      const name = document.createElement("td");
      name.textContent = row.team_name || "Unnamed";
      if (row.provisional) {
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = " provisional";
        name.append(tag);
      }
      if (row.flags && row.flags.length) {
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = " identity";
        tag.title = row.flags.join(" ");
        name.append(tag);
      }
      tr.append(name);
      tr.append(cell(String(row.games), "num"), cell(String(row.sessions), "num"));
      tr.append(cell(num(row.avg_points, 1), "num"), cell(num(row.avg_placement, 2), "num"));
      tr.append(cell(num(row.kills_per_game, 2), "num"), cell(num(row.damage_per_game, 0), "num"));
      tr.append(cell(pct(row.win_rate), "num"), cell(pct(row.top5_rate), "num"));
      tr.addEventListener("click", () => showTeam(row));
    } else {
      const name = document.createElement("td");
      name.textContent = row.player_name || "Unnamed";
      if (row.provisional) {
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = " provisional";
        name.append(tag);
      }
      tr.append(name);
      const teams = document.createElement("td");
      teams.textContent = (row.team_names || []).join(", ");
      tr.append(teams);
      tr.append(cell(String(row.games), "num"));
      tr.append(cell(num(row.kills_per_game, 2), "num"));
      tr.append(cell(num(row.damage_per_game, 0), "num"));
      tr.append(cell(num(row.assists_per_game, 2), "num"));
      tr.addEventListener("click", () => showPlayer(row));
    }
    table.append(tr);
  }
}

function matchLink(match) {
  const link = document.createElement("a");
  const team = match.team_id == null ? "" : `&team=${match.team_id}`;
  link.href = `/?game=${match.game_id}${team}`;
  link.textContent = `${match.event || "Match"} · ${match.map_name || ""}`;
  return link;
}

async function showTeam(row) {
  detail.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = row.team_name;
  detail.append(title);
  const response = await fetch(`/api/stats/teams/${encodeURIComponent(row.roster_key)}?${filters()}`);
  const body = await response.json();
  if (!response.ok || !body.ok) {
    const p = document.createElement("p");
    p.className = "unavailable";
    p.textContent = body.error || "This team's matches could not be loaded.";
    detail.append(p);
    return;
  }
  fillMatches(body.matches, (match) => [
    `Place ${match.placement == null ? "—" : match.placement}`,
    `kills ${match.kills == null ? "—" : match.kills}`,
    `damage ${match.damage == null ? "—" : match.damage}`,
    match.points_comparable ? `points ${match.score}` : `score ${match.score == null ? "—" : match.score} (not compatible with the placement + kill scheme)`,
  ].join(" · "));
}

async function showPlayer(row) {
  detail.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = row.player_name;
  detail.append(title);
  const response = await fetch(`/api/stats/players/${row.player_id}?${filters()}`);
  const body = await response.json();
  if (!response.ok || !body.ok) {
    const p = document.createElement("p");
    p.className = "unavailable";
    p.textContent = body.error || "This player's matches could not be loaded.";
    detail.append(p);
    return;
  }
  fillMatches(body.matches, (match) => [
    match.team_name,
    `kills ${match.kills == null ? "—" : match.kills}`,
    `damage ${match.damage == null ? "—" : match.damage}`,
    `assists ${match.assists == null ? "—" : match.assists}`,
  ].join(" · "));
}

function fillMatches(matches, line) {
  if (!matches.length) {
    const p = document.createElement("p");
    p.textContent = "No matches in this filter.";
    detail.append(p);
    return;
  }
  for (const match of matches) {
    const block = document.createElement("article");
    block.className = "player";
    block.append(matchLink(match));
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `Game ${match.game_id} · session ${match.session_id} · ${line(match)}`;
    block.append(meta);
    detail.append(block);
  }
}

document.querySelector("#tab-teams").addEventListener("click", () => {
  mode = "teams";
  document.querySelector("#tab-teams").classList.add("on");
  document.querySelector("#tab-players").classList.remove("on");
  loadTable().catch(showFailure);
});
document.querySelector("#tab-players").addEventListener("click", () => {
  mode = "players";
  document.querySelector("#tab-players").classList.add("on");
  document.querySelector("#tab-teams").classList.remove("on");
  loadTable().catch(showFailure);
});
for (const input of [mapSelect, sessionSelect, formatSelect, scoringSelect, minInput]) {
  input.addEventListener("change", () => loadTable().catch(showFailure));
}

function showFailure(error) {
  statusBox.textContent = "";
  const p = document.createElement("p");
  p.className = "unavailable";
  p.textContent = "Tiger Data is not available. Results stay hidden until the database can be queried.";
  statusBox.append(p);
  console.error(error);
}

loadOptions().then(loadTable).catch(showFailure);
