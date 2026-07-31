/** Local / CDN entity logos for compact card meta (no invented finance). */
(function (global) {
  "use strict";

  var BASE = "assets/logos/";

  /** Normalized display name → filename under assets/logos. */
  var BY_NAME = {
    mediamarkt: "m-mediamarkt.png",
    "media markt": "m-mediamarkt.png",
    "vatan bilgisayar": "m-vatan.png",
    vatan: "m-vatan.png",
    teknosa: "m-teknosa.png",
    hepsiburada: "m-hepsiburada.png",
    n11: "m-n11.png",
    arcelik: "m-arcelik.png",
    beko: "m-beko.png",
    "d r": "m-dr.png",
    "d&r": "m-dr.png",
    "is bankasi": "fi-isbank.jpg",
    isbank: "fi-isbank.jpg",
    fibabanka: "fi-fibabanka.png",
    "yapi kredi": "fi-yapikredi.jpg",
    yapikredi: "fi-yapikredi.jpg",
    "kuveyt turk": "fi-kuveytturk.png",
    kuveytturk: "fi-kuveytturk.png",
  };

  var BY_CODE = {
    "m-mediamarkt": "m-mediamarkt.png",
    "m-vatan": "m-vatan.png",
    "m-teknosa": "m-teknosa.png",
    "m-hepsiburada": "m-hepsiburada.png",
    "m-n11": "m-n11.png",
    "m-arcelik": "m-arcelik.png",
    "m-beko": "m-beko.png",
    "m-dr": "m-dr.png",
    "fi-isbank": "fi-isbank.jpg",
    "fi-fibabanka": "fi-fibabanka.png",
    "fi-yapikredi": "fi-yapikredi.jpg",
    "fi-kuveytturk": "fi-kuveytturk.png",
  };

  function normalize(name) {
    return String(name || "")
      .toLocaleLowerCase("tr-TR")
      .replace(/ı/g, "i")
      .replace(/İ/g, "i")
      .replace(/ğ/g, "g")
      .replace(/ü/g, "u")
      .replace(/ş/g, "s")
      .replace(/ö/g, "o")
      .replace(/ç/g, "c")
      .replace(/&/g, " ")
      .replace(/[^a-z0-9]+/g, " ")
      .trim()
      .replace(/\s+/g, " ");
  }

  function localSrc(name, code) {
    if (code && BY_CODE[String(code)]) return BASE + BY_CODE[String(code)];
    var key = normalize(name);
    if (BY_NAME[key]) return BASE + BY_NAME[key];
    var compact = key.replace(/\s+/g, "");
    if (BY_NAME[compact]) return BASE + BY_NAME[compact];
    return null;
  }

  function resolveLogo(opts) {
    opts = opts || {};
    if (opts.cdnUrl) return opts.cdnUrl;
    return localSrc(opts.name, opts.code);
  }

  function metaItemHtml(item) {
    var label = escapeHtml(item.label || "");
    var src = item.src;
    if (src) {
      return (
        '<span class="meta-logo" title="' +
        label +
        '"><img src="' +
        escapeHtml(src) +
        '" alt="' +
        label +
        '" loading="lazy" width="20" height="20" /></span>'
      );
    }
    if (!label) return "";
    return (
      '<span class="meta-text" title="' +
      label +
      '">' +
      label +
      "</span>"
    );
  }

  function metaRowHtml(items, stockLabel) {
    var parts = [];
    (items || []).forEach(function (item) {
      var html = metaItemHtml(item);
      if (html) parts.push(html);
    });
    if (stockLabel) {
      parts.push(
        '<span class="meta-pill">' + escapeHtml(stockLabel) + "</span>"
      );
    }
    if (!parts.length) return "";
    return (
      '<div class="deal-meta deal-meta-logos" role="list">' +
      parts.join('<span class="meta-dot" aria-hidden="true"></span>') +
      "</div>"
    );
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  global.TaksitlioEntityLogos = {
    resolve: resolveLogo,
    metaRowHtml: metaRowHtml,
    normalize: normalize,
  };
})(typeof window !== "undefined" ? window : globalThis);
