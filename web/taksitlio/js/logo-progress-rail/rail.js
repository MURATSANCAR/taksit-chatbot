/** Logo progress rail — only real candidate logos from backend events. */
(function (global) {
  "use strict";

  function renderRail(container, logos) {
    if (!container) return;
    logos = logos || {};
    var groups = ["merchant", "brand", "institution"];
    var html = '<div class="logo-progress-rail" aria-label="Aday logolar">';
    groups.forEach(function (kind) {
      var items = logos[kind] || [];
      if (!items.length) return;
      html += '<div class="logo-row kind-' + kind + '">';
      items.slice(0, 8).forEach(function (item) {
        var alt = escapeHtml(item.display_name || kind);
        if (item.logo_cdn_url) {
          html +=
            '<img width="40" height="40" alt="' +
            alt +
            '" src="' +
            escapeHtml(item.logo_cdn_url) +
            '" />';
        } else {
          html +=
            '<span class="logo-fallback" title="' +
            alt +
            '" style="width:40px;height:40px;display:inline-flex;align-items:center;justify-content:center">' +
            alt.slice(0, 2) +
            "</span>";
        }
      });
      html += "</div>";
    });
    html += "</div>";
    container.innerHTML = html;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  global.TaksitlioLogoProgressRail = { render: renderRail };
})(typeof window !== "undefined" ? window : globalThis);
