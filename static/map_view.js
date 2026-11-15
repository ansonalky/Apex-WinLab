/* Map background and current ring. Transform matches Overstat mapCordX/Y/R.
   Zoom and pan share one camera. Telemetry coordinates are not modified. */
const ZOOM_MIN = 1;
const ZOOM_MAX = 8;
const PAN_MARGIN = 48;
const HIT_RADIUS = 12;
const CLICK_SLOP = 5;

const camera = { zoom: 1, panX: 0, panY: 0, mapId: null };
let lastView = null;
let layout = null;
let boundCanvas = null;
let drag = null;
let hits = [];

window.ApexMap = {
  zoomRange: { min: ZOOM_MIN, max: ZOOM_MAX },
  camera,
  draw,
  reset: resetCamera,
  screenToMapPixel,
};

let drawing = false;

function draw(view) {
  if (drawing) return;
  drawing = true;
  try {
    paint(view);
  } finally {
    drawing = false;
  }
}

function paint(view) {
  lastView = view;
  hits = [];
  const { canvas, ctx, frame, selectedTeam, colorFor, squadName, ring, mapConfig } = view;
  bind(canvas);
  const css = resizeBackingStore(canvas);
  ctx.setTransform(css.dpr, 0, 0, css.dpr, 0, 0);
  ctx.clearRect(0, 0, css.width, css.height);
  ctx.fillStyle = "#1b1f27";
  ctx.fillRect(0, 0, css.width, css.height);
  const supported = Boolean(mapConfig && mapConfig.supported && mapConfig.image);
  const mapId = mapConfig && (mapConfig.map_id || mapConfig.image);
  if (mapId !== camera.mapId) {
    camera.mapId = mapId || null;
    camera.zoom = 1;
    camera.panX = 0;
    camera.panY = 0;
  }
  if (!supported) {
    ctx.fillStyle = "#f0c674";
    ctx.font = "16px sans-serif";
    const message = (mapConfig && mapConfig.message) || "Map configuration is not loaded.";
    wrap(ctx, message, 24, 40, Math.max(120, css.width - 170), 22);
    const hud = document.querySelector("#ring-status");
    if (hud) hud.textContent = `Slider ${formatClock(frame.elapsed)} · map background unavailable, ring not drawn`;
    return;
  }
  const box = contain(css.width, css.height, 2048, 2048);
  clampPan(box, css.width, css.height);
  layout = { box, width: css.width, height: css.height };
  const mapLeft = box.x + camera.panX;
  const mapTop = box.y + camera.panY;
  const mapW = box.w * camera.zoom;
  const mapH = box.h * camera.zoom;
  const image = imageFor(mapConfig.image);
  if (image.complete && image.naturalWidth) {
    ctx.drawImage(image, mapLeft, mapTop, mapW, mapH);
  }
  const spec = mapConfig;
  const project = (x, y) => {
    const sx = mapCordX(x, spec);
    const sy = mapCordY(y, spec);
    return [mapLeft + (sx / 2048) * mapW, mapTop + (sy / 2048) * mapH];
  };
  const radius = (r) => (mapCordR(r, spec) / 2048) * mapW;
  const points = (frame.players || []).filter((player) => player.x !== null && player.y !== null);
  for (const player of points) {
    const [px, py] = project(player.x, player.y);
    hits.push({
      x: px,
      y: py,
      teamId: String(player.teamId),
      playerName: player.name || "Unnamed",
      squadName: typeof squadName === "function" ? squadName(player.teamId) : "",
    });
    const mine = String(player.teamId) === String(selectedTeam);
    ctx.globalAlpha = mine ? 1 : 0.45;
    ctx.fillStyle = colorFor(player.teamId);
    const size = mine ? 7 : 4.5;
    const heading = facingDirection(frame, player, project, px, py);
    ctx.beginPath();
    if (heading) {
      // An arrow along the recorded yaw, anchored at the player's position.
      const [ux, uy] = heading;
      const tipX = px + ux * size * 1.9;
      const tipY = py + uy * size * 1.9;
      const backX = px - ux * size * 0.9;
      const backY = py - uy * size * 0.9;
      ctx.moveTo(tipX, tipY);
      ctx.lineTo(backX - uy * size * 1.1, backY + ux * size * 1.1);
      ctx.lineTo(px - ux * size * 0.35, py - uy * size * 0.35);
      ctx.lineTo(backX + uy * size * 1.1, backY - ux * size * 1.1);
      ctx.closePath();
    } else {
      ctx.arc(px, py, size, 0, Math.PI * 2);
    }
    ctx.fill();
    if (mine && player.name) {
      ctx.globalAlpha = 1;
      ctx.font = "13px sans-serif";
      ctx.fillText(player.name, px + 8, py - 8);
    }
  }
  ctx.globalAlpha = 1;
  const location = ring && ring.location;
  if (location && location.r < 100000) {
    const [cx, cy] = project(location.x, location.y);
    const rr = radius(location.r);
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, css.width, css.height);
    ctx.clip();
    ctx.fillStyle = "rgba(0, 0, 0, 0.45)";
    ctx.beginPath();
    ctx.rect(mapLeft, mapTop, mapW, mapH);
    ctx.arc(cx, cy, rr, 0, Math.PI * 2, true);
    ctx.fill("evenodd");
    ctx.restore();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(cx, cy, rr, 0, Math.PI * 2);
    ctx.stroke();
  }
  // Reference points with known coordinates (decision review), through the
  // same camera projection. A point only: no route is drawn, because none is known.
  for (const marker of view.markers || []) {
    if (!Number.isFinite(marker.x) || !Number.isFinite(marker.y)) continue;
    const [mx, my] = project(marker.x, marker.y);
    ctx.save();
    ctx.strokeStyle = marker.strong ? "#d4b8f0" : "#9b8bb0";
    ctx.lineWidth = marker.strong ? 2.5 : 1.5;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.arc(mx, my, 11, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#d4b8f0";
    ctx.font = "12px sans-serif";
    ctx.fillText(marker.label || "", mx + 14, my + 22);
    ctx.restore();
  }
  const hud = document.querySelector("#ring-status");
  if (hud) hud.textContent = `Map and ring at slider ${formatClock(frame.elapsed)} · ${ringText(ring)} · ${camera.zoom.toFixed(1)}×`;
}

