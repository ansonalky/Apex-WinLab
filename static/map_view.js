/* Map background and current ring. Transform matches Overstat mapCordX/Y/R.
   Zoom and pan share one camera. Telemetry coordinates are not modified. */
const ZOOM_MIN = 1;
const ZOOM_MAX = 8;
const PAN_MARGIN = 48;

const camera = { zoom: 1, panX: 0, panY: 0, mapId: null };
let lastView = null;
let layout = null;
let boundCanvas = null;
let drag = null;

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
  const { canvas, ctx, frame, selectedTeam, colorFor, ring, mapConfig } = view;
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
    const mine = String(player.teamId) === String(selectedTeam);
    ctx.globalAlpha = mine ? 1 : 0.45;
    ctx.fillStyle = colorFor(player.teamId);
    ctx.beginPath();
    ctx.arc(px, py, mine ? 7 : 4.5, 0, Math.PI * 2);
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
  canvas.addEventListener("pointercancel", onPointerUp);
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
  drag = { x: event.clientX, y: event.clientY, panX: camera.panX, panY: camera.panY };
  event.currentTarget.style.cursor = "grabbing";
  event.currentTarget.setPointerCapture(event.pointerId);
}

function onPointerMove(event) {
  if (!drag) return;
  camera.panX = drag.panX + (event.clientX - drag.x);
  camera.panY = drag.panY + (event.clientY - drag.y);
  if (layout) clampPan(layout.box, layout.width, layout.height);
  if (lastView) draw(lastView);
}

function onPointerUp(event) {
  if (!drag) return;
  drag = null;
  event.currentTarget.style.cursor = "grab";
}

function zoomBy(factor) {
  if (!layout || !supportedView()) return;
  zoomAt(layout.width / 2, layout.height / 2, camera.zoom * factor);
}

function zoomAt(cssX, cssY, nextZoom) {
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
  if (ring && ring.state === "closing_unrecorded") return `Ring ${ring.stage} · closing; the replay ends before this ring's end, so it is not drawn`;
  if (ring && ring.state === "unavailable") return "Ring: not available at this time";
  if (!ring || ring.state === "none" || !ring.location) return "Ring: none at this time";
  const countdown = ring.countdown_s == null ? "" : ` ${formatClock(ring.countdown_s)}`;
  const state = ring.state === "closing" ? "closing" : "waiting";
  return `Ring ${ring.stage} · ${state}${countdown}`;
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
