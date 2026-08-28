Slide 2 — Dynamic Flooding Model Over Time

Simple, two-visual layout, stacked vertically, synced together.

+----------------------------------------+
|          FLOOD MAP (animated)          |
|   Leaflet, time-slider driven, real    |
|   property points colored by flood     |
|   state at the current interval_hour   |
+----------------------------------------+
|      AHD-OVER-TIME LINE CHART          |
|   real hydrograph, same time axis,     |
|   a moving marker/playhead shows       |
|   "you are here" as the map animates   |
+----------------------------------------+
One shared time control drives both — a single slider/play button, not two independent ones. Dragging it (or hitting play) moves the map's flood-state coloring AND the chart's playhead marker in lockstep.
Map: properties colored by is_flooded (or a depth gradient) at the current interval_hour, pulled from flood_exposure.csv.
Chart: the real hydrograph line (flood_hydrograph.csv), with a vertical playhead line + dot tracking the current time, and the current AHD value called out in text next to the dot as it moves.
Auto-play on scroll-into-view (once), then let the user scrub manually after — gives the "wow" moment automatically without requiring interaction, but stays explorable afterward.
Keep this slide otherwise empty — no text block, no cards. The two synced visuals ARE the content.


NOTE: THE FLOOD MAP SHOULD BE BOUNDED TO THE AREA WHICH IS COVERED BY THE MAP - THE EDGE OF THE MODLED FLOOD SHOULD BE PERFECTLY IN LINE WITH THE OUTLINE OF THE SECTION SO THERE IS NO OBVIOUS CUT OFF OF WHERE THE FLOOD DATA STOPS ON THE MAP (AS THIS CUTOFF IS NATURALLY DONE BY THE FEATURES CUT OFF)