function bind(canvas) {
  if (boundCanvas === canvas) return;
  boundCanvas = canvas;
  canvas.style.touchAction = "none";
  canvas.style.cursor = "grab";
  canvas.style.aspectRatio = "1 / 1";
  ensureControls(canvas);
  canvas.addEventListener("wheel", onWheel, { passive: false });
  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("pointercancel", (event) => { drag = null; hideHover(event.currentTarget); });
  canvas.addEventListener("pointerleave", (event) => { if (!drag) hideHover(event.currentTarget); });
  const host = canvas.parentElement;
  if (host && window.ResizeObserver) {
    new ResizeObserver(() => { if (lastView) draw(lastView); }).observe(host);
  }
}

function ensureControls(canvas) {
  const host = canvas.parentElement;
  if (!host || host.querySelector("[data-map-zoom]")) return;
  if (getComputedStyle(host).position === "static") host.style.position = "relative";
  const bar = document.createElement("div");
  bar.dataset.mapZoom = "1";
  Object.assign(bar.style, {
    position: "absolute", top: "8px", right: "8px", display: "flex", gap: "4px", zIndex: "2",
  });
  const actions = [
    ["+", "Zoom in", () => zoomBy(1.25)],
    ["−", "Zoom out", () => zoomBy(0.8)],
    ["Reset", "Reset map view", resetCamera],
  ];
  for (const [label, name, action] of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.setAttribute("aria-label", name);
    Object.assign(button.style, {
      background: "#1b1f27", color: "#e7e9ee", border: "1px solid #2c3340",
      borderRadius: "6px", padding: "0.2rem 0.55rem", cursor: "pointer", font: "inherit",
    });
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      action();
    });
    bar.append(button);
  }
  host.append(bar);
}

function onWheel(event) {
  if (!layout || !supportedView()) return;
  event.preventDefault();
  const point = localPoint(event.currentTarget, event);
  const scale = event.deltaMode === 1 ? 0.04 : 0.0016;
  zoomAt(point.x, point.y, camera.zoom * Math.exp(-event.deltaY * scale));
}

function onPointerDown(event) {
  if (event.button !== 0 || !layout || !supportedView()) return;
  drag = { x: event.clientX, y: event.clientY, panX: camera.panX, panY: camera.panY, moved: false };
  event.currentTarget.setPointerCapture(event.pointerId);
}

function onPointerMove(event) {
  const point = localPoint(event.currentTarget, event);
  if (!drag) {
    showHover(event.currentTarget, point, event.clientX, event.clientY);
    return;
  }
  const dx = event.clientX - drag.x;
  const dy = event.clientY - drag.y;
  if (!drag.moved && Math.hypot(dx, dy) < CLICK_SLOP) return;
  drag.moved = true;
  hideHover(event.currentTarget);
  hideChoices(event.currentTarget);
  event.currentTarget.style.cursor = "grabbing";
  camera.panX = drag.panX + dx;
  camera.panY = drag.panY + dy;
  if (layout) clampPan(layout.box, layout.width, layout.height);
  if (lastView) draw(lastView);
}

