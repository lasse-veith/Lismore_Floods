# Final Report — Visual Plan (v2, user-specified structure)

Single-page scroll, HTML. Consistent colour coding throughout:
**blue = static, red = actual, green = credibility.**

---

## 1. Intro / Hero
- Title, one-line framing (the event, the question being answered)
- 4 KPI cards: `Static Estimate` ($295.8M) · `Simulated Actual` ($433.9M) ·
  `Gap` (+46.7%) · `Properties Affected` (6,446)
- These four numbers are the anchor for the whole page — everything after
  this is explaining how you got them

## 2. Dynamic Flooding Model Over Time
- Embedded flood progression map , this is an embedding of the flood_heat_map over time on the left, and on the righ the river_level_over time html file - they should move at the same speed

- Real-data callouts alongside: 14.4m real peak, real hydrograph
  date/time, real levee-overtop moment — this is where "grounded in the
  actual event" gets planted early, before any modelling is shown
- Light section — a map and a few stat chips, no charts yet

## 3. How the Static Cost Engine Was Built (incl. data collection)
This is the longest section, since it's covering both data sourcing and
methodology — split it into two visual halves so it doesn't read as one
wall of text:

**3a — Data collection**
- Compact visual data-source table or icon row: G-NAF (addresses), LiDAR
  (elevation), OSM (footprints), ABS Census (dwelling mix), the report
  (hydrograph/floor-height methodology/damage benchmark)
- Small "real vs synthetic" split visual (e.g. a simple two-tone bar or
  donut showing how many fields are real-sourced vs. calibrated-synthetic)
- Collapsible panel for the full field-by-field breakdown (links to
  `SYNTHETIC_ASSUMPTIONS.md` content) — technical readers only

**3b — Cost engine mechanics**
- Simple diagram or flow showing: property characteristics + flood depth
  → depth-tiered component triggers (flooring always, plaster/paint >5cm,
  electrical >25cm) → 9 unit-cost components → $295.8M total
- One supporting chart: distribution of triggered-component-count per
  property (how many properties got all 9 components vs. only a few) —
  this visually reinforces that the model isn't crudely all-or-nothing

## 4. How the Repair Model Was Built
- Diagram of the pipeline stages: insurance delay → queue position
  (Weibull rollout curve) → job duration (6 construction stages) → market
  pricing (log-normal baseline × Weibull surge decay × stockout logistics
  premium) → scope growth (mold/idle-time penalty)
- **This is the natural home for the mechanism-explainer visuals** —
  small standalone charts showing: the Weibull rollout S-curve itself
  (job starts over time), the surge-intensity decay curve, the warehouse
  stockout trigger point — three small supporting charts rather than one
  cluttered mega-chart
- Collapsible panel: full formula detail (the L(t)/M(t) equations, the
  scope multiplier formula) for a technical reader

## 5. Repair vs Static Model
- **The core cost-over-time chart** (static vs actual, two lines) — full - these are the lines on price_over_time.html 
  width, the biggest chart so far
- Directly below: mechanism attribution (surge / scope / stockout) as a
  stacked bar or donut, with the queue-backlog finding called out in a
  visually distinct callout box — this is your most interesting real
  insight and shouldn't just be one bar among several
- 1-2 sentence plain-language readout under the main chart

## 6. Credibility Model — Overview + Visuals
- Short conceptual explainer first (not math): a small schematic showing
  a property in the centre with nearby completed jobs as weighted dots
  (size/opacity = weight) — this is the one piece of methodology worth a
  picture, since "geospatial credibility model" won't be intuitive from
  text alone
- Then the full three-line cost-over-time chart (static / actual /
  credibility) — this is where the third line finally appears, landing
  with more impact after section 5 already established the static/actual
  gap
- Convergence chart: credibility error vs. static error, shrinking over
  time
- Collapsible panel: Bühlmann formula, similarity-variable table, tuned
  constants — technical detail, closed by default

## 7. Summary
- Not a chart — 3-5 short, numbered Key Observations, each tied to a
  specific number already shown earlier on the page
- This is the deliverable's actual thesis — give it real visual weight
  (numbered cards, larger type), don't let it read as an afterthought
  paragraph at the bottom

---

## Cross-cutting notes
- Progressive disclosure via collapsible panels in sections 3, 4, and 6 —
  keeps the default read exec-friendly while technical detail is one
  click away, not absent
- Keep every chart's colour coding identical throughout — no re-mapping
  static/actual/credibility between sections
- Section 5 and Section 6 both deserve their own full-width money chart —
  don't compress them into one crowded chart with all three lines shown
  only once; showing static-vs-actual first (section 5), then
  reintroducing credibility on top (section 6), lets the credibility
  line's value land as its own moment rather than being visually buried
  from the start
- Sections 1 and 7 (hero + summary) are where to spend the most design
  effort — first and last impression