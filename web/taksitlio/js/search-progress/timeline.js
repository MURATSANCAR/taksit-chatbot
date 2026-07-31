/** Progress timeline — only checkmarks for completed backend events. */
(function (global) {
  "use strict";

  var STEP_ORDER = [
    "FAST_PARSE_COMPLETED",
    "MERCHANT_CANDIDATES_RESOLVED",
    "PRODUCT_POOL_PARTIAL_READY",
    "PARTIAL_RESULTS_READY",
    "FINANCIAL_INSTITUTION_CANDIDATES_FOUND",
    "PAYMENT_PLAN_CALCULATION_STARTED",
    "FINAL_RESULTS_READY",
  ];

  function renderTimeline(container, events) {
    if (!container) return;
    var seen = {};
    (events || []).forEach(function (e) {
      seen[e.type] = e;
    });
    var html = '<ul class="search-progress-timeline">';
    STEP_ORDER.forEach(function (type, idx) {
      var done = !!seen[type];
      var active =
        !done &&
        events &&
        events.length &&
        events[events.length - 1].type === type;
      var label =
        (seen[type] && seen[type].display && seen[type].display.message) ||
        type;
      var mark = done ? "✓" : active ? "●" : "○";
      html +=
        '<li class="' +
        (done ? "done" : active ? "active" : "pending") +
        '"><span class="mark">' +
        mark +
        "</span> " +
        escapeHtml(label) +
        "</li>";
    });
    html += "</ul>";
    container.innerHTML = html;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  global.TaksitlioSearchProgress = { renderTimeline: renderTimeline };
})(typeof window !== "undefined" ? window : globalThis);