function onPointerUp(event) {
  if (!drag) return;
  const wasClick = !drag.moved;
  drag = null;
  const point = localPoint(event.currentTarget, event);
  if (!wasClick) {
    event.currentTarget.style.cursor = "grab";
    return;
  }
  const found = squadsAt(point.x, point.y);
  if (found.length === 1 && typeof window.ApexMap.onSelect === "function") {
    hideChoices(event.currentTarget);
    window.ApexMap.onSelect(found[0].teamId);
  } else if (found.length > 1) {
    showChoices(event.currentTarget, event.clientX, event.clientY, found);
  } else {
    hideChoices(event.currentTarget);
  }
  showHover(event.currentTarget, point, event.clientX, event.clientY);
}

function zoomBy(factor) {
  if (!layout || !supportedView()) return;
  zoomAt(layout.width / 2, layout.height / 2, camera.zoom * factor);
}

function squadsAt(cssX, cssY) {
  const grouped = new Map();
  for (const hit of hits) {
    const dist = Math.hypot(hit.x - cssX, hit.y - cssY);
    if (dist > HIT_RADIUS) continue;
    const prev = grouped.get(hit.teamId);
    if (!prev || dist < prev.dist) grouped.set(hit.teamId, { ...hit, dist });
  }
  return [...grouped.values()].sort((a, b) => a.dist - b.dist);
}

function showHover(canvas, point, clientX, clientY) {
  const host = canvas.parentElement;
  const found = squadsAt(point.x, point.y);
  if (!host || !found.length) {
    hideHover(canvas);
    if (!drag) canvas.style.cursor = "grab";
    return;
  }
  canvas.style.cursor = "pointer";
  let tip = host.querySelector("[data-map-tip]");
  if (!tip) {
    tip = document.createElement("div");
    tip.dataset.mapTip = "1";
    tip.style.cssText = "position:absolute;z-index:3;pointer-events:none;background:#121418;color:#e7e9ee;border:1px solid #2c3340;border-radius:6px;padding:0.2rem 0.45rem;font:12px sans-serif;white-space:nowrap;";
    host.append(tip);
  }
  const nearest = found[0];
  const extra = found.length > 1 ? ` · ${found.length} squads` : "";
  tip.textContent = `${nearest.playerName} · ${nearest.squadName || nearest.teamId}${extra}`;
  const rect = host.getBoundingClientRect();
  tip.style.left = `${clientX - rect.left + 12}px`;
  tip.style.top = `${clientY - rect.top + 14}px`;
}

function hideHover(canvas) {
  const tip = canvas.parentElement && canvas.parentElement.querySelector("[data-map-tip]");
  if (tip) tip.remove();
}

function showChoices(canvas, clientX, clientY, options) {
  const host = canvas.parentElement;
  if (!host) return;
  hideChoices(canvas);
  const menu = document.createElement("div");
  menu.dataset.mapChoice = "1";
  menu.setAttribute("role", "menu");
  menu.style.cssText = "position:absolute;z-index:4;background:#1b1f27;border:1px solid #2c3340;border-radius:6px;display:flex;flex-direction:column;min-width:8rem;padding:0.2rem;";
  const rect = host.getBoundingClientRect();
  menu.style.left = `${clientX - rect.left}px`;
  menu.style.top = `${clientY - rect.top}px`;
  for (const option of options) {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "menuitem");
    button.textContent = option.squadName || option.teamId;
    button.style.cssText = "font:inherit;color:#e7e9ee;background:transparent;border:0;text-align:left;padding:0.25rem 0.45rem;cursor:pointer;";
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      hideChoices(canvas);
      if (typeof window.ApexMap.onSelect === "function") window.ApexMap.onSelect(option.teamId);
    });
    menu.append(button);
  }
  host.append(menu);
}

function hideChoices(canvas) {
  const menu = canvas.parentElement && canvas.parentElement.querySelector("[data-map-choice]");
  if (menu) menu.remove();
}

function zoomAt(cssX, cssY, nextZoom) {
  if (boundCanvas) hideChoices(boundCanvas);
  const box = layout.box;
  const previous = camera.zoom;
  const zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, nextZoom));
  const fx = (cssX - box.x - camera.panX) / (box.w * previous);
  const fy = (cssY - box.y - camera.panY) / (box.h * previous);
  camera.zoom = zoom;
  camera.panX = cssX - box.x - fx * box.w * zoom;
  camera.panY = cssY - box.y - fy * box.h * zoom;
  clampPan(box, layout.width, layout.height);
  if (lastView) draw(lastView);
}

function resetCamera() {
  camera.zoom = 1;
  camera.panX = 0;
  camera.panY = 0;
  if (lastView) draw(lastView);
}

function supportedView() {
  const config = lastView && lastView.mapConfig;
  return Boolean(config && config.supported && config.image);
}

