/** ADR-011 search session client — SSE progress, no fake timers. */
(function (global) {
  "use strict";

  function createSearchSessionClient(baseUrl) {
    var root = (baseUrl || "").replace(/\/$/, "");

    function start(payload) {
      return fetch(root + "/v1/search-sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then(function (r) {
        return r.json();
      });
    }

    function answerClarification(sessionId, payload) {
      return fetch(root + "/v1/search-sessions/" + sessionId + "/clarifications", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then(function (r) {
        return r.json();
      });
    }

    function completeWithCurrent(sessionId) {
      return fetch(
        root + "/v1/search-sessions/" + sessionId + "/complete-with-current-results",
        { method: "POST" }
      ).then(function (r) {
        return r.json();
      });
    }

    function cancel(sessionId) {
      return fetch(root + "/v1/search-sessions/" + sessionId + "/cancel", {
        method: "POST",
      }).then(function (r) {
        return r.json();
      });
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
      es.onerror = function () {
        if (handlers && handlers.onError) handlers.onError();
      };
      return es;
    }

    return {
      start: start,
      answerClarification: answerClarification,
      completeWithCurrent: completeWithCurrent,
      cancel: cancel,
      subscribeEvents: subscribeEvents,
    };
  }

  global.TaksitlioSearchSession = { create: createSearchSessionClient };
})(typeof window !== "undefined" ? window : globalThis);
