# Slide 4 — Repair vs Static Model

Assuming top-left = text, top-right = donut chart (donut + text is the
natural pairing; flagging this in case the intent was reversed — easy to
swap in build if so).

```
+------------------+------------------+
|   TOP LEFT       |   TOP RIGHT       |
|   text            |   donut chart     |
|   (brief framing +|   surge / scope /  |
|   the headline    |   stockout split  |
|   +46.7% figure)  |                  |
+------------------+------------------+
|         BOTTOM — FULL WIDTH           |
|   static vs actual cost over time     |
|   (embedded, re-skinned                |
|   cost_over_time.html)                |
+----------------------------------------+
```

## Top left — text
- Minimal, same restraint as Slide 1's left panel: 1-2 sentences framing
  what this slide shows (static estimate vs. what actually happened),
  then the headline stat large and bold: **+46.7%** with a short label
  ("actual cost above static estimate")
- This is the anchor number for the slide — everything else supports it

## Top right — donut chart
- Three segments: surge pricing / scope growth / stockout logistics,
  using the real attribution percentages
- Each segment coloured distinctly (pick colours that don't clash with
  the blue/red static/actual convention used elsewhere — these are a
  breakdown of the RED "actual" side specifically, so consider shades
  that read as "sub-categories of red/orange" rather than introducing a
  fourth unrelated colour family)
- Centre of the donut: the total $ uplift (~$138M) as a large number —
  donuts read better with a headline figure in the centre rather than
  an empty hole
- Hover each segment: tooltip with exact % and $ figure
- Small callout/label directly on or beside the dominant segment (scope
  growth, ~82%) — worth calling out explicitly rather than letting the
  reader have to infer which slice is biggest, since this is your most
  interesting real finding (delay/backlog, not price surge, is the
  dominant driver)

## Bottom — full width, embedded chart
- Reuse `cost_over_time.html` as-is for the underlying chart logic/data —
  don't rebuild the chart, re-skin it
- Re-skin to match the report's UI aesthetic: same fonts, same
  background/card treatment, same blue/red colour values as used
  elsewhere on this page (if `cost_over_time.html` currently has its own
  default matplotlib/Chart.js styling, this needs a CSS pass so it
  doesn't look like a dropped-in foreign element)
- If `cost_over_time.html` was built as a standalone HTML file (own
  `<html>`/`<head>`), either: (a) extract just its chart-rendering JS/data
  and re-mount it inside a `<div>` in the main report using the report's
  own stylesheet, or (b) if it must stay an iframe embed, strip its
  margins/background so it blends rather than looking like a nested page
  — option (a) is strongly preferred for visual consistency
- This is the two-line version (static vs actual) — the third
  (credibility) line is Slide 6's reveal, not here; if the existing file
  already has three lines, this embed should suppress/hide the
  credibility series for this slide specifically, so it isn't shown
  before its narrative moment