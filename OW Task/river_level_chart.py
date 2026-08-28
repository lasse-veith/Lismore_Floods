"""river_level_chart.py — self-contained animated HTML chart: river/gauge
level over time, built to move in exact lockstep with Output/flood_heat_map.html
(from heat_map.py) once the two pages are embedded side by side.

Reuses heat_map.py's own hydrograph loading, natural cubic spline
(natural_cubic_spline()), INTERP_STEP_HOURS, and EVENT_START_ISO directly
(imported, not re-derived) — the two pages share the exact same 127-frame,
2-hour-step timeline and the exact same interpolated water-level numbers,
computed once by the same function, so a viewer scrubbing/playing both at
the same frame index is always looking at the same simulated moment.
Importing heat_map.py only executes its module-level constants/functions;
its expensive DEM/raster work is gated behind `if __name__ == "__main__"`,
so this script never re-runs it.

Peak annotation: the REAL 12h gauge reading with the highest water level
(not the spline's own local maximum, which slightly overshoots the real
peak between anchor points) — same convention heat_map.py's own
river_level_over_time.png annotation already uses.

Output: Output/river_level_over_time.html — self-contained (all data
embedded inline as JSON, drawn on a <canvas>, no external libraries/CDN).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).resolve().parent  # OW Task (root)
OUTPUT_DIR = BASE_DIR / "Output"
OUTPUT_HTML = OUTPUT_DIR / "river_level_over_time.html"

sys.path.insert(0, str(BASE_DIR))
import heat_map as hm  # noqa: E402 — reuse hydrograph loading + cubic spline + timeline constants

# Must match heat_map.py's HTML_TEMPLATE's own PLAY_INTERVAL_MS exactly — the two pages are
# meant to be shown side by side and stay in lockstep whenever both are playing.
PLAY_INTERVAL_MS = 150

PEAK_LABEL_TEXT = "Flood peak: 14.4m on Feb 28th at about 3pm"


def build_timeline() -> dict:
    hydrograph = hm.load_hydrograph()
    hours_real = hydrograph["interval_hour"].to_numpy(dtype=float)
    levels_real = hydrograph["gauge_water_level_m_ahd"].to_numpy(dtype=float)

    hours_fine = np.arange(hours_real[0], hours_real[-1] + 1, hm.INTERP_STEP_HOURS)
    levels_fine = hm.natural_cubic_spline(hours_real, levels_real, hours_fine)
    levels_fine = np.clip(levels_fine, 0.0, levels_real.max())  # clipped to the real peak exactly — see heat_map.py
    is_reading = np.isin(hours_fine, hours_real)

    peak_idx = int(np.argmax(levels_real))
    return {
        "hours_fine": hours_fine,
        "levels_fine": levels_fine,
        "is_reading": is_reading,
        "peak_hour_real": float(hours_real[peak_idx]),
        "peak_level_real": float(levels_real[peak_idx]),
    }


HTML_TEMPLATE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Lismore 2022 Flood, River Level Over Time</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  @font-face {
    font-family: 'Saira';
    src: url('../UI%20Design/presentation/assets/fonts/Saira-Variable.ttf') format('truetype');
    font-weight: 100 900; font-stretch: 50% 125%; font-style: normal;
  }
  html, body { margin: 0; padding: 0; height: 100%; overflow: hidden; font-family: 'Saira', -apple-system, sans-serif; background: #f2f3f5; }
  #wrap { display: flex; flex-direction: column; height: 100%; box-sizing: border-box; padding: 20px 22px 16px; }
  h1 { color: #14161a; font-size: 15px; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
  .sub { color: #9aa1ac; font-size: 11.5px; margin-top: 2px; }
  #chartCard {
    background: #fff; border-radius: 18px; padding: 18px 20px 14px;
    box-shadow: 0 1px 2px rgba(16,20,27,0.04), 0 10px 24px -12px rgba(16,20,27,0.12);
    flex: 1; min-height: 0; display: flex; flex-direction: column; box-sizing: border-box;
  }
  .chart-head { display: flex; align-items: flex-start; justify-content: space-between; flex-shrink: 0; }
  .chart-legend { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: #6b7280; }
  .chart-legend .dot { width: 8px; height: 8px; border-radius: 50%; background: #3b6fd6; flex-shrink: 0; }
  .chart-legend .dot2 { width: 6px; height: 6px; border-radius: 50%; background: #14161a; flex-shrink: 0; margin-left: 10px; }
  #canvasWrap { position: relative; flex: 1; min-height: 0; margin-top: 8px; }
  canvas { width: 100%; height: 100%; display: block; }
  #statsRow { display: flex; justify-content: flex-start; gap: 26px; margin-top: 10px; flex-wrap: wrap; flex-shrink: 0; }
  .statBox .label { color: #9aa1ac; font-size: 10px; text-transform: uppercase; letter-spacing: 0.07em; font-family: 'Saira', monospace; }
  .statBox .value { font-size: 17px; font-weight: 600; margin-top: 2px; font-family: 'Saira', monospace; }
  #dateValue { color: #14161a; }
  #levelValue { color: #3b6fd6; }
  #controls { display: flex; align-items: center; gap: 12px; padding-top: 12px; flex-shrink: 0; }
  #playBtn {
    cursor: pointer; border: none; background: #3b6fd6; color: #fff; border-radius: 999px; font-family: 'Saira', sans-serif;
    padding: 8px 16px; font-size: 12.5px; font-weight: 600; flex-shrink: 0;
  }
  #playBtn:hover { background: #2f5fc0; }
  #slider { flex: 1; accent-color: #3b6fd6; }
  /* embedded mode: the parent page supplies its own heading + shared time
     control (see pages/flood.html), so this chart's own title/controls
     would just be a redundant duplicate — trimmed to the card + live stats. */
  .embedded #wrap { padding: 4px; }
  .embedded h1, .embedded .sub, .embedded #controls { display: none; }
  .embedded #chartCard { box-shadow: none; border-radius: 0; }
</style>
</head>
<body>
<div id="wrap">
  <h1>River Level Over Time</h1>
  <div class="sub">Wilsons River gauge level (m AHD), real hydrograph, synced with the flood map</div>
  <div id="chartCard">
    <div class="chart-head">
      <div></div>
      <div class="chart-legend"><span class="dot"></span> Water level<span class="dot2"></span> Real reading</div>
    </div>
    <div id="canvasWrap">
      <canvas id="chart"></canvas>
    </div>
    <div id="statsRow">
      <div class="statBox"><div class="label">Date / time</div><div class="value" id="dateValue">-</div></div>
      <div class="statBox"><div class="label">River level</div><div class="value" id="levelValue">0.0m AHD</div></div>
    </div>
    <div id="controls">
      <button id="playBtn">&#9654; Play</button>
      <input type="range" id="slider" min="0" max="__MAX_INDEX__" step="1" value="0">
    </div>
  </div>
</div>
<script>
  // Embedded mode (?embedded=1): a parent page supplies one shared time
  // control for this chart + the paired flood map — see pages/flood.html.
  if (new URLSearchParams(location.search).has('embedded')) {
    document.documentElement.classList.add('embedded');
  }
</script>
<script>
const HOURS = __HOURS_JSON__;
const LEVELS = __LEVELS_JSON__;
const IS_READING = __IS_READING_JSON__;
const PEAK_HOUR_REAL = __PEAK_HOUR_JSON__;
const PEAK_LEVEL_REAL = __PEAK_LEVEL_JSON__;
const PEAK_LABEL_TEXT = __PEAK_LABEL_JSON__;
const EVENT_START_ISO = "__EVENT_START_ISO__";
const PLAY_INTERVAL_MS = __PLAY_INTERVAL_MS__; // matches flood_heat_map.html's own PLAY_INTERVAL_MS — kept in lockstep
const N = HOURS.length;
const MAX_HOUR = HOURS[N - 1];
const MAX_Y = Math.max(...LEVELS) * 1.15;

function formatDateTime(hour) {
  const d = new Date(EVENT_START_ISO);
  d.setUTCHours(d.getUTCHours() + hour);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getUTCDate())}/${pad(d.getUTCMonth() + 1)}/${d.getUTCFullYear()} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

const canvas = document.getElementById('chart');
const canvasWrap = document.getElementById('canvasWrap');
const ctx = canvas.getContext('2d');
let W = 0, H = 0, DPR = Math.min(window.devicePixelRatio || 1, 2);
const PAD = { left: 46, right: 14, top: 16, bottom: 26 };
let lastFrame = 0;

function resizeCanvas() {
  W = canvasWrap.clientWidth;
  H = canvasWrap.clientHeight;
  canvas.width = W * DPR;
  canvas.height = H * DPR;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  render(lastFrame);
}

function xPix(hour) { return PAD.left + (hour / MAX_HOUR) * (W - PAD.left - PAD.right); }
function yPix(val) { return H - PAD.bottom - (val / MAX_Y) * (H - PAD.top - PAD.bottom); }

function drawAxes() {
  ctx.fillStyle = '#b6bbc4';
  ctx.font = '11px "Saira", sans-serif';
  ctx.lineWidth = 1;

  const yTicks = 4;
  for (let i = 0; i <= yTicks; i++) {
    const val = (MAX_Y / yTicks) * i;
    const y = yPix(val);
    ctx.strokeStyle = 'rgba(16,20,27,0.06)';
    ctx.beginPath();
    ctx.moveTo(PAD.left, y);
    ctx.lineTo(W - PAD.right, y);
    ctx.stroke();
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.fillText(val.toFixed(0) + 'm', PAD.left - 10, y);
  }

  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const xTickCount = 5;
  for (let i = 0; i <= xTickCount; i++) {
    const hour = Math.round((MAX_HOUR / xTickCount) * i / 12) * 12;
    const x = xPix(hour);
    ctx.fillText('+' + hour + 'h', x, H - PAD.bottom + 8);
  }
}

function pathForCurve(hours, values, upToIndex) {
  // Smoothed with quadratic-curve-through-midpoints (the standard canvas
  // line-smoothing trick) — applied on top of data that is ALREADY a
  // natural cubic spline fit (heat_map.py's natural_cubic_spline()), not
  // re-derived here — this only smooths the polyline connecting
  // already-spline-fitted points for crisp rendering at any resolution.
  const pts = [];
  for (let i = 0; i <= upToIndex; i++) pts.push({ x: xPix(hours[i]), y: yPix(values[i]) });
  const path = new Path2D();
  if (pts.length === 1) {
    path.moveTo(pts[0].x, pts[0].y);
    path.lineTo(pts[0].x, pts[0].y);
  } else {
    path.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length - 1; i++) {
      const xMid = (pts[i].x + pts[i + 1].x) / 2;
      const yMid = (pts[i].y + pts[i + 1].y) / 2;
      path.quadraticCurveTo(pts[i].x, pts[i].y, xMid, yMid);
    }
    const last = pts[pts.length - 1], secondLast = pts[pts.length - 2];
    path.quadraticCurveTo(secondLast.x, secondLast.y, last.x, last.y);
  }
  return { path, lastPoint: pts[pts.length - 1] };
}

function drawCurvedLine(hours, values, upToIndex, color) {
  const { path, lastPoint } = pathForCurve(hours, values, upToIndex);

  // soft gradient fill under the curve, fading to transparent
  const fillPath = new Path2D(path);
  const lastX = xPix(hours[upToIndex]);
  fillPath.lineTo(lastX, H - PAD.bottom);
  fillPath.lineTo(xPix(hours[0]), H - PAD.bottom);
  fillPath.closePath();
  const grad = ctx.createLinearGradient(0, PAD.top, 0, H - PAD.bottom);
  grad.addColorStop(0, 'rgba(59,111,214,0.22)');
  grad.addColorStop(1, 'rgba(59,111,214,0.0)');
  ctx.fillStyle = grad;
  ctx.fill(fillPath);

  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.stroke(path);

  if (upToIndex >= 0) {
    ctx.fillStyle = '#fff';
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.arc(lastPoint.x, lastPoint.y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  return lastPoint;
}

function drawRealReadingDots(upToIndex) {
  ctx.fillStyle = '#14161a';
  for (let i = 0; i <= upToIndex; i++) {
    if (!IS_READING[i]) continue;
    const x = xPix(HOURS[i]);
    const y = yPix(LEVELS[i]);
    ctx.beginPath();
    ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawPeakLabel(upToIndex) {
  if (HOURS[upToIndex] < PEAK_HOUR_REAL) return;
  const x = xPix(PEAK_HOUR_REAL);
  const y = yPix(PEAK_LEVEL_REAL);

  ctx.fillStyle = '#c98a1a';
  ctx.beginPath();
  ctx.arc(x, y, 4, 0, Math.PI * 2);
  ctx.fill();

  // Box sits to the LEFT of the peak, vertically centred on it — not above,
  // since the real peak sits close enough to the top of the chart that a
  // box placed above it can run past the canvas edge and get clipped.
  const boxH = 26;
  ctx.font = '600 11px "Saira", sans-serif';
  const textWidth = ctx.measureText(PEAK_LABEL_TEXT).width;
  const boxW = textWidth + 20;
  const gap = 14;

  let boxX = x - gap - boxW;
  if (boxX < PAD.left) boxX = x + gap; // not enough room on the left — fall back to the right instead
  let boxY = y - boxH / 2;
  boxY = Math.min(Math.max(boxY, PAD.top), H - PAD.bottom - boxH);

  ctx.strokeStyle = 'rgba(201,138,26,0.5)';
  ctx.lineWidth = 1.5;
  ctx.setLineDash([2, 3]);
  ctx.beginPath();
  ctx.moveTo(boxX < x ? boxX + boxW : boxX, y);
  ctx.lineTo(x - (boxX < x ? 4 : -4), y);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.fillStyle = '#fff';
  ctx.strokeStyle = 'rgba(16,20,27,0.08)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(boxX, boxY, boxW, boxH, 8);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = '#c98a1a';
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillText(PEAK_LABEL_TEXT, boxX + 10, boxY + 14);
}

/* "live" tooltip card that tracks the current point — the same visual
   language as a hover tooltip, but always shown at the current frame since
   position here is driven by the shared time slider, not the mouse. */
function drawLiveTooltip(point, hour, level) {
  const label = formatDateTime(hour);
  const valueText = level.toFixed(2) + 'm AHD';
  ctx.font = '600 11px "Saira", sans-serif';
  const w1 = ctx.measureText(label).width;
  ctx.font = '600 13px "Saira", monospace';
  const w2 = ctx.measureText(valueText).width;
  const boxW = Math.max(w1, w2) + 24;
  const boxH = 46;

  let boxX = point.x + 14;
  if (boxX + boxW > W - PAD.right) boxX = point.x - boxW - 14;
  let boxY = point.y - boxH - 12;
  if (boxY < PAD.top) boxY = point.y + 12;

  ctx.fillStyle = '#fff';
  ctx.strokeStyle = 'rgba(16,20,27,0.08)';
  ctx.lineWidth = 1;
  ctx.shadowColor = 'rgba(16,20,27,0.18)';
  ctx.shadowBlur = 14;
  ctx.shadowOffsetY = 4;
  ctx.beginPath();
  ctx.roundRect(boxX, boxY, boxW, boxH, 10);
  ctx.fill();
  ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
  ctx.stroke();

  ctx.textAlign = 'left'; ctx.textBaseline = 'top';
  ctx.font = '500 10.5px "Saira", sans-serif';
  ctx.fillStyle = '#9aa1ac';
  ctx.fillText(label, boxX + 12, boxY + 8);
  ctx.font = '600 13px "Saira", monospace';
  ctx.fillStyle = '#3b6fd6';
  ctx.fillText(valueText, boxX + 12, boxY + 22);
}

function updateStats(i) {
  const hour = HOURS[i];
  document.getElementById('dateValue').textContent = formatDateTime(hour);
  document.getElementById('levelValue').textContent = LEVELS[i].toFixed(2) + 'm AHD';
}

function render(i) {
  lastFrame = i;
  if (!W || !H) return;
  ctx.clearRect(0, 0, W, H);
  drawAxes();
  const point = drawCurvedLine(HOURS, LEVELS, i, '#3b6fd6');
  drawRealReadingDots(i);
  drawPeakLabel(i);
  drawLiveTooltip(point, HOURS[i], LEVELS[i]);
  updateStats(i);
}

window.addEventListener('resize', resizeCanvas);
if (window.ResizeObserver) new ResizeObserver(resizeCanvas).observe(canvasWrap);

const slider = document.getElementById('slider');
slider.addEventListener('input', (e) => render(+e.target.value));

let playing = false, playTimer = null;
const playBtn = document.getElementById('playBtn');

function startPlaying() {
  playing = true;
  playBtn.innerHTML = '&#10074;&#10074; Pause';
  const step = () => {
    let next = (+slider.value + 1) % N;
    slider.value = next;
    render(next);
    playTimer = setTimeout(step, PLAY_INTERVAL_MS);
  };
  playTimer = setTimeout(step, PLAY_INTERVAL_MS);
}
function stopPlaying() {
  playing = false;
  playBtn.innerHTML = '&#9654; Play';
  clearTimeout(playTimer);
}
playBtn.addEventListener('click', () => {
  if (playing) { stopPlaying(); return; }
  startPlaying();
});

resizeCanvas();

// --- shared time-control protocol (see pages/flood.html) ---
window.parent.postMessage({ type: 'ow-river-ready', maxIndex: N - 1 }, '*');
window.addEventListener('message', (e) => {
  if (e.data && e.data.type === 'ow-seek') render(e.data.frame);
});
</script>
</body>
</html>
"""


