# Page 1 — Hero Section — Detailed Layout Spec

## Grid structure
Two-column CSS grid, roughly 38% / 62% split, full viewport height.

```
+------------------+------------------------------+
|                  |   TOP RIGHT                   |
|                  |   hero visual                 |
|   LEFT PANEL     |   (tall, ~50% of right column)|
|   (full height,  +------------------------------+
|   minimal text)  |  CARD A   |   CARD B          |
|                  |  (static) |   (actual)        |
|                  +------------------------------+
|                  |   BAR CHART                   |
|                  |   static vs actual             |
|                  |   (~30% of right column)      |
+------------------+------------------------------+
```

---

## Left panel — minimal text
- Title (large, bold): the project name / one clean headline
- One, maybe two sentences max — the framing question, not a summary of
  results (results live on the right, this panel earns attention through
  restraint, not density)
- A small scroll-cue at the bottom (subtle animated down-arrow or "Scroll
  to explore" — low-key, not a competing focal point)
- Consider a subtle animated background on this panel specifically — a
  slow, ambient water-ripple or ripple-on-scroll canvas effect behind the
  text (low opacity, doesn't fight the text for attention) — this is a
  good place to spend an "insane visual" budget item cheaply, since it's
  ambient rather than something the user needs to read/interpret

## Top right — hero visual
**Note: the hydrograph chart + animated flood map are already spoken for
by Page 2 (synced together there) — this needs to be a genuinely
different visual, not a preview of the same data in the same form, or
Page 2 loses its impact from feeling repeated.**

**Recommendation: an abstract "scale of the event" hero moment — not a
line chart, not a map, not a literal photo.**

Reasoning against a photo: a real photograph would need to be either a
generic stock/AI image (undercuts the "grounded in real data" story) or
an actual real news photo (reproduction/copyright issues). Reasoning
against reusing the hydrograph/map form here: that's Page 2's whole
purpose, and leading with it on Page 1 would flatten Page 2's reveal.

**Build — an illustrative water-rise animation with the peak stat as the
payoff**:
- A simple silhouette illustration — a stylized row of house
  outlines/skyline (flat, iconographic, not photorealistic) sitting on a
  baseline
- An animated water-fill rises from the bottom of the panel up to a level
  representing the real 14.4m peak (scaled to the illustration, not a
  literal ruler) — a smooth, continuous rise animation on load/scroll,
  with a soft gradient and subtle ripple/shimmer texture on the water's
  surface (this is where a small amount of canvas/SVG animation effort
  buys a lot of visual impact, since it's abstract rather than needing
  to be data-precise)
- As the water reaches its peak, a large glowing stat fades/scales in
  over it: **"14.4m"** with a short line underneath — "the highest flood
  level on record, 2m above the previous 1954 peak" (a real fact,
  already sourced earlier in the project)
- Optional light interactivity: hovering over the illustration could
  gently increase the ripple/shimmer intensity, or a small toggle lets
  the water level animate down to the *previous* record (12.27m, 1954)
  for a quick visual comparison — nice-to-have, not essential
- This stays purely illustrative/atmospheric — no axis, no data points,
  no time dimension — precisely so it doesn't compete with or duplicate
  Page 2's literal, data-precise hydrograph + map pairing

This gives Page 1 its own distinct hero moment (scale/drama of the
event, delivered abstractly) while saving the literal real-data
chart-and-map payoff for Page 2, where it lands with full impact instead
of feeling like a rerun.

## Middle right — two KPI cards
- **Card A**: Static Estimate, $295.8M, blue accent
- **Card B**: Simulated Actual, $433.9M, red accent
- Numbers should **count up on scroll-into-view** (animated number
  tween from 0 to final value, ~1-1.5s) rather than appearing static —
  cheap, high-impact interactivity
- Small subtext under each: one line each ("Day-0 estimate" / "Realized
  cost, modelled") — no more than that, cards should be scannable in
  under a second

## Bottom right — bar chart, static vs actual
- Two bars, blue and red, same colour convention as the cards above them
  (visual link between the cards and the chart reinforces they're the
  same two numbers, just visualized differently)
- Animate bars growing from 0 on scroll-into-view, not appearing instantly
- Show the % difference (+46.7%) as a floating annotation/bracket between
  the two bars — this is the number you want the eye to land on
- Hover on either bar: small tooltip with the exact dollar figure
- Keep this chart simple and uncluttered — it's a supporting confirmation
  of the cards above it, not a new dataset to interpret

---

## Interactivity summary (the "insane visuals" budget, spent deliberately)
1. Ambient ripple background, left panel — atmosphere, low cognitive load
2. Draw-on-scroll animated hydrograph with hover tooltip — the hero
   moment, real data, genuinely interactive
3. Count-up KPI cards on scroll — cheap, satisfying, reinforces the
   numbers before the chart shows them again
4. Grow-in animated bar chart with hover tooltips — ties cards to chart
   visually and numerically

Deliberately NOT doing: a literal flood photo (copyright/authenticity
issues), a 3D/WebGL scene (high build risk for the payoff, better spent
on the flood map in Section 2 where a real geospatial payoff exists), or
stacking more than these four interactive elements on one screen (risk of
feeling busy rather than polished — restraint is part of "insane" reading
as intentional rather than chaotic).