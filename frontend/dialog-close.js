"use strict";

// Ajoute une croix de fermeture en haut à droite de chaque fenêtre (<dialog>) des pages hors agenda.
(() => {
  const style = document.createElement("style");
  style.textContent = `
    dialog .dialog-close {
      position: sticky; top: 0; z-index: 5; display: flex !important; align-items: center; justify-content: center;
      width: 36px !important; height: 36px !important; min-height: 36px !important;
      margin: -8px -8px -28px auto !important; padding: 0 !important;
      border: 1px solid #dbe3ec !important; border-radius: 50% !important;
      background: #fff !important; color: #0f4c81 !important;
      font-size: 24px !important; line-height: 1 !important; cursor: pointer; box-shadow: none !important;
    }
    dialog .dialog-close:hover { background: #eef2f7 !important; }
  `;
  document.head.append(style);

  document.querySelectorAll("dialog").forEach((dialog) => {
    if (dialog.querySelector(".dialog-close")) return;
    const close = document.createElement("button");
    close.type = "button";
    close.className = "dialog-close";
    close.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';
    close.title = "Fermer";
    close.setAttribute("aria-label", "Fermer");
    close.addEventListener("click", () => {
      const cancel = new Event("cancel", { cancelable: true });
      dialog.dispatchEvent(cancel);
      if (!cancel.defaultPrevented) dialog.close();
    });
    dialog.prepend(close);
  });
})();
