// Tactical panel: explanatory measurements for the selected squad at the
// displayed second. Not a model input; it never changes a probability.
// Loaded after the frame is drawn so it does not slow seeking. A response for
// an older second or squad is dropped.
window.Tactical = (() => {
  const box = document.querySelector("#tactical");
  let token = 0;

  const esc = (value) => String(value).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  // Shared formats: MM:SS, and distances in unverified game units.
  const clock = (s) => window.UI.mmss(s);
  const units = (n) => window.UI.units(n);
  const signed = (n) => window.UI.signedUnits(n);

  function teamName(squads, id) {
    const squad = (squads || []).find((item) => String(item.teamId) === String(id));
    return squad ? squad.teamName : `team ${id}`;
  }

  function ringPart(ring) {
    if (!ring.known) {
      const phase = { not_revealed: "no ring has started closing yet", closing_target_unknown: "closing, end not recorded" }[ring.phase] || ring.phase || "Unknown";
      return `<p><strong>Ring:</strong> ${esc(phase)}${ring.label ? ` (${esc(ring.label)})` : ""}. Boundary distance not measured: ${esc(ring.reason || "geometry not revealed")}.</p>`;
    }
    if (ring.players_measured == null) {
      return `<p><strong>Ring:</strong> ${esc(ring.label)}${ring.phase === "closing" ? `, ${ring.closing_remaining_s}s left` : ""}. No fresh member position to measure.</p>`;
    }
    const margin = ring.outside_players
      ? `<span class="unavailable">${ring.outside_players} of ${ring.players_measured} outside</span>`
      : `all ${ring.players_measured} inside; nearest member ${units(ring.nearest_inside_margin)} from the edge`;
    return `<p><strong>Ring:</strong> ${esc(ring.label)}${ring.phase === "closing" ? `, ${ring.closing_remaining_s}s left` : ""}, radius ${units(ring.radius)}. ${margin}. Boundary distance per member: ${ring.boundary_distance.map(signed).join(", ")} (positive is outside).</p>`;
  }

  function firePart(fire, squads) {
    if (!fire.damage_events) return `<p><strong>Observed fire, last ${fire.window_s}s:</strong> no damage events to this squad.</p>`;
    const sources = [
      fire.from_opponents ? `${fire.from_opponents} from opponents` : "",
      fire.from_ring ? `${fire.from_ring} from the ring` : "",
      fire.from_other_environment ? `${fire.from_other_environment} other environment` : "",
      fire.from_own_squad ? `${fire.from_own_squad} from own squad` : "",
    ].filter(Boolean).join(", ");
    const rows = fire.attackers.map((a) => `<li>${esc(a.team_name || teamName(squads, a.team))}: ${a.damage} damage in ${a.hits} hit${a.hits === 1 ? "" : "s"}, from ${a.sector || "an unknown direction"}${a.bearing_deg != null ? ` (${a.bearing_deg}°)` : ""}, last ${a.seconds_ago}s ago</li>`).join("");
    return `<p><strong>Observed fire, last ${fire.window_s}s:</strong> ${fire.damage_taken} damage in ${fire.damage_events} events (${sources}); ${fire.attacking_squads} attacking squad${fire.attacking_squads === 1 ? "" : "s"}.</p>${rows ? `<ul>${rows}</ul>` : ""}`;
  }

  function pressurePart(pressure, squads) {
    const sectors = Object.entries(pressure.sectors).filter(([, n]) => n).map(([s, n]) => `${s}${n > 1 ? ` ×${n}` : ""}`).join(", ");
    const rows = pressure.opponents.filter((o) => o.band !== "far").slice(0, 5).map((o) => `<li>${esc(teamName(squads, o.team))}: ${units(o.distance)} ${o.sector ? `to the ${o.sector}` : "(too close for a bearing)"}, height ${signed(o.dz)}</li>`).join("");
    return `<p><strong>Opposing squads nearby:</strong> ${pressure.near_squads} within ${units(pressure.near_units)}, ${pressure.medium_squads} more within ${units(pressure.medium_units)}${sectors ? `; directions ${sectors}` : ""}${pressure.too_close_for_bearing ? `; ${pressure.too_close_for_bearing} closer than ${units(pressure.min_bearing_units)}` : ""}.</p>${rows ? `<ul>${rows}</ul>` : ""}`;
  }

  function elevationPart(elevation) {
    if (!elevation.available) return "<p><strong>Relative height:</strong> no fresh member height.</p>";
    if (!elevation.opponents_compared) return `<p><strong>Relative height:</strong> no opposing squad with a fresh height within ${units(elevation.comparison_distance_units)}.</p>`;
    return `<p><strong>Relative height</strong> vs ${elevation.opponents_compared} squad${elevation.opponents_compared === 1 ? "" : "s"} within ${units(elevation.comparison_distance_units)}: higher than ${elevation.higher_than}, lower than ${elevation.lower_than}, mean ${signed(elevation.mean_dz)}. Height only, not high-ground control.</p>`;
  }

  function qualityPart(quality) {
    const members = quality.members.map((m) => `${esc(m.name)} (${esc(m.status)}${m.pos_age_s != null ? `, position ${m.pos_age_s}s old` : ""}${m.note ? `: ${esc(m.note)}` : ""})`).join("; ");
    return `<p class="when">Data: ${quality.standing_with_fresh_position} of ${quality.standing} standing members have a position updated within ${quality.fresh_gap_s}s. ${members}. Opponents: ${quality.opposing_standing_with_fresh_position} standing players used, ${quality.opposing_standing_stale_excluded} with stale positions excluded.</p>`;
  }

  async function load(game, t, team, squads) {
    const mine = ++token;
    if (!box) return;
    box.innerHTML = '<p class="when">Loading…</p>';
    let data;
    try {
      const response = await fetch(`/api/matches/${game}/tactical?t=${t}&team=${encodeURIComponent(team)}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      data = await response.json();
    } catch (error) {
      if (mine === token) box.innerHTML = '<p class="unavailable">Tactical measurements could not be loaded.</p>';
      return;
    }
    if (mine !== token) return;
    if (!data.available) {
      box.innerHTML = `<p class="unavailable">Not available: ${esc(data.reason || "")}</p>`;
      return;
    }
    box.innerHTML = `<p class="when">Measured at ${clock(data.elapsed)} for ${esc(teamName(squads, data.team))}. Not used by the model; the estimate does not include it.</p>
      ${ringPart(data.ring)}
      ${firePart(data.fire, squads)}
      ${pressurePart(data.pressure, squads)}
      ${elevationPart(data.elevation)}
      ${qualityPart(data.quality)}
      <details ${window.UI.keep("tactical-limits")}><summary>What these measurements do not show</summary><ul>${data.limits.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></details>`;
  }

  return { load };
})();
