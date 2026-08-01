/** ADR-011 search session client — SSE progress, no fake timers. */
(function (global) {
  "use strict";

  function createSearchSessionClient(baseUrl) {
    var root = (baseUrl || "").replace(/\/$/, "");

    function parseResponse(r) {
      return r.text().then(function (raw) {
        var body = null;
        if (raw) {
          try {
            body = JSON.parse(raw);
          } catch (e) {
            body = { detail: raw.slice(0, 200) };
          }
        }
        if (!r.ok) {
          // Keep raw detail only for console/debug; UI must use TaksitlioPublic.errorMessage.
          var detail = body && (body.detail || body.message || body.error);
          var err = new Error("http_" + r.status);
          err.status = r.status;
          err.body = body;
          err.detail = detail;
          throw err;
        }
        if (body == null || typeof body !== "object") {
          var bad = new Error("http_" + r.status + "_invalid_json");
          bad.status = r.status || 500;
          bad.body = body;
          throw bad;
        }
        return body;
      });
    }

    function postJson(path, payload) {
      return fetch(root + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      }).then(parseResponse);
    }

    function postEmpty(path) {
      return fetch(root + path, { method: "POST" }).then(parseResponse);
    }

    function start(payload) {
      return postJson("/v1/search-sessions", payload);
    }

    function answerClarification(sessionId, payload) {
      return postJson("/v1/search-sessions/" + sessionId + "/clarifications", payload);
    }

    function updateConstraint(sessionId, payload) {
      return postJson("/v1/search-sessions/" + sessionId + "/constraints", payload);
    }

    function supersedeMessage(sessionId, payload) {
      return postJson("/v1/search-sessions/" + sessionId + "/messages", payload);
    }

    function completeWithCurrent(sessionId) {
      return postEmpty(
        "/v1/search-sessions/" + sessionId + "/complete-with-current-results"
      );
    }

    function cancel(sessionId) {
      return postEmpty("/v1/search-sessions/" + sessionId + "/cancel");
    }

    function getSession(sessionId) {
      return fetch(root + "/v1/search-sessions/" + sessionId).then(parseResponse);
    }

    function postFeedback(payload) {
      return postJson("/v1/feedback", payload);
    }

    function subscribeEvents(sessionId, handlers) {
      var url = root + "/v1/search-sessions/" + sessionId + "/events";
      var es = new EventSource(url);
      es.onmessage = function (ev) {
        try {
          var data = JSON.parse(ev.data);
          if (handlers && handlers.onEvent) handlers.onEvent(data);
        } catch (e) {
          /* ignore malformed */
        }
      };
      // Backend sends `event: <TYPE>`; those do not hit onmessage.
      [
        "SEARCH_ACCEPTED",
        "FAST_PARSE_STARTED",
        "FAST_PARSE_COMPLETED",
        "ENTITY_RESOLUTION_STARTED",
        "ENTITY_RESOLUTION_COMPLETED",
        "GAP_ANALYSIS_COMPLETED",
        "CLARIFICATION_REQUIRED",
        "CLARIFICATION_ANSWERED",
        "LLM_JOB_QUEUED",
        "LLM_JOB_STARTED",
        "PRODUCT_POOL_SEARCH_STARTED",
        "PRODUCT_POOL_PARTIAL_READY",
        "MERCHANT_CANDIDATES_RESOLVED",
        "BRAND_CANDIDATES_RESOLVED",
        "FINANCE_SEARCH_STARTED",
        "FINANCIAL_INSTITUTION_CANDIDATES_FOUND",
        "PAYMENT_PLAN_CALCULATION_STARTED",
        "PARTIAL_RESULTS_READY",
        "LLM_JOB_COMPLETED",
        "LLM_JOB_TIMED_OUT",
        "RANKING_STARTED",
        "FINAL_RESULTS_READY",
        "SEARCH_COMPLETED",
        "SEARCH_COMPLETED_DEGRADED",
        "SEARCH_FAILED",
        "SEARCH_CANCELLED",
      ].forEach(function (type) {
        es.addEventListener(type, function (ev) {
          try {
            var data = JSON.parse(ev.data);
            if (handlers && handlers.onEvent) handlers.onEvent(data);
          } catch (e) {
            /* ignore */
          }
        });
      });
      es.onerror = function () {
        if (handlers && handlers.onError) handlers.onError();
      };
      return es;
    }

    return {
      start: start,
      answerClarification: answerClarification,
      updateConstraint: updateConstraint,
      supersedeMessage: supersedeMessage,
      completeWithCurrent: completeWithCurrent,
      cancel: cancel,
      getSession: getSession,
      postFeedback: postFeedback,
      subscribeEvents: subscribeEvents,
    };
  }

  global.TaksitlioSearchSession = { create: createSearchSessionClient };
})(typeof window !== "undefined" ? window : globalThis);