function clampPan(box, width, height) {
  const mapW = box.w * camera.zoom;
  const mapH = box.h * camera.zoom;
  const minX = PAN_MARGIN - mapW - box.x;
  const maxX = width - PAN_MARGIN - box.x;
  const minY = PAN_MARGIN - mapH - box.y;
  const maxY = height - PAN_MARGIN - box.y;
  camera.panX = minX > maxX ? (width - mapW) / 2 - box.x : Math.min(maxX, Math.max(minX, camera.panX));
  camera.panY = minY > maxY ? (height - mapH) / 2 - box.y : Math.min(maxY, Math.max(minY, camera.panY));
}

function screenToMapPixel(cssX, cssY) {
  if (!layout) return null;
  const mapW = layout.box.w * camera.zoom;
  const mapH = layout.box.h * camera.zoom;
  return [
    ((cssX - layout.box.x - camera.panX) / mapW) * 2048,
    ((cssY - layout.box.y - camera.panY) / mapH) * 2048,
  ];
}

function resizeBackingStore(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, rect.width);
  const height = Math.max(1, rect.height);
  const pixelsWide = Math.round(width * dpr);
  const pixelsHigh = Math.round(height * dpr);
  if (canvas.width !== pixelsWide || canvas.height !== pixelsHigh) {
    canvas.width = pixelsWide;
    canvas.height = pixelsHigh;
  }
  return { width, height, dpr };
}

function localPoint(canvas, event) {
  const rect = canvas.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

const images = {};

function imageFor(src) {
  if (!images[src]) {
    const image = new Image();
    image.src = src;
    image.onload = () => window.dispatchEvent(new Event("map-image"));
    images[src] = image;
  }
  return images[src];
}

function contain(width, height, imageW, imageH) {
  const scale = Math.min(width / imageW, height / imageH);
  const w = imageW * scale;
  const h = imageH * scale;
  return { x: (width - w) / 2, y: (height - h) / 2, w, h };
}

function mapCordX(value, spec) {
  value = parseInt(value, 10);
  return 1024 * ((value + spec.ox) / spec.x) + 1024;
}

function mapCordY(value, spec) {
  value = parseInt(value, 10);
  return 1024 * ((-value - spec.oy) / spec.y) + 1024;
}

function mapCordR(value, spec) {
  return 1024 * (value / spec.x);
}

function ringText(ring) {
  // ring.label is the shared server-side label (ring N = N-th closure); ring.stage stays the raw index.
  if (ring && ring.state === "closing_unrecorded") return `${ring.label}; the replay ends before this ring's end, so it is not drawn`;
  if (ring && ring.state === "unavailable") return "Ring: not available at this time";
  if (!ring || ring.state === "none" || !ring.location) return "Ring: none at this time";
  const countdown = ring.countdown_s == null ? "" : ` ${formatClock(ring.countdown_s)}`;
  if (!countdown) return ring.label;
  return ring.state === "closing" ? `${ring.label} ·${countdown} left` : `${ring.label} · closes in${countdown}`;
}

// Screen direction of a player's recorded yaw, or null for a plain dot.
// Yaw is angles.y: degrees, 0 along replay +x, counter-clockwise (verified on
// three replays; see docs/map-rendering.md). The direction goes through the same
// project() as the marker, so map flips, zoom and pan are applied to it. A
// facing written more than frame.facing.fresh_s seconds ago is stale and shows
// a dot; so does an eliminated player. Alive and downed players can show an
// arrow. The arrow is recorded yaw, not verified visibility. No viewing cone
// is drawn: that would need a field of view and map geometry, which this
// data does not have. Facing is never inferred from movement.
function facingDirection(frame, player, project, px, py) {
  const facing = frame.facing && frame.facing.players && frame.facing.players[player.id];
  const fresh = frame.facing && frame.facing.fresh_s;
  if (!facing || typeof facing.yaw !== "number" || typeof facing.age_s !== "number" || typeof fresh !== "number") return null;
  if (facing.age_s > fresh || (player.status !== "alive" && player.status !== "downed")) return null;
  const radians = (facing.yaw * Math.PI) / 180;
  const [qx, qy] = project(player.x + Math.cos(radians) * 1000, player.y + Math.sin(radians) * 1000);
  const dx = qx - px;
  const dy = qy - py;
  const length = Math.hypot(dx, dy);
  return length > 0 ? [dx / length, dy / length] : null;
}

function formatClock(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

function wrap(ctx, text, x, y, max, line) {
  const words = text.split(" ");
  let row = "";
  for (const word of words) {
    const trial = row ? `${row} ${word}` : word;
    if (ctx.measureText(trial).width > max) {
      ctx.fillText(row, x, y);
      y += line;
      row = word;
    } else row = trial;
  }
  if (row) ctx.fillText(row, x, y);
}
