/** User-facing copy: never leak model/provider names; brand as NanobaseAI. */
(function (global) {
  "use strict";

  var VENDOR_RE = new RegExp(
    [
      "llama\\.?cpp",
      "llamacpp",
      "openai",
      "anthropic",
      "claude",
      "gemini",
      "mistral",
      "qwen[\\w.-]*",
      "chatgpt",
      "gpt-?\\d[\\w.-]*",
      "deepseek",
      "vllm",
      "ollama",
      "hugging\\s*face",
      "huggingface",
      "model\\s*gateway",
      "remote_openai[\\w.-]*",
      "remote_nine[\\w.-]*",
      "nine_b",
      "poc-fast[\\w.-]*",
      "taksitlio-fast-[a-z]",
      "provider_mode",
      "UNDERSTANDING[_\\w]*",
      "deterministic_fallback",
      "ModelGateway(?:Error)?",
      "profile_code",
      "fast[_-]?(?:a|b|c)(?:_|\\b)",
    ].join("|"),
    "gi"
  );

  function sanitize(text) {
    if (text == null || text === "") return text;
    return String(text).replace(VENDOR_RE, "NanobaseAI");
  }

  function errorMessage(err) {
    if (err && err.status === 409) {
      return "Oturum güncellendi; lütfen tekrar dene.";
    }
    if (err && (err.status === 0 || err.status >= 500)) {
      return "NanobaseAI şu an yanıt veremiyor. Lütfen tekrar dene.";
    }
    if (err && err.status === 404) {
      return "NanobaseAI bu aramayı bulamadı. Lütfen tekrar dene.";
    }
    if (err && err.status === 422) {
      return "NanobaseAI isteğini işleyemedi. Lütfen farklı bir şekilde dene.";
    }
    return "NanobaseAI isteğini işlerken bir sorun oluştu. Lütfen tekrar dene.";
  }

  global.TaksitlioPublic = {
    sanitize: sanitize,
    errorMessage: errorMessage,
  };
})(typeof window !== "undefined" ? window : globalThis);
