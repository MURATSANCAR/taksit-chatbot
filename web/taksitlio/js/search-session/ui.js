/** Wire guest chat to ADR-011 search-sessions (no DEMO offers, no fake bank claims). */
(function (global) {
  "use strict";

  var SESSION_KEY = "taksitlio_search_session_id";
  var TERMINAL = {
    CANCELLED: 1,
    COMPLETED: 1,
    TIMED_OUT: 1,
    COMPLETED_DEGRADED: 1,
  };

  function apiBase() {
    return "";
  }

  function conversationId() {
    var key = "taksitlio_conversation_id";
    try {
      var id = sessionStorage.getItem(key);
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

  function persistSessionId(id) {
    try {
      if (id) sessionStorage.setItem(SESSION_KEY, id);
      else sessionStorage.removeItem(SESSION_KEY);
    } catch (_) {
      /* ignore */
    }
  }

  function readPersistedSessionId() {
    try {
      return sessionStorage.getItem(SESSION_KEY);
    } catch (_) {
      return null;
    }
  }

  function isTerminalStatus(status) {
    return !!(status && TERMINAL[String(status)]);
  }

  function ensurePanels(thread) {
    var host = thread.querySelector("[data-search-ui]");
    if (host) return host;
    host = document.createElement("div");
    host.dataset.searchUi = "1";
    host.innerHTML =
      '<div data-progress></div><div data-chips></div><div data-logos></div>' +
      '<div data-clarification></div><div data-partial></div>' +
      '<div data-controls class="search-controls"></div>' +
      '<div data-feedback class="search-feedback"></div>';
    thread.appendChild(host);
    return host;
  }

  function productsToCards(snapshot) {
    var products = (snapshot && snapshot.products) || [];
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

  function errorMessage(err) {
    if (!err) return "İsteğini işlerken bir sorun oluştu.";
    if (err.status === 409) return "Oturum güncellendi; lütfen tekrar dene.";
    if (err.message && err.message !== "http_" + err.status) return err.message;
    return "İsteğini işlerken bir sorun oluştu. Lütfen tekrar dene.";
  }

  function attachSearchUi(opts) {
    var thread = opts.thread;
    var botBubble = opts.botBubble;
    var clearTyping = opts.clearTyping;
    var onError = opts.onError || function (msg) {
      if (botBubble) botBubble(msg);
    };
    var client = global.TaksitlioSearchSession.create(apiBase());
    var es = null;
    var events = [];
    var state = {
      searchSessionId: null,
      queryVersion: 0,
      status: null,
      route: null,
      chips: [],
      clarification: null,
      lastProducts: [],
      terminalRefreshDone: false,
    };

    function syncState(payload) {
      if (!payload) return;
      if (payload.search_session_id) {
        state.searchSessionId = payload.search_session_id;
        persistSessionId(payload.search_session_id);
      }
      if (payload.query_version != null) state.queryVersion = payload.query_version;
      if (payload.status) state.status = payload.status;
      if (payload.route) state.route = payload.route;
      if (payload.chips) state.chips = payload.chips;
      if (payload.clarification !== undefined) state.clarification = payload.clarification;
      if (isTerminalStatus(state.status)) {
        persistSessionId(null);
      }
    }

    function isActiveSession() {
      return !!(state.searchSessionId && !isTerminalStatus(state.status));
    }

    function renderControls(el) {
      if (!el) return;
      if (!state.searchSessionId || state.route !== "LLM" || isTerminalStatus(state.status)) {
        el.innerHTML = "";
        return;
      }
      el.innerHTML =
        '<button type="button" data-act="show">Mevcut sonuçları göster</button>' +
        '<button type="button" data-act="cancel">Aramayı iptal et</button>';
      el.querySelector('[data-act="show"]').onclick = function () {
        client
          .completeWithCurrent(state.searchSessionId)
          .then(function (done) {
            applyPayload(done, { announce: true });
          })
          .catch(function (err) {
            onError(errorMessage(err));
          });
      };
      el.querySelector('[data-act="cancel"]').onclick = function () {
        client
          .cancel(state.searchSessionId)
          .then(function (done) {
            syncState(done);
            state.status = done.status || "CANCELLED";
            persistSessionId(null);
            if (es) {
              es.close();
              es = null;
            }
            el.innerHTML = "";
            botBubble("Arama iptal edildi.");
          })
          .catch(function (err) {
            onError(errorMessage(err));
          });
      };
    }

    function renderFeedback(el) {
      if (!el) return;
      if (!state.lastProducts.length || !state.queryVersion) {
        el.innerHTML = "";
        return;
      }
      el.innerHTML =
        '<button type="button" class="feedback-btn" data-act="feedback">' +
        "Bu sonuç yardımcı olmadı</button>";
      el.querySelector('[data-act="feedback"]').onclick = function () {
        var btn = el.querySelector('[data-act="feedback"]');
        if (btn) btn.disabled = true;
        var first = state.lastProducts[0] || {};
        client
          .postFeedback({
            query_version: state.queryVersion,
            parsed_constraints: {},
            selected_product: first.product_id || null,
            error_class: "PRODUCT_IDENTITY_ERROR",
            user_note: "guest_ui:results_not_helpful",
          })
          .then(function () {
            el.innerHTML = '<p class="feedback-thanks">Geri bildirimin için teşekkürler.</p>';
          })
          .catch(function (err) {
            if (btn) btn.disabled = false;
            onError(errorMessage(err));
          });
      };
    }

    function renderResults(payload, optsReplace) {
      var Cards = global.TaksitlioCards;
      if (!Cards) return;
      var snap = (payload && (payload.results || payload.partial_results)) || {};
      var cards = productsToCards(snap);
      if (!cards.length) return;
      state.lastProducts = snap.products || [];

      var existing = thread.querySelector("[data-showroom-live]");
      if (optsReplace && existing) existing.remove();

      var deals = Cards.dealsFromChatPayload({ cards: cards, phase: payload.route });
      var wrap = document.createElement("div");
      wrap.className = "showroom";
      wrap.dataset.showroomLive = "1";
      var label =
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
        wrap.querySelectorAll(".deal, .offer").forEach(function (node) {
          node.classList.add("on");
        });
      });
      var panels = ensurePanels(thread);
      renderFeedback(panels.querySelector("[data-feedback]"));
    }

    function wireChips(panels, chips) {
      if (!global.TaksitlioConstraintChips) return;
      global.TaksitlioConstraintChips.render(
        panels.querySelector("[data-chips]"),
        chips || [],
        function (chipId) {
          if (!state.searchSessionId || !state.queryVersion) return;
          client
            .updateConstraint(state.searchSessionId, {
              action: "DELETE",
              constraint_id: chipId,
              value: null,
              expected_query_version: state.queryVersion,
            })
            .then(function (ans) {
              applyPayload(ans, { announce: true, announceText: "Kısıtı güncelledim." });
            })
            .catch(function (err) {
              onError(errorMessage(err));
            });
        }
      );
    }

    function wireClarification(panels, clarification) {
      var slot = panels.querySelector("[data-clarification]");
      if (!slot) return;
      if (!clarification || !global.TaksitlioClarification) {
        slot.innerHTML = "";
        return;
      }
      global.TaksitlioClarification.render(slot, clarification, function (ids) {
        client
          .answerClarification(state.searchSessionId, {
            clarification_id: clarification.clarification_id,
            selected_option_ids: ids,
            expected_query_version: state.queryVersion,
          })
          .then(function (ans) {
            slot.innerHTML = "";
            applyPayload(ans, {
              announce: true,
              announceText:
                ans.route === "FAST"
                  ? "Tercihinize göre ürünleri sıraladım."
                  : "Devam ediyorum...",
            });
          })
          .catch(function (err) {
            onError(errorMessage(err));
          });
      });
    }

    function subscribe(sessionId, panels) {
      if (es) es.close();
      state.terminalRefreshDone = false;
      events = [];
      es = client.subscribeEvents(sessionId, {
        onEvent: function (ev) {
          events.push(ev);
          if (global.TaksitlioSearchProgress) {
            global.TaksitlioSearchProgress.renderTimeline(
              panels.querySelector("[data-progress]"),
              events
            );
          }
          var type = ev && (ev.type || ev.event_type);
          var terminalTypes = {
            FINAL_RESULTS_READY: 1,
            SEARCH_COMPLETED: 1,
            SEARCH_COMPLETED_DEGRADED: 1,
            LLM_JOB_COMPLETED: 1,
          };
          if (
            terminalTypes[type] &&
            state.route === "LLM" &&
            !state.terminalRefreshDone &&
            state.searchSessionId
          ) {
            state.terminalRefreshDone = true;
            client
              .completeWithCurrent(state.searchSessionId)
              .then(function (done) {
                applyPayload(done, {
                  announce: true,
                  announceText: "Sonuçlar hazır.",
                  replaceResults: true,
                });
              })
              .catch(function () {
                state.terminalRefreshDone = false;
              });
          }
          if (type === "SEARCH_CANCELLED") {
            state.status = "CANCELLED";
            persistSessionId(null);
          }
          var data = (ev && ev.data) || {};
          if (
            type === "PARTIAL_RESULTS_READY" &&
            data.products &&
            global.TaksitlioProgressiveProducts
          ) {
            global.TaksitlioProgressiveProducts.render(
              panels.querySelector("[data-partial]"),
              data
            );
          }
        },
      });
    }

    function applyPayload(payload, options) {
      options = options || {};
      var panels = ensurePanels(thread);
      syncState(payload);
      clearTyping();

      if (options.announce) {
        var text =
          options.announceText ||
          (payload.route === "CLARIFICATION"
            ? (payload.clarification && payload.clarification.question_text) ||
              payload.status
            : payload.route === "LLM"
              ? "Tercihlerinizi ürün özellikleriyle eşleştiriyorum..."
              : "Kriterlerinize uygun ürünler hazırlanıyor...");
        botBubble(text);
      }

      wireChips(panels, payload.chips || state.chips || []);
      if (global.TaksitlioLogoProgressRail) {
        global.TaksitlioLogoProgressRail.render(
          panels.querySelector("[data-logos]"),
          payload.logos || {}
        );
      }
      wireClarification(panels, payload.clarification || null);

      if (payload.partial_results || payload.results) {
        if (global.TaksitlioProgressiveProducts) {
          global.TaksitlioProgressiveProducts.render(
            panels.querySelector("[data-partial]"),
            payload.partial_results || payload.results
          );
        }
        renderResults(payload, options.replaceResults);
      }

      renderControls(panels.querySelector("[data-controls]"));
      if (state.searchSessionId) {
        subscribe(state.searchSessionId, panels);
      }
      return payload;
    }

    async function runSearch(message) {
      var panels = ensurePanels(thread);
      var payload;
      try {
        if (isActiveSession()) {
          payload = await client.supersedeMessage(state.searchSessionId, {
            message: message,
          });
        } else {
          state.searchSessionId = null;
          state.queryVersion = 0;
          state.status = null;
          state.route = null;
          state.lastProducts = [];
          state.terminalRefreshDone = false;
          payload = await client.start({
            conversation_id: conversationId(),
            message: message,
            client_query_id:
              typeof crypto !== "undefined" && crypto.randomUUID
                ? crypto.randomUUID()
                : undefined,
          });
        }
      } catch (err) {
        clearTyping();
        if (err && err.status === 404 && state.searchSessionId) {
          persistSessionId(null);
          state.searchSessionId = null;
          state.status = null;
          payload = await client.start({
            conversation_id: conversationId(),
            message: message,
            client_query_id:
              typeof crypto !== "undefined" && crypto.randomUUID
                ? crypto.randomUUID()
                : undefined,
          });
        } else {
          throw err;
        }
      }
      return applyPayload(payload, { announce: true, replaceResults: true });
    }

    async function hydratePersistedSession() {
      var id = readPersistedSessionId();
      if (!id) return null;
      try {
        var snap = await client.getSession(id);
        if (isTerminalStatus(snap.status)) {
          persistSessionId(null);
          return null;
        }
        state.searchSessionId = snap.search_session_id;
        state.queryVersion = snap.query_version;
        state.status = snap.status;
        var panels = ensurePanels(thread);
        subscribe(id, panels);
        return snap;
      } catch (_) {
        persistSessionId(null);
        return null;
      }
    }

    hydratePersistedSession();

    return {
      runSearch: runSearch,
      isActiveSession: isActiveSession,
      getState: function () {
        return {
          searchSessionId: state.searchSessionId,
          queryVersion: state.queryVersion,
          status: state.status,
          route: state.route,
        };
      },
    };
  }

  global.TaksitlioSearchUi = { attach: attachSearchUi };
})(typeof window !== "undefined" ? window : globalThis);
