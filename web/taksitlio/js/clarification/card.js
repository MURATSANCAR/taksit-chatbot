/** Clarification card — targeted options, free text allowed. */
(function (global) {
  "use strict";

  function renderClarification(container, clarification, onSelect) {
    if (!container || !clarification) return;
    var html =
      '<div class="clarification-card" role="group" aria-label="Netleştirme">' +
      "<p>" +
      escapeHtml(clarification.question_text) +
      "</p><div class="clarification-options">";
    (clarification.options || []).forEach(function (opt) {
      html +=
        '<button type="button" data-option-id="' +
        escapeHtml(opt.option_id) +
        '">' +
        escapeHtml(opt.label) +
        "</button>";
    });
    html += "</div></div>";
    container.innerHTML = html;
    container.querySelectorAll("button[data-option-id]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (onSelect) onSelect([btn.getAttribute("data-option-id")]);
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

  global.TaksitlioClarification = { render: renderClarification };
})(typeof window !== "undefined" ? window : globalThis);
