"use strict";

(() => {
  const link = document.querySelector("#openConfiguration");
  const dialog = document.querySelector("#configFrameDialog");
  const frame = document.querySelector("#configFrame");
  if (!link || !dialog || !frame) return;

  link.addEventListener("click", (event) => {
    event.preventDefault();
    document.querySelector("#agendaMenu")?.removeAttribute("open");
    frame.src = "agenda.html?config=1&embed=1";
    dialog.showModal();
  });

  window.addEventListener("message", (event) => {
    if (event.origin === window.location.origin && event.data?.type === "agenda-config-closed") {
      dialog.close();
    }
  });
  dialog.addEventListener("close", () => {
    frame.removeAttribute("src");
    document.dispatchEvent(new CustomEvent("blocks:changed"));
  });
})();
