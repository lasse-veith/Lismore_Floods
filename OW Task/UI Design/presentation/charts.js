/* charts.js — tiny dependency-free SVG line/area-chart helper shared by the
   section files that need one (03, 04, 05). Not a visual on its own — just
   draws whatever series each section hands it, using this site's colour
   tokens. Keeps every section file focused on its own real data instead of
   re-deriving axis/gridline math. */

const OWCharts = (function () {
  function el(name, attrs) {
    const e = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  /**
   * Renders a multi-series line/area chart into an <svg> element.
   * @param {SVGSVGElement} svg
   * @param {{
   *   x:number[],
   *   series: {values:number[], color:string, dashed?:boolean, fill?:boolean}[],
   *   yFormat?:(v)=>string, xFormat?:(v)=>string, yTicks?:number, xTicks?:number,
   *   annotate?:{x:number,y:number,label:string,color:string},
   *   shadeBands?: {x0:number,x1:number,color?:string,label?:string}[],
   *   animate?: boolean
   * }} cfg
   */
  function lineChart(svg, cfg) {
    // viewBox size is configurable per call — a full-bleed chart needs a much
    // wider aspect ratio than a half-width one to render at a sane height on
    // the fixed 1920x1080 stage (height scales with displayed width at a
    // fixed W:H ratio, so a chart shown twice as wide needs twice the W here
    // to land at the same rendered height).
    const W = cfg.width || 860, H = cfg.height || 340;
    const PAD = { l: 56, r: 20, t: 18, b: 34 };
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.innerHTML = '';

    const allY = cfg.series.flatMap(s => s.values);
    const minX = cfg.x[0], maxX = cfg.x[cfg.x.length - 1];
    const maxY = cfg.yMax !== undefined ? cfg.yMax : Math.max(...allY) * 1.08;
    const minY = cfg.yMin !== undefined ? cfg.yMin : Math.min(0, Math.min(...allY));

    const xPix = (v) => PAD.l + ((v - minX) / (maxX - minX)) * (W - PAD.l - PAD.r);
    const yPix = (v) => H - PAD.b - ((v - minY) / (maxY - minY)) * (H - PAD.t - PAD.b);
    const baselineY = yPix(Math.max(minY, 0));

    // shaded background bands (e.g. "stocked out" period) — drawn first, under everything
    if (cfg.shadeBands) {
      cfg.shadeBands.forEach((b) => {
        const x0 = xPix(b.x0), x1 = xPix(b.x1);
        svg.appendChild(el('rect', { x: x0, y: PAD.t, width: Math.max(0, x1 - x0), height: H - PAD.t - PAD.b, fill: b.color || 'rgba(255,255,255,0.05)' }));
        if (b.label) {
          const t = el('text', { x: x0 + 6, y: PAD.t + 14, fill: '#5b6377', 'font-size': 9.5, 'font-family': "'IBM Plex Mono', monospace" });
          t.textContent = b.label;
          svg.appendChild(t);
        }
      });
    }

    // gridlines + y labels
    const yTicks = cfg.yTicks || 4;
    for (let i = 0; i <= yTicks; i++) {
      const val = minY + ((maxY - minY) / yTicks) * i;
      const y = yPix(val);
      svg.appendChild(el('line', { x1: PAD.l, x2: W - PAD.r, y1: y, y2: y, stroke: 'rgba(255,255,255,0.06)', 'stroke-width': 1 }));
      const t = el('text', { x: PAD.l - 8, y: y + 3, fill: '#5b6377', 'font-size': 10, 'font-family': "'IBM Plex Mono', monospace", 'text-anchor': 'end' });
      t.textContent = cfg.yFormat ? cfg.yFormat(val) : val.toFixed(0);
      svg.appendChild(t);
    }

    // x labels
    const xTicks = cfg.xTicks || 4;
    for (let i = 0; i <= xTicks; i++) {
      const val = minX + ((maxX - minX) / xTicks) * i;
      const x = xPix(val);
      const t = el('text', { x, y: H - PAD.b + 16, fill: '#5b6377', 'font-size': 10, 'font-family': "'IBM Plex Mono', monospace", 'text-anchor': 'middle' });
      t.textContent = cfg.xFormat ? cfg.xFormat(val) : val.toFixed(0);
      svg.appendChild(t);
    }

    // axis lines
    svg.appendChild(el('line', { x1: PAD.l, x2: PAD.l, y1: PAD.t, y2: H - PAD.b, stroke: 'rgba(255,255,255,0.16)', 'stroke-width': 1 }));
    svg.appendChild(el('line', { x1: PAD.l, x2: W - PAD.r, y1: H - PAD.b, y2: H - PAD.b, stroke: 'rgba(255,255,255,0.16)', 'stroke-width': 1 }));

    const drawnPaths = [];

    cfg.series.forEach((s) => {
      let d = '';
      cfg.x.forEach((xv, i) => {
        const x = xPix(xv), y = yPix(s.values[i]);
        d += (i === 0 ? 'M' : 'L') + x.toFixed(2) + ',' + y.toFixed(2) + ' ';
      });

      if (s.fill) {
        const lastX = xPix(cfg.x[cfg.x.length - 1]), firstX = xPix(cfg.x[0]);
        const areaD = d + `L${lastX.toFixed(2)},${baselineY.toFixed(2)} L${firstX.toFixed(2)},${baselineY.toFixed(2)} Z`;
        const area = el('path', { d: areaD, fill: s.color, opacity: 0.16, stroke: 'none' });
        svg.appendChild(area);
      }

      const path = el('path', { d, fill: 'none', stroke: s.color, 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' });
      if (s.dashed) path.setAttribute('stroke-dasharray', '5,5');
      svg.appendChild(path);
      drawnPaths.push({ path, dashed: !!s.dashed });
    });

    if (cfg.annotate) {
      const ax = xPix(cfg.annotate.x), ay = yPix(cfg.annotate.y);
      svg.appendChild(el('circle', { cx: ax, cy: ay, r: 4, fill: cfg.annotate.color }));
      svg.appendChild(el('line', { x1: ax, x2: ax, y1: ay - 6, y2: ay - 22, stroke: cfg.annotate.color, 'stroke-width': 1 }));
      const t = el('text', { x: ax, y: ay - 26, fill: cfg.annotate.color, 'font-size': 10.5, 'font-family': "'IBM Plex Mono', monospace", 'text-anchor': 'middle' });
      t.textContent = cfg.annotate.label;
      svg.appendChild(t);
    }

    // on-scroll draw-in: dashed lines fade in (dasharray already carries the
    // dash pattern, animating length would fight it), solid lines draw
    // progressively left-to-right via the classic stroke-dashoffset trick.
    if (cfg.animate) {
      drawnPaths.forEach(({ path, dashed }, i) => {
        if (dashed) {
          path.style.opacity = 0;
          path.style.transition = `opacity 0.8s ease ${0.15 * i}s`;
          requestAnimationFrame(() => requestAnimationFrame(() => { path.style.opacity = 1; }));
          return;
        }
        const len = path.getTotalLength();
        path.style.strokeDasharray = len;
        path.style.strokeDashoffset = len;
        path.style.transition = `stroke-dashoffset 1.3s var(--ease, ease) ${0.15 * i}s`;
        requestAnimationFrame(() => requestAnimationFrame(() => { path.style.strokeDashoffset = 0; }));
      });
    }
  }

  return { lineChart };
})();
