/** Wire guest chat to ADR-011 search-sessions (no DEMO offers, no fake bank claims). */
(function (global) {
  "use strict";

  function apiBase() {
    return "";
  }

  function conversationId() {
    const key = "taksitlio_conversation_id";
    try {
      let id = sessionStorage.getItem(key);
      if (!id) {
        id =
          typeof crypto !== "undefined" && crypto.randomUUID
            ? crypto.randomUUID()
            : "00000000-0000-4000-8000-" + String(Date.now()).padStart(12, "0").slice(-12);
        sessionStorage.setItem(key, id);
      }
      return id;
    } catch (_) {
      return "00000000-0000-4000-8000-000000000001";
    }
  }

  function ensurePanels(thread) {
    let host = thread.querySelector("[data-search-ui]");
    if (host) return host;
    host = document.createElement("div");
    host.dataset.searchUi = "1";
    host.innerHTML =
      '<div data-progress></div><div data-chips></div><div data-logos></div>' +
      '<div data-clarification></div><div data-partial></div><div data-controls class="search-controls"></div>';
    thread.appendChild(host);
    return host;
  }

  function renderControls(el, sessionId, client, onDone) {
    if (!el) return;
    el.innerHTML =
      '<button type="button" data-act="show">Mevcut sonuçları göster</button>' +
      '<button type="button" data-act="cancel">Aramayı iptal et</button>';
    el.querySelector('[data-act="show"]').onclick = function () {
      client.completeWithCurrent(sessionId).then(onDone);
    };
    el.querySelector('[data-act="cancel"]').onclick = function () {
      client.cancel(sessionId).then(onDone);
    };
  }

  function productsToCards(snapshot) {
    const products = (snapshot && snapshot.products) || [];
    return products.map(function (p) {
      return {
        product_id: p.product_id,
        display_name: p.display_name,
        merchant: { display_name: p.merchant_display_name },
        price: p.price,
        currency: "TRY",
        stock_status: "AVAILABLE",
        ranking_label: (snapshot && snapshot.label) || "Ön sonuçlar",
        image: {
          status: p.thumbnail_cdn_url ? "READY" : "IMAGE_UNAVAILABLE",
          thumbnail_cdn_url: p.thumbnail_cdn_url || null,
        },
        best_finance: p.best_finance_summary || null,
      };
    });
  }

  function renderResults(thread, payload) {
    const Cards = global.TaksitlioCards;
    if (!Cards) return;
    const cards = productsToCards(payload.results || payload.partial_results || {});
    if (!cards.length) return;
    const deals = Cards.dealsFromChatPayload({ cards: cards, phase: payload.route });
    const wrap = document.createElement("div");
    wrap.className = "showroom";
    const label =
      payload.route === "LLM" || payload.route === "DEGRADED"
        ? "Ön sonuçlar"
        : "Uygun ürünler";
    wrap.innerHTML =
      '<div class="showroom-head"><div class="eyebrow">' +
      Cards.escapeHtml(label) +
      "</div><h2>" +
      deals.deals.length +
      " seçenek</h2></div>" +
      deals.deals.map(Cards.dealArticleHtml).join("");
    thread.appendChild(wrap);
    requestAnimationFrame(function () {
      wrap.querySelectorAll(".deal, .offer").forEach(function (el) {
        el.classList.add("on");
      });
    });
  }

  function attachSearchUi(opts) {
    const thread = opts.thread;
    const botBubble = opts.botBubble;
    const clearTyping = opts.clearTyping;
    const client = global.TaksitlioSearchSession.create(apiBase());
    let es = null;
    let events = [];

    async function runSearch(message) {
      events = [];
      const panels = ensurePanels(thread);
      const started = await client.start({
        conversation_id: conversationId(),
        message: message,
        client_query_id:
          typeof crypto !== "undefined" && crypto.randomUUID
            ? crypto.randomUUID()
            : undefined,
      });
      clearTyping();
      botBubble(started.route === "CLARIFICATION"
        ? (started.clarification && started.clarification.question_text) || started.status
        : started.route === "LLM"
          ? "Tercihlerinizi ürün özellikleriyle eşleştiriyorum..."
          : "Kriterlerinize uygun ürünler hazırlanıyor...");

      if (global.TaksitlioConstraintChips) {
        global.TaksitlioConstraintChips.render(panels.querySelector("[data-chips]"), started.chips || []);
      }
      if (global.TaksitlioLogoProgressRail) {
        global.TaksitlioLogoProgressRail.render(panels.querySelector("[data-logos]"), started.logos || {});
      }
      if (started.clarification && global.TaksitlioClarification) {
        global.TaksitlioClarification.render(
          panels.querySelector("[data-clarification]"),
          started.clarification,
          function (ids) {
            client
              .answerClarification(started.search_session_id, {
                clarification_id: started.clarification.clarification_id,
                selected_option_ids: ids,
                expected_query_version: started.query_version,
              })
              .then(function (ans) {
                panels.querySelector("[data-clarification]").innerHTML = "";
                if (global.TaksitlioConstraintChips) {
                  global.TaksitlioConstraintChips.render(
                    panels.querySelector("[data-chips]"),
                    ans.chips || []
                  );
                }
                botBubble(ans.route === "FAST" ? "Tercihinize göre ürünleri sıraladım." : "Devam ediyorum...");
                renderResults(thread, ans);
              });
          }
        );
      }

      if (started.partial_results || started.results) {
        if (global.TaksitlioProgressiveProducts) {
          global.TaksitlioProgressiveProducts.render(
            panels.querySelector("[data-partial]"),
            started.partial_results || started.results
          );
        }
        renderResults(thread, started);
      }

      if (started.route === "LLM") {
        renderControls(panels.querySelector("[data-controls]"), started.search_session_id, client, function (done) {
          renderResults(thread, done);
        });
      }

      if (es) es.close();
      es = client.subscribeEvents(started.search_session_id, {
        onEvent: function (ev) {
          events.push(ev);
          if (global.TaksitlioSearchProgress) {
            global.TaksitlioSearchProgress.renderTimeline(
              panels.querySelector("[data-progress]"),
              events
            );
          }
        },
      });
      return started;
    }

    return { runSearch: runSearch };
  }

  global.TaksitlioSearchUi = { attach: attachSearchUi };
})(typeof window !== "undefined" ? window : globalThis);
