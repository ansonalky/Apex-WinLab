/* Map background and current ring. Transform matches Overstat mapCordX/Y/R. */
window.ApexMap = {
  draw(view) {
    const { canvas, ctx, frame, selectedTeam, colorFor, ring, mapConfig } = view;
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#1b1f27";
    ctx.fillRect(0, 0, width, height);
    if (!mapConfig || !mapConfig.supported || !mapConfig.image) {
      ctx.fillStyle = "#f0c674";
      ctx.font = "16px sans-serif";
      const message = (mapConfig && mapConfig.message) || "Map configuration is not loaded.";
      wrap(ctx, message, 24, 40, width - 48, 22);
      const hud = document.querySelector("#ring-status");
      if (hud) hud.textContent = `Slider ${formatClock(frame.elapsed)} · map background unavailable, ring not drawn`;
      return;
    }
    const box = contain(width, height, 2048, 2048);
    const image = imageFor(mapConfig.image);
    if (image.complete && image.naturalWidth) {
      ctx.drawImage(image, box.x, box.y, box.w, box.h);
    }
    const spec = mapConfig;
    const project = (x, y) => {
      const sx = mapCordX(x, spec);
      const sy = mapCordY(y, spec);
      return [box.x + (sx / 2048) * box.w, box.y + (sy / 2048) * box.h];
    };
    const radius = (r) => (mapCordR(r, spec) / 2048) * box.w;
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
      ctx.fillStyle = "rgba(0, 0, 0, 0.45)";
      ctx.beginPath();
      ctx.rect(box.x, box.y, box.w, box.h);
      ctx.arc(cx, cy, rr, 0, Math.PI * 2, true);
      ctx.fill("evenodd");
      ctx.restore();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(cx, cy, rr, 0, Math.PI * 2);
      ctx.stroke();
    }
    // Reference points with known coordinates (decision review). A point only:
    // no route is drawn, because no route is known.
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
      ctx.fillText(marker.label, mx + 14, my + 22);
      ctx.restore();
    }
    const hud = document.querySelector("#ring-status");
    if (hud) hud.textContent = `Map and ring at slider ${formatClock(frame.elapsed)} · ${ringText(ring)}`;
  },
};

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
