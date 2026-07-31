/** Progress timeline — disabled in guest UI (products only). */
(function (global) {
  "use strict";

  function renderTimeline(container) {
    if (!container) return;
    container.innerHTML = "";
  }

  global.TaksitlioSearchProgress = {
    renderTimeline: renderTimeline,
    STEP_LABELS: {},
    STEP_ORDER: [],
  };
})(typeof window !== "undefined" ? window : globalThis);
