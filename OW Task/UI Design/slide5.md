# Slide 5 — Credibility Model

```
+------------------+------------------+
|   TOP LEFT       |   TOP RIGHT       |
|   logic +        |   accuracy over   |
|   collapsible    |   time (error     |
|   maths panel    |   area chart)     |
+------------------+------------------+
|         BOTTOM — FULL WIDTH           |
|   remaining repair cost over time     |
|   (animated, the chart already        |
|   reviewed — static/actual/credibility|
|   remaining-cost lines)               |
+----------------------------------------+
```

## Top left — logic + collapsible maths
- Plain-language explanation first, no formulas visible by default:
  short description of what the credibility model does (blends the
  static estimate with real evidence from nearby/similar completed
  repairs, trusting that evidence more as it accumulates)
- Small schematic optional here if there's room: property in the centre,
  nearby completed jobs as weighted dots (size = weight) — reinforces
  "nearby and similar" visually before any maths is shown
- **Collapsible "Show the maths" panel**, closed by default: the actual
  Bühlmann formula, the similarity-variable table per component, the
  distance/recency weighting, the outlier protection (MAD-based), the
  tuned constants (k, distance range, recency half-life) — full technical
  detail for anyone who clicks it open, completely hidden otherwise

## Top right — accuracy over time (error area chart)
This is the validation payoff — worth building carefully since it's the
chart that actually proves the credibility model works.

- **X-axis**: time, weekly intervals (day 0, 7, 14, ... through the full
  simulation range) — courser than the underlying model's evaluation
  granularity is fine here, since weekly is the right resolution for a
  reader to track visually
- **Y-axis**: absolute error (or % error) of the credibility-updated
  portfolio estimate vs. the known final true total ($433.9M) — i.e.
  `|cumulative_credibility_estimate(t) - final_actual_total|` at each
  weekly checkpoint
- **Area chart, filled from the line down to zero** — visually this
  reads as "the error," shrinking and eventually flattening to (near)
  zero as the model converges, which is exactly the story you want: a
  wedge of red/orange area that narrows to nothing by the end
- Consider showing the STATIC model's error as a second, flat/non-shrinking
  reference line on the same chart (or a very thin second area) — the
  static line's error never improves (it's a constant gap to the true
  total throughout), which is the direct visual contrast that makes the
  credibility model's shrinking error meaningful. Without this reference,
  a shrinking-to-zero error chart alone doesn't show what the *alternative*
  (not using a credibility model at all) would have looked like.
- Annotate the point where error first drops below some meaningful
  threshold (e.g. <10% or <5%) with a small marker/label — "converged to
  within 5% by day X" is a genuinely strong stat to be able to point at
- Colour: since this chart is specifically about the GREEN
  (credibility) line's behaviour, use green for the shrinking area, and
  a muted/dashed grey or blue for the static reference line (not red —
  red is reserved for "actual" elsewhere and using it here would suggest
  this chart is showing something about the actual-cost line, which it
  isn't)

## Bottom — remaining repair cost over time (animated)
- The chart already built and reviewed (three lines: static remaining,
  actual remaining, credibility remaining, all descending toward zero as
  repairs complete) — same colour convention (blue/red/green) as
  everywhere else
- "Animated like the others": apply the same on-scroll draw-in animation
  treatment used for the other line charts on this page (lines drawing
  progressively left to right, or the step function animating in) rather
  than appearing instantly — consistency with the animation language
  established on Slides 1, 2, and 4
- Keep the stockout-period shading (the grey band from the earlier
  version of this chart) if it's still informative here — worth a
  quick call given it adds a bit of visual complexity; drop it if the
  slide feels crowded once the top-right area chart is also in place

## Design note on the two charts together
Top-right (error shrinking to zero) and bottom (remaining cost shrinking
to zero) are conceptually similar shapes — both are "things converging
toward zero over time." Make sure they're visually distinguishable at a
glance (different chart type — area vs. line/step — already helps; keep
that distinction rather than making both area charts, which would risk
the slide reading as one repeated idea rather than two different
validations).