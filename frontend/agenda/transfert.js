"use strict";

// Transfert d'un patient depuis le calendrier, sans quitter la vue agenda.
(() => {
  const api = `${window.AppAuth.API_BASE}/api/agenda`;
  const $ = (selector) => document.querySelector(selector);
  const dialog = $("#transferDialog");
  const errorBox = $("#transferError");
  let appointmentId = null;
  let locations = [];

  async function request(path, options = {}) {
    const response = await fetch(`${api}${path}`, {
      ...options,
      headers: window.AppAuth.headers(options.headers || {}),
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const reason = data?.detail || `Erreur HTTP ${response.status}`;
      throw new Error(
        typeof reason === "string" ? reason : reason.map((item) => item.msg).join(" ; "),
      );
    }
    return data;
  }

  function fill(select, entries, placeholder) {
    select.replaceChildren(new Option(placeholder, ""));
    for (const entry of entries) select.append(new Option(entry.display_name || entry.name, entry.id));
  }

  function renderStock(units) {
    const container = $("#trConsumptions");
    container.replaceChildren();
    if (!units.length) {
      const empty = document.createElement("p");
      empty.textContent = "Aucune unité de stock disponible.";
      container.append(empty);
      return;
    }
    for (const unit of units) {
      const row = document.createElement("div");
      row.className = "stock-consumption-row";
      const use = document.createElement("input");
      use.type = "checkbox";
      use.className = "stock-consumption-use";
      use.value = unit.id;
      const label = document.createElement("label");
      label.className = "stock-consumption-label";
      const details = [
        `${unit.item_name} — ${unit.location_name}`,
        unit.lot_number ? `lot ${unit.lot_number}` : "",
        unit.serial_number ? `série ${unit.serial_number}` : "",
        `disponible : ${unit.available_quantity}`,
      ].filter(Boolean);
      label.append(use, document.createTextNode(details.join(" · ")));
      const quantity = document.createElement("input");
      quantity.type = "number";
      quantity.className = "stock-consumption-quantity";
      quantity.min = "0.001";
      quantity.max = String(unit.available_quantity);
      quantity.step = "0.001";
      quantity.value = String(Math.min(1, Number(unit.available_quantity)));
      quantity.disabled = true;
      use.addEventListener("change", () => {
        quantity.disabled = !use.checked;
      });
      row.append(label, quantity);
      container.append(row);
    }
  }

  async function loadReferences() {
    const [rooms, types, units] = await Promise.all([
      request("/rooms"),
      request("/appointment-types"),
      request("/stock-units"),
    ]);
    locations = rooms;
    fill($("#trLocation"), rooms, "Choisir une salle");
    fill($("#trFollowUpType"), types, "Choisir un type");
    renderStock(units);
  }

  const STATES = {
    PLANNED: "Planifié", PRESENT: "Présent", WAITING: "En attente", IN_PROGRESS: "En cours",
    RECOVERY: "Réveil", BILLING: "Facturation", COMPLETED: "Terminé", DISCHARGED: "Sorti",
  };
  const localDay = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;

  async function loadMovements() {
    const list = $("#trMovements");
    const day = $("#trMovesDay").value;
    list.replaceChildren();
    try {
      const rows = await request(
        `/appointments/${encodeURIComponent(appointmentId)}/movements?day=${encodeURIComponent(day)}`,
      );
      if (!rows.length) {
        const empty = document.createElement("li");
        empty.className = "movement-empty";
        empty.textContent = "Aucun mouvement enregistré pour cette journée.";
        list.append(empty);
        return;
      }
      for (const row of rows) {
        const item = document.createElement("li");
        const time = new Date(row.at).toLocaleTimeString("fr-BE", { hour: "2-digit", minute: "2-digit" });
        const route = `${row.from_room || "—"} → ${row.to_room || "—"}`;
        const state = row.state ? ` · ${STATES[row.state] || row.state}` : "";
        const strong = document.createElement("strong");
        strong.textContent = time;
        item.append(strong, document.createTextNode(` ${route}${state}`));
        const meta = [row.by_name && `par ${row.by_name}`, row.comment].filter(Boolean).join(" · ");
        if (meta) {
          const small = document.createElement("small");
          small.textContent = meta;
          item.append(document.createElement("br"), small);
        }
        list.append(item);
      }
    } catch (error) {
      const failed = document.createElement("li");
      failed.className = "movement-empty";
      failed.textContent = error.message;
      list.append(failed);
    }
  }

  window.openTransferDialog = async (event) => {
    errorBox.textContent = "";
    try {
      await loadReferences();
    } catch (error) {
      window.alert(error.message);
      return;
    }
    appointmentId = event.id;
    $("#transferWho").textContent = event.patient_name || "";
    $("#trLocation").value = event.room_id || "";
    $("#trState").value = locations.find((room) => room.id === event.room_id)?.default_state
      || ($("#trState").querySelector(`[value="${event.state}"]`) ? event.state : "PRESENT");
    for (const id of ["#trHandover", "#trRecovery", "#trDischarge", "#trFollowUpDate"]) $(id).value = "";
    $("#trFollowup").checked = false;
    $("#trFollowUpTypeField").hidden = true;
    $("#trFollowUpDateField").hidden = true;
    dialog.showModal();
    $("#trMovesDay").value = localDay(new Date());
    loadMovements();
  };

  $("#trMovesDay").addEventListener("change", loadMovements);

  $("#trLocation").addEventListener("change", (change) => {
    const room = locations.find((entry) => entry.id === change.target.value);
    if (room?.default_state) $("#trState").value = room.default_state;
  });

  $("#trFollowup").addEventListener("change", (change) => {
    $("#trFollowUpTypeField").hidden = !change.target.checked;
    $("#trFollowUpDateField").hidden = !change.target.checked;
    $("#trFollowUpType").required = change.target.checked;
  });

  $("#trCancel").addEventListener("click", () => dialog.close());

  $("#transferForm").addEventListener("submit", async (submit) => {
    submit.preventDefault();
    const followUp = $("#trFollowup").checked;
    const payload = {
      to_location_id: $("#trLocation").value,
      start_now: $("#trStartNow").checked,
      new_state: $("#trState").value,
      handover_comment: $("#trHandover").value.trim() || null,
      recovery_comment: $("#trRecovery").value.trim() || null,
      discharge_instructions: $("#trDischarge").value.trim() || null,
      follow_up: {
        required: followUp,
        appointment_type_id: followUp ? $("#trFollowUpType").value : null,
        target_date: followUp ? $("#trFollowUpDate").value || null : null,
      },
      consumptions: [...document.querySelectorAll("#trConsumptions .stock-consumption-row")]
        .filter((row) => row.querySelector(".stock-consumption-use").checked)
        .map((row) => ({
          stock_unit_id: row.querySelector(".stock-consumption-use").value,
          quantity: row.querySelector(".stock-consumption-quantity").value,
        })),
    };
    try {
      await request(`/appointments/${encodeURIComponent(appointmentId)}/transfer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      dialog.close();
      document.dispatchEvent(new CustomEvent("blocks:changed"));
      const message = $("#message");
      if (message) message.textContent = "Transfert enregistré.";
    } catch (error) {
      errorBox.textContent = error.message;
    }
  });

})();
