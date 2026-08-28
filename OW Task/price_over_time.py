"""price_over_time.py — self-contained animated HTML chart: cumulative repair
cost over time, static estimate vs. the true (repair-model-engine) actual
cost, sampled every 7 days and drawn in with a one-shot reveal animation from
day 0 up to the final real totals on load (same "day 0 = flood peak" epoch
as the rest of the Repair Model stage).

Reuses cost_comparison.py's own data-loading/computation functions
(load_static_timeline, load_repaired_properties, compute_actual_cumulative)
rather than re-deriving the series — same numbers as cost_comparison.png,
just resampled to 7-day steps and rendered as an animated page instead of a
static PNG. No new randomness, no new columns.

Output: Output/price_over_time.html — self-contained (all data embedded
inline as JSON, chart drawn on a <canvas>, no external libraries/CDN, no
server needed).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent  # OW Task (root)
OUTPUT_DIR = BASE_DIR / "Output"
OUTPUT_HTML = OUTPUT_DIR / "price_over_time.html"

sys.path.insert(0, str(BASE_DIR))
import cost_comparison as cc  # noqa: E402 — reuse its data loading/computation

INTERVAL_DAYS = 7


def resample_every_n_days(days: list[int], cumulative: list[float], interval: int) -> tuple[list[int], list[float]]:
    """Every `interval` days, plus the final day exactly (so the animation's
    last frame always shows the true final total, not a rounded-down one)."""
    max_day = days[-1]
    sample_days = sorted(set(range(0, max_day + 1, interval)) | {max_day})
    by_day = dict(zip(days, cumulative))
    return sample_days, [by_day[d] for d in sample_days]


HTML_TEMPLATE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Lismore 2022 Flood, Repair Cost Over Time</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  @font-face {
    font-family: 'Saira';
    src: url('../UI%20Design/presentation/assets/fonts/Saira-Variable.ttf') format('truetype');
    font-weight: 100 900; font-stretch: 50% 125%; font-style: normal;
  }
  html, body { margin: 0; padding: 0; height: 100%; overflow: hidden; font-family: 'Saira', -apple-system, sans-serif; background: #f2f3f5; }
  :root { --static-color: #3b6fd6; --actual-color: #d64545; }
  #wrap { display: flex; flex-direction: column; height: 100%; box-sizing: border-box; padding: 20px 24px 18px; }
  h1 { color: #14161a; font-size: 15px; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
  .sub { color: #9aa1ac; font-size: 11.5px; margin-top: 2px; }
  /* embedded mode: the host card supplies its own heading, so this chart's
     own title/subtitle would be a redundant duplicate. */
  .embedded #wrap { padding: 6px; }
  .embedded h1, .embedded .sub { display: none; }
  #chartCard {
    background: #fff; border-radius: 18px; padding: 18px 22px 16px;
    box-shadow: 0 1px 2px rgba(16,20,27,0.04), 0 10px 24px -12px rgba(16,20,27,0.12);
    flex: 1; min-height: 0; display: flex; flex-direction: column; box-sizing: border-box;
  }
  .embedded #chartCard { box-shadow: none; border-radius: 0; padding: 4px; }
  .chart-head { display: flex; align-items: flex-start; justify-content: space-between; flex-shrink: 0; }
  .chart-legend { display: flex; align-items: center; gap: 16px; font-size: 11.5px; color: #6b7280; flex-wrap: wrap; }
  .legendItem { display: flex; align-items: center; gap: 6px; }
  .swatch { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
  #canvasWrap { position: relative; flex: 1; min-height: 0; margin-top: 8px; }
  canvas { width: 100%; height: 100%; display: block; }
  #statsRow { display: flex; justify-content: flex-start; gap: 30px; margin-top: 12px; flex-wrap: wrap; flex-shrink: 0; }
  .statBox .label { color: #9aa1ac; font-size: 10px; text-transform: uppercase; letter-spacing: 0.07em; font-family: 'Saira', monospace; }
  .statBox .value { font-family: 'Saira', monospace; font-size: 18px; font-weight: 600; margin-top: 2px; }
  #dayValue { color: #14161a; }
  #staticValue { color: var(--static-color); }
  #actualValue { color: var(--actual-color); }
  #diffValue { color: #c98a1a; }
</style>
</head>
<body>
<script>
  const __params = new URLSearchParams(location.search);
  if (__params.has('embedded')) {
    document.documentElement.classList.add('embedded');
  }
  if (__params.get('colors') === 'swap') {
    document.documentElement.style.setProperty('--static-color', '#d64545');
    document.documentElement.style.setProperty('--actual-color', '#3b6fd6');
  }
</script>
<div id="wrap">
  <h1>Repair Cost Over Time</h1>
  <div class="sub">Static estimate vs. true (repair-model-engine) actual cost, since the flood peak</div>
  <div id="chartCard">
    <div class="chart-head">
      <div></div>
      <div class="chart-legend">
        <span class="legendItem"><span class="swatch" style="background:var(--static-color)"></span> Static estimate</span>
        <span class="legendItem"><span class="swatch" style="background:var(--actual-color)"></span> Actual cost</span>
      </div>
    </div>
    <div id="canvasWrap">
      <canvas id="chart"></canvas>
    </div>
    <div id="statsRow">
      <div class="statBox"><div class="label">Day since flood peak</div><div class="value" id="dayValue">0</div></div>
      <div class="statBox"><div class="label">Static cumulative</div><div class="value" id="staticValue">$0M</div></div>
      <div class="statBox"><div class="label">Actual cumulative</div><div class="value" id="actualValue">$0M</div></div>
      <div class="statBox"><div class="label">Gap (actual - static)</div><div class="value" id="diffValue">$0M</div></div>
    </div>
  </div>
</div>
<script>
const DAYS = __DAYS_JSON__;
const STATIC_CUM = __STATIC_JSON__;
const ACTUAL_CUM = __ACTUAL_JSON__;
const N = DAYS.length;
const COLORS_SWAPPED = new URLSearchParams(location.search).get('colors') === 'swap';
const STATIC_COLOR = COLORS_SWAPPED ? '#d64545' : '#3b6fd6';
const ACTUAL_COLOR = COLORS_SWAPPED ? '#3b6fd6' : '#d64545';
const STATIC_FILL = COLORS_SWAPPED ? 'rgba(214,69,69,1)' : 'rgba(59,111,214,1)';
const ACTUAL_FILL = COLORS_SWAPPED ? 'rgba(59,111,214,1)' : 'rgba(214,69,69,1)';
const MAX_DAY = DAYS[N - 1];
const MAX_Y = Math.max(STATIC_CUM[N - 1], ACTUAL_CUM[N - 1]) * 1.1;

function fmtMoney(v) { return '$' + (v / 1e6).toFixed(1) + 'M'; }

function valueAt(arr, idx) {
  const i0 = Math.floor(idx), i1 = Math.min(N - 1, i0 + 1);
  const frac = idx - i0;
  return arr[i0] + (arr[i1] - arr[i0]) * frac;
}

const canvas = document.getElementById('chart');
const canvasWrap = document.getElementById('canvasWrap');
const ctx = canvas.getContext('2d');
let W = 0, H = 0, DPR = Math.min(window.devicePixelRatio || 1, 2);
const PAD = { left: 56, right: 16, top: 16, bottom: 26 };
let lastIdx = 0;

function resizeCanvas() {
  W = canvasWrap.clientWidth;
  H = canvasWrap.clientHeight;
  canvas.width = W * DPR;
  canvas.height = H * DPR;
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  render(lastIdx);
}

function xPix(day) { return PAD.left + (day / MAX_DAY) * (W - PAD.left - PAD.right); }
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
    ctx.fillText(fmtMoney(val), PAD.left - 10, y);
  }

  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const xTickCount = 6;
  for (let i = 0; i <= xTickCount; i++) {
    const day = Math.round((MAX_DAY / xTickCount) * i);
    ctx.fillText('d' + day, xPix(day), H - PAD.bottom + 8);
  }
}

/** Builds a smooth quadratic-through-midpoints path up to a possibly
 * FRACTIONAL index (idx), so the reveal animation draws continuously
 * instead of jumping between the underlying 7-day sample points. */
function pathFor(days, values, idxFloat) {
  const i0 = Math.floor(idxFloat);
  const pts = [];
  for (let i = 0; i <= i0; i++) pts.push({ x: xPix(days[i]), y: yPix(values[i]) });
  if (idxFloat > i0 && i0 < N - 1) {
    const day = days[i0] + (days[i0 + 1] - days[i0]) * (idxFloat - i0);
    const val = valueAt(values, idxFloat);
    pts.push({ x: xPix(day), y: yPix(val) });
  }
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

function drawLine(days, values, idxFloat, color, fill) {
  const { path, lastPoint } = pathFor(days, values, idxFloat);

  if (fill) {
    const fillPath = new Path2D(path);
    fillPath.lineTo(lastPoint.x, H - PAD.bottom);
    fillPath.lineTo(xPix(days[0]), H - PAD.bottom);
    fillPath.closePath();
    const grad = ctx.createLinearGradient(0, PAD.top, 0, H - PAD.bottom);
    grad.addColorStop(0, fill);
    grad.addColorStop(1, fill.replace(/[\d.]+\)$/, '0)'));
    ctx.fillStyle = grad;
    ctx.fill(fillPath);
  }

  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.stroke(path);
}

/** Draws the marker dot at markerIdx, independent of how much of the line
 * is currently drawn — once the reveal finishes, the full line stays drawn
 * and this dot is what actually moves while the viewer drags/hovers. */
function drawMarker(days, values, markerIdx, color) {
  const point = { x: xPix(valueAt(days, markerIdx)), y: yPix(valueAt(values, markerIdx)) };
  ctx.fillStyle = '#fff';
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.arc(point.x, point.y, 5, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  return point;
}

function drawLiveTooltip(staticPoint, actualPoint, day, staticVal, actualVal) {
  const rows = [
    { label: 'Static', value: fmtMoney(staticVal), color: STATIC_COLOR },
    { label: 'Actual', value: fmtMoney(actualVal), color: ACTUAL_COLOR },
  ];
  ctx.font = '600 13px "Saira", monospace';
  const valW = Math.max(...rows.map(r => ctx.measureText(r.value).width));
  ctx.font = '500 11px "Saira", sans-serif';
  const labelW = Math.max(...rows.map(r => ctx.measureText(r.label).width));
  const dayText = 'Day ' + Math.round(day);
  const boxW = Math.max(ctx.measureText(dayText).width, labelW + valW + 34) + 24;
  const boxH = 58;

  const anchorX = Math.max(staticPoint.x, actualPoint.x);
  const anchorY = Math.min(staticPoint.y, actualPoint.y);
  let boxX = anchorX + 14;
  if (boxX + boxW > W - PAD.right) boxX = anchorX - boxW - 14;
  let boxY = anchorY - boxH - 10;
  if (boxY < PAD.top) boxY = PAD.top;

  ctx.fillStyle = '#fff';
  ctx.strokeStyle = 'rgba(16,20,27,0.08)';
  ctx.lineWidth = 1;
  ctx.shadowColor = 'rgba(16,20,27,0.18)';
  ctx.shadowBlur = 16;
  ctx.shadowOffsetY = 5;
  ctx.beginPath();
  ctx.roundRect(boxX, boxY, boxW, boxH, 10);
  ctx.fill();
  ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;
  ctx.stroke();

  ctx.textAlign = 'left'; ctx.textBaseline = 'top';
  ctx.font = '600 10.5px "Saira", sans-serif';
  ctx.fillStyle = '#9aa1ac';
  ctx.fillText(dayText, boxX + 12, boxY + 8);

  rows.forEach((r, i) => {
    const rowY = boxY + 24 + i * 16;
    ctx.beginPath();
    ctx.arc(boxX + 16, rowY + 5, 3.5, 0, Math.PI * 2);
    ctx.fillStyle = r.color;
    ctx.fill();
    ctx.font = '500 11px "Saira", sans-serif';
    ctx.fillStyle = '#6b7280';
    ctx.fillText(r.label, boxX + 26, rowY);
    ctx.font = '600 12px "Saira", monospace';
    ctx.fillStyle = '#14161a';
    ctx.textAlign = 'right';
    ctx.fillText(r.value, boxX + boxW - 12, rowY - 1);
    ctx.textAlign = 'left';
  });
}

function updateStats(idxFloat) {
  const day = Math.round(valueAt(DAYS, idxFloat));
  const s = valueAt(STATIC_CUM, idxFloat), a = valueAt(ACTUAL_CUM, idxFloat);
  document.getElementById('dayValue').textContent = day.toLocaleString();
  document.getElementById('staticValue').textContent = fmtMoney(s);
  document.getElementById('actualValue').textContent = fmtMoney(a);
  document.getElementById('diffValue').textContent = fmtMoney(a - s);
}

/** drawIdx: how much of the line is currently drawn (only changes during
 * the reveal animation, then stays at N-1 forever). markerIdx: where the
 * dot + tooltip currently sit, independent of drawIdx once the reveal is
 * done, this is what dragging across the chart actually moves. */
function render(drawIdx, markerIdx) {
  if (markerIdx === undefined) markerIdx = drawIdx;
  lastIdx = drawIdx;
  if (!W || !H) return;
  ctx.clearRect(0, 0, W, H);
  drawAxes();
  drawLine(DAYS, STATIC_CUM, drawIdx, STATIC_COLOR, STATIC_FILL);
  drawLine(DAYS, ACTUAL_CUM, drawIdx, ACTUAL_COLOR, ACTUAL_FILL);
  const staticPoint = drawMarker(DAYS, STATIC_CUM, markerIdx, STATIC_COLOR);
  const actualPoint = drawMarker(DAYS, ACTUAL_CUM, markerIdx, ACTUAL_COLOR);
  const day = valueAt(DAYS, markerIdx);
  drawLiveTooltip(staticPoint, actualPoint, day, valueAt(STATIC_CUM, markerIdx), valueAt(ACTUAL_CUM, markerIdx));
  updateStats(markerIdx);
}

window.addEventListener('resize', resizeCanvas);
if (window.ResizeObserver) new ResizeObserver(resizeCanvas).observe(canvasWrap);

/* draws itself once, from day 0 up to the final real totals, then stays
 * fully drawn — dragging/hovering afterward moves the marker along the
 * already-complete line instead of re-truncating it. */
const DRAW_DURATION_MS = 2400;
let revealDone = false;
function animateIn() {
  render(0);
  const start = performance.now();
  function step(now) {
    const p = Math.min(1, (now - start) / DRAW_DURATION_MS);
    const eased = 1 - Math.pow(1 - p, 3);
    render(eased * (N - 1));
    if (p < 1) requestAnimationFrame(step);
    else revealDone = true;
  }
  requestAnimationFrame(step);
}

/* drag (or just hover) left/right along the chart to inspect the cost at
 * any point in time, once the reveal has finished drawing the full line */
function dayToIdx(day) {
  day = Math.max(DAYS[0], Math.min(DAYS[N - 1], day));
  for (let i = 0; i < N - 1; i++) {
    if (day >= DAYS[i] && day <= DAYS[i + 1]) {
      const span = DAYS[i + 1] - DAYS[i] || 1;
      return i + (day - DAYS[i]) / span;
    }
  }
  return N - 1;
}

function scrubToClientX(clientX) {
  const rect = canvas.getBoundingClientRect();
  const relX = clientX - rect.left;
  const frac = Math.max(0, Math.min(1, (relX - PAD.left) / (W - PAD.left - PAD.right)));
  render(N - 1, dayToIdx(MAX_DAY * frac));
}

canvasWrap.style.cursor = 'crosshair';
canvasWrap.addEventListener('mousemove', (e) => { if (revealDone) scrubToClientX(e.clientX); });
canvasWrap.addEventListener('mouseleave', () => { if (revealDone) render(N - 1, N - 1); });
canvasWrap.addEventListener('touchstart', (e) => { if (revealDone) scrubToClientX(e.touches[0].clientX); }, { passive: true });
canvasWrap.addEventListener('touchmove', (e) => { if (revealDone) scrubToClientX(e.touches[0].clientX); }, { passive: true });

resizeCanvas();
requestAnimationFrame(() => setTimeout(animateIn, 150));
</script>
</body>
</html>
"""


