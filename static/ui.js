// Shared display formats for the match page (not used by the map renderer).
// Times are MM:SS with minutes allowed past 99; probabilities are percentages
// with one decimal; differences are percentage points; spatial values are
// unverified "game units"; missing values read "Unknown".
window.UI = (() => {
  const esc = (value) => String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const finite = (value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));

  function mmss(seconds) {
    if (!finite(seconds)) return "Unknown";
    const whole = Math.max(0, Math.round(Number(seconds)));
    return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
  }

  const span = (a, b) => (finite(a) && finite(b) ? `${mmss(a)}–${mmss(b)}` : "Unknown");
  const pct = (p) => (finite(p) ? `${(100 * Number(p)).toFixed(1)}%` : "Unknown");
  // A probability difference already in percentage points.
  const pp = (value) => (finite(value) ? `${Number(value) > 0 ? "+" : Number(value) < 0 ? "−" : ""}${Math.abs(Number(value)).toFixed(1)} pp` : "Unknown");
  const units = (n) => (finite(n) ? `${Math.round(Number(n)).toLocaleString()} game units` : "Unknown");
  const signedUnits = (n) => (finite(n) ? `${Number(n) > 0 ? "+" : ""}${Math.round(Number(n)).toLocaleString()} game units` : "Unknown");
  const num = (n, digits = 2) => (finite(n) ? Number(n).toFixed(digits) : "Unknown");
  const text = (value) => (value === null || value === undefined || value === "" ? "Unknown" : esc(value));

  // <details data-keep="key"> stays open across re-renders of its panel.
  const openKeys = new Set();
  document.addEventListener("toggle", (event) => {
    const el = event.target;
    if (!(el instanceof HTMLDetailsElement) || !el.dataset.keep) return;
    if (el.open) openKeys.add(el.dataset.keep);
    else openKeys.delete(el.dataset.keep);
  }, true);
  const keep = (key) => `data-keep="${esc(key)}"${openKeys.has(key) ? " open" : ""}`;

  // A team name with its map color as a marker. The color comes from the
  // match's teamId-to-color mapping (the map's colorFor); only hex colors are
  // used, and the name stays plain text so color is never the only identifier.
  const HEX = /^#[0-9a-f]{3,8}$/i;
  function teamTag(name, color) {
    const swatch = HEX.test(String(color || "")) ? color : "#888";
    return `<span class="team-ref"><span class="team-swatch" style="background:${swatch}" aria-hidden="true"></span>${text(name)}</span>`;
  }

  // teamId -> {name, color} from a squads list ({teamId, teamName, color}).
  function teamIndex(squads) {
    const index = new Map();
    for (const squad of squads || []) index.set(String(squad.teamId), { name: squad.teamName, color: squad.color });
    return (teamId, fallbackName) => {
      const found = index.get(String(teamId));
      return teamTag(found ? found.name : fallbackName ?? `Team ${teamId}`, found && found.color);
    };
  }

  return { esc, finite, mmss, span, pct, pp, units, signedUnits, num, text, keep, teamTag, teamIndex };
})();
