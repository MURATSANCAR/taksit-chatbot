/** Partial product carousel — labelled Ön sonuçlar. */
(function (global) {
  "use strict";

  function renderPartial(container, snapshot) {
    if (!container) return;
    if (!snapshot || !(snapshot.products || []).length) {
      container.innerHTML = "";
      return;
    }
    var html =
      '<div class="partial-products"><div class="partial-label">' +
      escapeHtml(snapshot.label || "Ön sonuçlar") +
      '</div><div class="partial-carousel">';
    (snapshot.products || []).forEach(function (p) {
      html +=
        '<article class="partial-card" style="min-width:140px">' +
        (p.thumbnail_cdn_url
          ? '<img width="120" height="90" alt="" src="' +
            escapeHtml(p.thumbnail_cdn_url) +
            '" />'
          : '<div class="skeleton" style="width:120px;height:90px"></div>') +
        "<h4>" +
        escapeHtml(p.display_name) +
        "</h4><p>" +
        escapeHtml(p.merchant_display_name) +
        "</p><p>" +
        escapeHtml(String(p.price)) +
        " TL</p></article>";
    });
    html += "</div></div>";
    container.innerHTML = html;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  global.TaksitlioProgressiveProducts = { render: renderPartial };
})(typeof window !== "undefined" ? window : globalThis);