def write_html(days: list[int], static_cumulative: list[float], actual_cumulative: list[float]) -> None:
    html = HTML_TEMPLATE
    html = html.replace("__DAYS_JSON__", json.dumps(days))
    html = html.replace("__STATIC_JSON__", json.dumps([round(v, 2) for v in static_cumulative]))
    html = html.replace("__ACTUAL_JSON__", json.dumps([round(v, 2) for v in actual_cumulative]))
    OUTPUT_HTML.write_text(html)


def main() -> None:
    print(f"Loading {cc.STATIC_TIMELINE_CSV.name} ...")
    static_days, static_cumulative = cc.load_static_timeline()

    print(f"Loading {cc.PROPERTIES_CSV.name} ...")
    repaired = cc.load_repaired_properties()
    print(f"  {len(repaired):,} completed repairs")

    max_day = max(static_days[-1], max((r["job_end_day"] for r in repaired), default=0))
    actual_days, actual_cumulative = cc.compute_actual_cumulative(repaired, max_day)

    print(f"Resampling both series every {INTERVAL_DAYS} days ...")
    sample_days_static, sample_static = resample_every_n_days(static_days, static_cumulative, INTERVAL_DAYS)
    sample_days_actual, sample_actual = resample_every_n_days(actual_days, actual_cumulative, INTERVAL_DAYS)
    assert sample_days_static == sample_days_actual, "static/actual timelines must share the same day axis"
    print(f"  {len(sample_days_static)} frames (day 0-{sample_days_static[-1]})")

    print(f"Writing {OUTPUT_HTML.name} ...")
    write_html(sample_days_static, sample_static, sample_actual)

    print("\n=== Summary ===")
    print(f"Final static cumulative:  ${sample_static[-1]:,.0f}")
    print(f"Final actual cumulative:  ${sample_actual[-1]:,.0f}")
    print(f"Final gap (actual - static): ${sample_actual[-1] - sample_static[-1]:,.0f} "
          f"({(sample_actual[-1] / sample_static[-1] - 1):+.1%})")
    print(f"\nWrote {OUTPUT_HTML.relative_to(BASE_DIR)} ({OUTPUT_HTML.stat().st_size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
