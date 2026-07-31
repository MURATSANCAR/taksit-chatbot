/** ADR-011 search session client — SSE progress, no fake timers. */
(function (global) {
  "use strict";

  function createSearchSessionClient(baseUrl) {
    var root = (baseUrl || "").replace(/\/$/, "");

    function parseResponse(r) {
      return r.json().then(function (body) {
        if (!r.ok) {
          var detail = body && (body.detail || body.message || body.error);
          var err = new Error(
            typeof detail === "string"
              ? detail
              : detail
                ? JSON.stringify(detail)
                : "http_" + r.status
          );
          err.status = r.status;
          err.body = body;
          throw err;
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
      // Named SSE events (event: TYPE) also arrive as MessageEvent on that type
      [
        "PARTIAL_RESULTS_READY",
        "FINAL_RESULTS_READY",
        "SEARCH_COMPLETED",
        "SEARCH_COMPLETED_DEGRADED",
        "LLM_JOB_COMPLETED",
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