def write_html(timeline: dict) -> None:
    hours = [int(round(h)) for h in timeline["hours_fine"]]
    levels = [round(float(v), 3) for v in timeline["levels_fine"]]
    is_reading = [bool(v) for v in timeline["is_reading"]]

    html = HTML_TEMPLATE
    html = html.replace("__MAX_INDEX__", str(len(hours) - 1))
    html = html.replace("__HOURS_JSON__", json.dumps(hours))
    html = html.replace("__LEVELS_JSON__", json.dumps(levels))
    html = html.replace("__IS_READING_JSON__", json.dumps(is_reading))
    html = html.replace("__PEAK_HOUR_JSON__", json.dumps(timeline["peak_hour_real"]))
    html = html.replace("__PEAK_LEVEL_JSON__", json.dumps(timeline["peak_level_real"]))
    html = html.replace("__PEAK_LABEL_JSON__", json.dumps(PEAK_LABEL_TEXT))
    html = html.replace("__EVENT_START_ISO__", hm.EVENT_START_ISO)
    html = html.replace("__PLAY_INTERVAL_MS__", str(PLAY_INTERVAL_MS))

    OUTPUT_HTML.write_text(html)


def main() -> None:
    print(f"Loading {hm.HYDROGRAPH_CSV.name} and fitting the same natural cubic spline heat_map.py uses ...")
    timeline = build_timeline()
    print(f"  {len(timeline['hours_fine'])} frames, every {hm.INTERP_STEP_HOURS:.0f}h "
          f"(hour {timeline['hours_fine'][0]:.0f}-{timeline['hours_fine'][-1]:.0f})")
    print(f"  Real peak: {timeline['peak_level_real']:.2f}m AHD at hour {timeline['peak_hour_real']:.0f}")

    print(f"Writing {OUTPUT_HTML.name} ...")
    write_html(timeline)

    print(f"\nWrote {OUTPUT_HTML.relative_to(BASE_DIR)} ({OUTPUT_HTML.stat().st_size / 1e3:.0f} KB)")
    print(f"Play speed: {PLAY_INTERVAL_MS}ms/frame — matches flood_heat_map.html's own PLAY_INTERVAL_MS exactly.")


if __name__ == "__main__":
    main()
