/** Constraint chips — render plan chips (Şart / Tercih / Hariç / Bütçe). */
(function (global) {
  "use strict";

  function chipClass(kind) {
    switch (String(kind || "")) {
      case "required":
        return "chip chip-required";
      case "preference":
        return "chip chip-prefer";
      case "excluded":
        return "chip chip-exclude";
      case "budget":
      case "stretch_budget":
        return "chip chip-budget";
      case "campaign":
        return "chip chip-campaign";
      case "unsupported":
        return "chip chip-unsupported";
      default:
        return "chip";
    }
  }

  function renderChips(container, chips) {
    if (!container) return;
    container.innerHTML = "";
    const list = Array.isArray(chips) ? chips : [];
    list.forEach(function (chip) {
      if (!chip || !chip.label) return;
      const el = document.createElement("button");
      el.type = "button";
      el.className = chipClass(chip.kind);
      el.textContent = String(chip.label);
      if (chip.constraint_id) el.dataset.constraintId = String(chip.constraint_id);
      container.appendChild(el);
    });
  }

  global.TaksitlioConstraintChips = { render: renderChips };
})(typeof window !== "undefined" ? window : globalThis);
