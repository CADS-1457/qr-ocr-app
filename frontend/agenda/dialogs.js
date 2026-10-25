"use strict";

// Ajoute une croix de fermeture en haut de chaque fenêtre et ferme au clic sur le fond.
document.querySelectorAll("dialog:not(.config-frame-dialog)").forEach((dialog) => {
  const close = document.createElement("button");
  close.type = "button";
  close.className = "dialog-close";
  close.textContent = "×";
  close.title = "Fermer";
  close.setAttribute("aria-label", "Fermer");
  close.addEventListener("click", () => dialog.close());
  dialog.prepend(close);
  dialog.addEventListener("mousedown", (event) => {
    if (event.target === dialog) dialog.close();
  });
});