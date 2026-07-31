/** Constraint chips from understanding state. */
(function (global) {
  "use strict";

  var HIDDEN_KINDS = { budget: 1, uncertainty: 1 };

  function visibleChips(chips) {
    return (chips || []).filter(function (chip) {
      if (!chip) return false;
      if (HIDDEN_KINDS[chip.kind]) return false;
      if (chip.id === "cat:uncertain") return false;
      return true;
    });
  }

  function renderChips(container, chips, onAction) {
    if (!container) return;
    var list = visibleChips(chips);
    if (!list.length) {
      container.innerHTML = "";
      return;
    }
    var html = '<div class="constraint-chips" role="list">';
    list.forEach(function (chip) {
      html +=
        '<button type="button" role="listitem" class="chip kind-' +
        escapeHtml(chip.kind || "default") +
        '" data-chip-id="' +
        escapeHtml(chip.id) +
        '">' +
        escapeHtml(chip.label) +
        "</button>";
    });
    html += "</div>";
    container.innerHTML = html;
    container.querySelectorAll("button.chip").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (onAction) onAction(btn.getAttribute("data-chip-id"));
      });
    });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  global.TaksitlioConstraintChips = { render: renderChips };
})(typeof window !== "undefined" ? window : globalThis);
