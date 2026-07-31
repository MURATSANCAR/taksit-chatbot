/** Partial product cards — 2–3 on-screen, featured badge on top. */
(function (global) {
  "use strict";

  function visibleCount() {
    var shell = document.querySelector(".shell");
    var w =
      (shell && shell.getBoundingClientRect().width) ||
      window.innerWidth ||
      390;
    return w < 340 ? 2 : 3;
  }

  function formatPrice(price) {
    var n = Number(price);
    if (!isFinite(n)) return String(price == null ? "" : price);
    try {
      return new Intl.NumberFormat("tr-TR").format(n);
    } catch (_) {
      return String(n);
    }
  }

  function renderPartial(container, snapshot) {
    if (!container) return;
    var all = (snapshot && snapshot.products) || [];
    if (!all.length) {
      container.innerHTML = "";
      return;
    }
    var limit = visibleCount();
    var products = all.slice(0, limit);
    var html =
      '<div class="partial-products" data-cols="' +
      products.length +
      '"><div class="partial-label">' +
      escapeHtml(snapshot.label || "Ön sonuçlar") +
      '</div><div class="partial-carousel">';
    products.forEach(function (p, i) {
      var featured = i === 0;
      var finance = p.best_finance_summary || p.best_finance || null;
      var financeHtml = "";
      if (
        finance &&
        finance.monthly_payment != null &&
        finance.term_months != null
      ) {
        financeHtml =
          '<p class="partial-finance">' +
          escapeHtml(formatPrice(finance.monthly_payment)) +
          " TL × " +
          escapeHtml(String(finance.term_months)) +
          " ay</p>" +
          '<span class="partial-finance-hint">' +
          escapeHtml(finance.display_label || "Tahmini aylık ödeme") +
          "</span>";
      }
      html +=
        '<article class="partial-card' +
        (featured ? " is-featured" : "") +
        '">' +
        '<div class="partial-media">' +
        (featured
          ? '<span class="partial-badge">Öne çıkan</span>'
          : "") +
        (p.thumbnail_cdn_url
          ? '<img alt="" loading="lazy" src="' +
            escapeHtml(p.thumbnail_cdn_url) +
            '" />'
          : '<div class="skeleton" aria-hidden="true"></div>') +
        "</div>" +
        "<h4>" +
        escapeHtml(p.display_name) +
        "</h4><p>" +
        escapeHtml(p.merchant_display_name || "") +
        "</p><p class=\"partial-price\">" +
        escapeHtml(formatPrice(p.price)) +
        " TL</p>" +
        financeHtml +
        "</article>";
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
