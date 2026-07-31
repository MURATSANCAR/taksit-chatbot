/** Constraint chips — disabled in guest UI (progress + products only). */
(function (global) {
  "use strict";

  function renderChips(container) {
    if (!container) return;
    container.innerHTML = "";
  }

  global.TaksitlioConstraintChips = { render: renderChips };
})(typeof window !== "undefined" ? window : globalThis);
