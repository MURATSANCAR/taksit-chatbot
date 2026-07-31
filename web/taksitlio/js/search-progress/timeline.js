/** Progress timeline — sequential TR steps with motion (no raw event codes). */
(function (global) {
  "use strict";

  var STEP_ORDER = [
    "FAST_PARSE_COMPLETED",
    "MERCHANT_CANDIDATES_RESOLVED",
    "PARTIAL_RESULTS_READY",
    "FINANCIAL_INSTITUTION_CANDIDATES_FOUND",
    "PAYMENT_PLAN_CALCULATION_STARTED",
    "FINAL_RESULTS_READY",
  ];

  var STEP_LABELS = {
    FAST_PARSE_COMPLETED: "İhtiyacını anladım",
    MERCHANT_CANDIDATES_RESOLVED: "Senin için uygun mağazaları seçiyorum",
    PARTIAL_RESULTS_READY: "Ön sonuçlar hazır — tercihlerinle daraltıyorum",
    FINANCIAL_INSTITUTION_CANDIDATES_FOUND:
      "Taksit ve finansman seçeneklerini karşılaştırıyorum",
    PAYMENT_PLAN_CALCULATION_STARTED:
      "Aylık ödeme ve vade seçeneklerini hesaplıyorum",
    FINAL_RESULTS_READY: "Sana özel öneriler hazır",
  };

  /** Events that advance progress without owning a visible row. */
  var ADVANCE_ALIASES = {
    SEARCH_ACCEPTED: "FAST_PARSE_COMPLETED",
    FAST_PARSE_STARTED: "FAST_PARSE_COMPLETED",
    ENTITY_RESOLUTION_STARTED: "MERCHANT_CANDIDATES_RESOLVED",
    ENTITY_RESOLUTION_COMPLETED: "MERCHANT_CANDIDATES_RESOLVED",
    PRODUCT_POOL_SEARCH_STARTED: "MERCHANT_CANDIDATES_RESOLVED",
    PRODUCT_POOL_PARTIAL_READY: "PARTIAL_RESULTS_READY",
    FINANCE_SEARCH_STARTED: "FINANCIAL_INSTITUTION_CANDIDATES_FOUND",
    RANKING_STARTED: "FINAL_RESULTS_READY",
    SEARCH_COMPLETED: "FINAL_RESULTS_READY",
    SEARCH_COMPLETED_DEGRADED: "FINAL_RESULTS_READY",
  };

  function reduceMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (_) {
      return false;
    }
  }

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

  function indexOfStep(type) {
    var i = STEP_ORDER.indexOf(type);
    if (i >= 0) return i;
    var alias = ADVANCE_ALIASES[type];
    return alias ? STEP_ORDER.indexOf(alias) : -1;
  }

  function progressState(events) {
    var seen = {};
    var maxDone = -1;
    (events || []).forEach(function (e) {
      if (!e || !e.type) return;
      var idx = indexOfStep(e.type);
      if (STEP_ORDER.indexOf(e.type) >= 0) {
        seen[e.type] = e;
        maxDone = Math.max(maxDone, STEP_ORDER.indexOf(e.type));
      } else if (idx >= 0) {
        maxDone = Math.max(maxDone, idx);
        if (!seen[STEP_ORDER[idx]]) {
          seen[STEP_ORDER[idx]] = { type: STEP_ORDER[idx], display: e.display };
        }
      }
    });
    var allDone = maxDone >= STEP_ORDER.length - 1 && !!seen[STEP_ORDER[STEP_ORDER.length - 1]];
    var activeIdx = -1;
    if (!allDone) {
      activeIdx = Math.min(maxDone + 1, STEP_ORDER.length - 1);
      if (maxDone < 0 && (events || []).length) activeIdx = 0;
    }
    var revealUntil = allDone
      ? STEP_ORDER.length - 1
      : Math.max(activeIdx, maxDone, 0);
    return { seen: seen, maxDone: maxDone, activeIdx: activeIdx, revealUntil: revealUntil, allDone: allDone };
  }

  function ensureList(container) {
    var ul = container.querySelector("ul.search-progress-timeline");
    if (ul) return ul;
    container.innerHTML =
      '<ul class="search-progress-timeline" aria-label="Arama adımları"></ul>';
    return container.querySelector("ul.search-progress-timeline");
  }

  function renderTimeline(container, events) {
    if (!container) return;
    if (!events || !events.length) {
      container.innerHTML = "";
      return;
    }
    var state = progressState(events);
    var ul = ensureList(container);
    var existing = {};
    Array.prototype.forEach.call(ul.querySelectorAll("li[data-step]"), function (li) {
      existing[li.getAttribute("data-step")] = li;
    });

    STEP_ORDER.forEach(function (type, idx) {
      if (idx > state.revealUntil) {
        if (existing[type]) existing[type].remove();
        return;
      }
      var done = idx <= state.maxDone;
      var active = !done && idx === state.activeIdx;
      var label = labelFor(type, state.seen[type]);
      if (global.TaksitlioPublic && global.TaksitlioPublic.sanitize) {
        label = global.TaksitlioPublic.sanitize(label);
      }
      var mark = done ? "✓" : active ? "●" : "○";
      var cls = done ? "done" : active ? "active" : "pending";
      var li = existing[type];
      var isNew = !li;
      if (!li) {
        li = document.createElement("li");
        li.setAttribute("data-step", type);
        li.innerHTML =
          '<span class="mark" aria-hidden="true"></span><span class="step-text"></span>';
        ul.appendChild(li);
      }
      var prev = li.getAttribute("data-state");
      li.className = cls;
      li.setAttribute("data-state", cls);
      li.querySelector(".mark").textContent = mark;
      li.querySelector(".step-text").textContent = label;

      if (!reduceMotion()) {
        if (isNew) {
          li.classList.add("step-enter");
          li.style.setProperty("--step-i", String(idx));
          requestAnimationFrame(function () {
            li.classList.add("step-enter-on");
          });
        } else if (prev !== cls && done) {
          li.classList.remove("step-pop");
          void li.offsetWidth;
          li.classList.add("step-pop");
        }
      }
    });

    Object.keys(existing).forEach(function (type) {
      var idx = STEP_ORDER.indexOf(type);
      if (idx > state.revealUntil && existing[type].parentNode) {
        existing[type].remove();
      }
    });
  }

  global.TaksitlioSearchProgress = {
    renderTimeline: renderTimeline,
    STEP_LABELS: STEP_LABELS,
    STEP_ORDER: STEP_ORDER,
  };
})(typeof window !== "undefined" ? window : globalThis);
