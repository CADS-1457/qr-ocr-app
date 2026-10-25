"use strict";

(() => {
  const api = `${window.AppAuth.API_BASE}/api/agenda`;
  const dialog = document.querySelector("#followUpDialog");
  const list = document.querySelector("#followUpList");
  const title = document.querySelector("#followUpTitle");
  const dayInput = document.querySelector("#followUpDay");
  const message = document.querySelector("#followUpMessage");
  let current = null;

  const STATES = {
    PLANNED: "Planifié", PRESENT: "Présent", WAITING: "En attente", IN_PROGRESS: "En cours",
    RECOVERY: "Réveil", BILLING: "Facturation", COMPLETED: "Terminé", DISCHARGED: "Sorti",
    CANCELLED: "Annulé", NO_SHOW: "Absent",
  };
  const state = (value) => STATES[value] || value;

  function describe(entry) {
    switch (entry.kind) {
      case "TRANSFER":
        return { icon: "⇄", text: `Transfert : ${entry.from_room || "?"} → ${entry.to_room || "?"}` };
      case "STATE":
        return {
          icon: "●",
          text: entry.old_value && entry.old_value !== entry.new_value
            ? `Statut : ${state(entry.old_value)} → ${state(entry.new_value)}`
            : `Statut : ${state(entry.new_value)}`,
        };
      case "NOTE":
        return { icon: "✎", text: `Note${entry.to_room ? ` (${entry.to_room})` : ""}` };
      case "AUDIT_CREATE":
        return { icon: "+", text: "Rendez-vous créé" };
      case "AUDIT_MOVE":
        return { icon: "↕", text: "Rendez-vous déplacé" };
      case "AUDIT_DELETE":
        return { icon: "×", text: "Rendez-vous supprimé" };
      default:
        return { icon: "•", text: entry.kind };
    }
  }

  async function load() {
    message.textContent = "";
    list.replaceChildren();
    try {
      const response = await fetch(
        `${api}/appointments/${current.id}/day-follow-up?day=${encodeURIComponent(dayInput.value)}`,
        { headers: window.AppAuth.headers() },
      );
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(data?.detail || `Erreur HTTP ${response.status}`);
      if (!data.length) {
        message.textContent = "Aucun événement enregistré pour cette journée.";
        return;
      }
      for (const entry of data) {
        const { icon, text } = describe(entry);
        const item = document.createElement("li");
        const time = document.createElement("time");
        time.textContent = new Date(entry.at).toLocaleTimeString("fr-BE", { hour: "2-digit", minute: "2-digit" });
        const mark = document.createElement("span");
        mark.className = "follow-up-icon";
        mark.textContent = icon;
        const body = document.createElement("div");
        const label = document.createElement("strong");
        label.textContent = text;
        body.append(label);
        const extra = [entry.comment, entry.by_name && `par ${entry.by_name}`].filter(Boolean).join(" · ");
        if (extra) {
          const detail = document.createElement("small");
          detail.textContent = extra;
          body.append(detail);
        }
        item.append(time, mark, body);
        list.append(item);
      }
    } catch (error) {
      message.textContent = error.message;
    }
  }

  window.openDayFollowUp = (event) => {
    current = event;
    const start = new Date(event.starts_at);
    const offset = start.getTimezoneOffset() * 60_000;
    dayInput.value = new Date(start.getTime() - offset).toISOString().slice(0, 10);
    title.textContent = `Suivi de la journée — ${event.patient_name || ""}`;
    dialog.showModal();
    load();
  };

  dayInput.addEventListener("change", load);
})();
