/* embed.js — shared glue for every file in sections/. Include this once at
   the bottom of each slide's <body>. Each slide is a fixed 1920x1080 iframe
   (see index.html's deck-stage) — this only triggers that slide's .reveal
   cascade (defined in theme.css) the first time index.html shows it, so the
   animation plays once per slide, on arrival, not on every re-visit. */

(function () {
  window.addEventListener('message', (e) => {
    if (e.data && e.data.type === 'ow-activate') {
      document.body.classList.add('in-view');
    }
  });
})();
