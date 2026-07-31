/** Pure helpers for Taksitlio progressive cards (ADR-010 P14). No invented prices. */
(function (global) {
  "use strict";

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatMoney(n, currency) {
    if (n == null || Number.isNaN(Number(n))) return null;
    const cur = currency || "TRY";
    const suffix = cur === "TRY" ? " TL" : ` ${cur}`;
    return `${Number(n).toLocaleString("tr-TR", { maximumFractionDigits: 0 })}${suffix}`;
  }

  function sessionId() {
    const key = "taksitlio_session_id";
    try {
      let id = sessionStorage.getItem(key);
      if (!id) {
        id =
          typeof crypto !== "undefined" && crypto.randomUUID
            ? crypto.randomUUID()
            : `s-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        sessionStorage.setItem(key, id);
      }
      return id;
    } catch (_) {
      return `s-${Date.now()}`;
    }
  }

  /** Map API product card → deal view model. CDN URL only; never invent finance. */
  function cardToDeal(card, index) {
    const imageReady =
      card &&
      card.image &&
      card.image.status === "READY" &&
      card.image.thumbnail_cdn_url;
    const finance = card && card.best_finance ? card.best_finance : null;
    const merchant =
      (card.merchant && card.merchant.display_name) || "";
    const metaParts = [merchant];
    if (finance && finance.institution_display_name) {
      metaParts.push(finance.institution_display_name);
    }
    if (card.stock_status === "AVAILABLE") metaParts.push("Stokta");

    const productPrice = formatMoney(card.price, card.currency) || "—";
    let financeLine = null;
    let hint = null;
    if (finance && finance.monthly_payment != null && finance.term_months != null) {
      const monthly = formatMoney(finance.monthly_payment, card.currency);
      financeLine = monthly
        ? `${monthly} × ${finance.term_months} ay`
        : `${finance.term_months} ay taksit`;
      hint = finance.display_label || "Tahmini aylık ödeme";
    }

    return {
      kind: "product",
      best: index === 0,
      name: card.display_name || "Ürün",
      meta: metaParts.filter(Boolean).join(" · "),
      badge: index === 0 ? "Öne çıkan" : card.ranking_label || "Seçenek",
      primary: productPrice,
      secondary: "Ürün fiyatı",
      financeLine,
      hint,
      img: imageReady ? card.image.thumbnail_cdn_url : null,
      productUrl: card.product_url || null,
    };
  }

  /** Legacy V004 campaign grounding → deal view (API fields only). */
  function campaignToDeal(c, index) {
    const monthly =
      c.monthly_payment != null
        ? formatMoney(c.monthly_payment, c.currency || "TRY")
        : null;
    const price =
      c.list_price != null
        ? formatMoney(c.list_price, c.currency || "TRY")
        : null;
    const tenure = c.installment_count ? `${c.installment_count} ay` : null;
    return {
      kind: "campaign",
      best: index === 0,
      name: c.title || c.product_name || "Kampanya",
      meta: [c.brand, c.summary].filter(Boolean).join(" · ").slice(0, 120),
      badge: index === 0 ? "Öne çıkan" : "Kampanya",
      primary: price || monthly || "—",
      secondary: price ? "liste fiyatı" : "",
      financeLine: monthly && tenure ? `${monthly} / ${tenure}` : monthly,
      hint: monthly ? "Kampanya kaydı (katalog dışı)" : null,
      img: null,
      productUrl: null,
    };
  }

  function dealsFromChatPayload(payload) {
    const cards = (payload && payload.cards) || [];
    if (cards.length) {
      return {
        source: "catalog",
        phase: payload.phase || "FIRST_CARDS",
        deals: cards.map(cardToDeal),
      };
    }
    const campaigns = (payload && payload.campaigns) || [];
    return {
      source: "campaigns",
      phase: null,
      deals: campaigns.map(campaignToDeal),
    };
  }

  function dealArticleHtml(d, i) {
    const badgeOnMedia = d.best
      ? `<span class="deal-badge">Öne çıkan</span>`
      : "";
    const media = d.img
      ? `<div class="deal-media">${badgeOnMedia}<img src="${escapeHtml(d.img)}" alt="" loading="lazy" /></div>`
      : `<div class="deal-media is-empty">${badgeOnMedia}<span class="deal-media-ph">Görsel yok</span></div>`;
    const hint = d.hint
      ? `<span class="hint">${escapeHtml(d.hint)}</span>`
      : "";
    const priceLabel = escapeHtml(d.secondary || "Ürün fiyatı");
    const financeBlock = d.financeLine
      ? `<div class="deal-finance">
            <div class="fl">Taksit seçeneği</div>
            <div class="fn">${escapeHtml(d.financeLine)}</div>
            ${hint}
          </div>`
      : hint;
    return `
      <article class="deal ${d.best ? "best" : ""}" style="animation-delay:${0.06 + i * 0.1}s">
        ${media}
        <div class="deal-body">
          <div class="deal-copy">
            <div class="deal-name">${escapeHtml(d.name)}</div>
            <div class="deal-meta">${escapeHtml(d.meta)}</div>
          </div>
          <div class="deal-price">
            <div class="l">${priceLabel}</div>
            <div class="n">${escapeHtml(d.primary)}</div>
            ${financeBlock}
          </div>
        </div>
      </article>`;
  }

  global.TaksitlioCards = {
    escapeHtml,
    formatMoney,
    sessionId,
    cardToDeal,
    campaignToDeal,
    dealsFromChatPayload,
    dealArticleHtml,
  };
})(typeof window !== "undefined" ? window : globalThis);
