"use strict";

// Standardise les boutons « Actualiser » : icône seule, avec un cercle qui tourne pendant le chargement.
(() => {
  const style = document.createElement("style");
  style.textContent = `
    .refresh-icon-btn {
      display: inline-flex !important; align-items: center; justify-content: center;
      width: 44px !important; height: 44px !important; min-width: 44px !important; min-height: 44px !important;
      padding: 0 !important; border: 1px solid #dbe3ec !important; border-radius: 12px !important;
      background: #fff !important; color: #0f4c81 !important; cursor: pointer; box-shadow: none !important;
    }
    .refresh-icon-btn:hover:not(:disabled) { background: #eff6ff !important; border-color: #93c5fd !important; }
    .refresh-icon-btn:disabled { opacity: .55; cursor: not-allowed; }
    .refresh-icon-btn svg { display: block; }
    .refresh-icon-btn.is-loading svg { animation: refresh-spin .8s linear infinite; }
    .refresh-icon-btn.is-loading { opacity: 1 !important; }
    @keyframes refresh-spin { to { transform: rotate(360deg); } }
    @media (prefers-reduced-motion: reduce) { .refresh-icon-btn.is-loading svg { animation-duration: 2s; } }
  `;
  document.head.append(style);

  const ICON = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>';
  const signedIn = () => Boolean(window.AppAuth && window.AppAuth.user && window.AppAuth.user());

  // Un bouton désactivé alors qu'on est connecté signifie qu'un chargement est en cours.
  const sync = (button) => button.classList.toggle("is-loading", button.disabled && signedIn());

  const convert = (button) => {
    if (button.dataset.refreshIcon) return;
    const label = button.textContent.trim();
    button.dataset.refreshIcon = "1";
    button.classList.add("refresh-icon-btn");
    button.title = label;
    button.setAttribute("aria-label", label);
    button.innerHTML = ICON;
    sync(button);
    new MutationObserver(() => sync(button)).observe(button, { attributes: true, attributeFilter: ["disabled"] });
    button.addEventListener("click", () => {
      if (button.disabled) return;
      button.classList.add("is-loading");
      setTimeout(() => sync(button), 700);
    });
  };

  const run = () => {
    document.querySelectorAll("button").forEach((button) => {
      if (button.dataset.refreshIcon || button.querySelector("svg")) return;
      if (/^Actualiser\b/i.test(button.textContent.trim())) convert(button);
    });
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
})();
