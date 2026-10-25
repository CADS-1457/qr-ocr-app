"use strict";

// Bloque l'affichage du module Agenda pour les comptes sans droit (activé par le SuperAdmin).
(() => {
  let denied = false;

  function denyAccess() {
    const main = document.querySelector("main");
    if (!main || denied) return;
    denied = true;
    const keep = "#loginForm, #authBar, #authStatus";
    for (const child of main.children) {
      if (!child.matches(keep) && !child.querySelector(keep)) child.style.display = "none";
    }
    const box = document.createElement("section");
    box.className = "calendar-card";
    box.style.padding = "24px";
    const title = document.createElement("h1");
    title.textContent = "Module Agenda non activé";
    const text = document.createElement("p");
    text.textContent =
      "Votre compte n’a pas accès au module Agenda. Demandez son activation au SuperAdmin.";
    box.append(title, text);
    main.append(box);
  }

  async function check() {
    if (!window.AppAuth.token()) return;
    try {
      const user = await window.AppAuth.refreshUser();
      if (user && !window.AppAuth.canUseAgenda(user)) denyAccess();
    } catch {
      /* la page affichera elle-même l'erreur de connexion */
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (window.AppAuth.token() && !window.AppAuth.canUseAgenda()) denyAccess();
    check();
  });
})();
