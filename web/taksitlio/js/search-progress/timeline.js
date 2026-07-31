/** Progress timeline — Turkish marketing copy only (never raw event codes). */
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

  /** Friendly TR copy for each step — pending and fallback for missing display.message. */
  var STEP_LABELS = {
    FAST_PARSE_COMPLETED: "İhtiyacını anladım",
    MERCHANT_CANDIDATES_RESOLVED: "Senin için uygun mağazaları seçiyorum",
    PRODUCT_POOL_PARTIAL_READY: "Katalogdan ilk eşleşmeleri getiriyorum",
    PARTIAL_RESULTS_READY: "Ön sonuçlar hazır — tercihlerinle daraltıyorum",
    FINANCIAL_INSTITUTION_CANDIDATES_FOUND:
      "Taksit ve finansman seçeneklerini karşılaştırıyorum",
    PAYMENT_PLAN_CALCULATION_STARTED:
      "Aylık ödeme ve vade seçeneklerini hesaplıyorum",
    FINAL_RESULTS_READY: "Sana özel öneriler hazır",
  };

  function isRawEventCode(text) {
    return /^[A-Z][A-Z0-9_]{3,}$/.test(String(text || "").trim());
  }

  function labelFor(type, event) {
    var fromBackend =
      event && event.display && (event.display.message || event.display.title);
    if (fromBackend && !isRawEventCode(fromBackend)) {
      return String(fromBackend);
    }
    return STEP_LABELS[type] || "İşlem devam ediyor…";
  }

  function renderTimeline(container, events) {
    if (!container) return;
    var seen = {};
    (events || []).forEach(function (e) {
      seen[e.type] = e;
    });
    var html = '<ul class="search-progress-timeline" aria-label="Arama adımları">';
    STEP_ORDER.forEach(function (type) {
      var done = !!seen[type];
      var active =
        !done &&
        events &&
        events.length &&
        events[events.length - 1].type === type;
      var label = labelFor(type, seen[type]);
      if (global.TaksitlioPublic && global.TaksitlioPublic.sanitize) {
        label = global.TaksitlioPublic.sanitize(label);
      }
      var mark = done ? "✓" : active ? "●" : "○";
      html +=
        '<li class="' +
        (done ? "done" : active ? "active" : "pending") +
        '"><span class="mark" aria-hidden="true">' +
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

  global.TaksitlioSearchProgress = {
    renderTimeline: renderTimeline,
    STEP_LABELS: STEP_LABELS,
  };
})(typeof window !== "undefined" ? window : globalThis);
