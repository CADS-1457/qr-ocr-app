"use strict";

// Exceptions de planning (fermeture, congé, malade…) et leurs libellés paramétrables.
(() => {
  const api = `${window.AppAuth.API_BASE}/api/agenda`;
  const $ = (selector) => document.querySelector(selector);
  const dialog = $("#exceptionsDialog");
  const form = $("#exceptionForm");
  const errorBox = $("#exceptionError");
  const typeError = $("#exTypeError");
  let types = [];
  let editingId = null;
  let editingTypeId = null;

  async function request(path, options = {}) {
    const response = await fetch(`${api}${path}`, {
      ...options,
      headers: window.AppAuth.headers(options.headers || {}),
    });
    if (response.status === 204) return null;
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const reason = data?.detail || `Erreur HTTP ${response.status}`;
      throw new Error(typeof reason === "string" ? reason : reason.map((item) => item.msg).join(" ; "));
    }
    return data;
  }

  const pad = (value) => String(value).padStart(2, "0");
  const isoDay = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const frDate = (iso) => iso.split("-").reverse().join("/");
  const json = { "Content-Type": "application/json" };

  function fillSelect(select, entries, placeholder) {
    const previous = select.value;
    select.replaceChildren();
    if (placeholder !== null) select.append(new Option(placeholder, ""));
    for (const entry of entries) select.append(new Option(entry.display_name || entry.name, entry.id));
    select.value = [...select.options].some((option) => option.value === previous) ? previous : select.options[0]?.value || "";
  }

  function fillRoomList(rooms) {
    const list = $("#exRoomList");
    list.replaceChildren();
    for (const room of rooms) {
      const label = document.createElement("label");
      label.className = "checkbox";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.value = room.id;
      label.append(box, document.createTextNode(` ${room.display_name || room.name}`));
      list.append(label);
    }
  }
  const roomPicks = () => [...document.querySelectorAll("#exRoomList input:checked")].map((box) => box.value);
  function setRoomPicks(ids) {
    document.querySelectorAll("#exRoomList input").forEach((box) => { box.checked = ids.includes(box.value); });
  }

  function confirmDelete(title, message) {
    return window.confirmAction?.(title, message, "Supprimer", true) ?? Promise.resolve(false);
  }

  function setAllDay(allDay) {
    $("#exAllDay").checked = allDay;
    $("#exTimes").hidden = allDay;
  }

  function resetForm() {
    editingId = null;
    $("#exNote").value = "";
    setRoomPicks([]);
    $("#exDoctor").value = "";
    $("#exStart").value = isoDay(new Date());
    $("#exEnd").value = isoDay(new Date());
    $("#exStartTime").value = "";
    $("#exEndTime").value = "";
    setAllDay(true);
    $("#exceptionFormTitle").textContent = "Nouvelle exception";
    $("#exceptionSubmit").textContent = "Ajouter l’exception";
    $("#exceptionCancelEdit").hidden = true;
    errorBox.textContent = "";
  }

  function resetTypeForm() {
    editingTypeId = null;
    $("#exTypeName").value = "";
    $("#exTypeColor").value = "#64748b";
    $("#exTypeBlocks").checked = true;
    $("#exTypeFormTitle").textContent = "Libellés";
    $("#exTypeSubmit").textContent = "Ajouter le libellé";
    $("#exTypeCancel").hidden = true;
    typeError.textContent = "";
  }

  function row(titleText, detailText, color, actions) {
    const element = document.createElement("div");
    element.className = "block-row";
    const info = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = titleText;
    if (color) title.style.borderLeft = `6px solid ${color}`;
    if (color) title.style.paddingLeft = "8px";
    const detail = document.createElement("span");
    detail.textContent = detailText;
    info.append(title, detail);
    element.append(info, ...actions);
    return element;
  }

  function button(label, className, handler) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className;
    node.textContent = label;
    node.addEventListener("click", handler);
    return node;
  }

  async function loadTypes() {
    types = await request("/exception-types");
    fillSelect($("#exType"), types, null);
    const list = $("#exTypeList");
    list.replaceChildren();
    for (const type of types) {
      list.append(row(
        type.name,
        type.blocks_booking ? "Bloque les rendez-vous" : "Informatif (ne bloque pas)",
        type.color,
        [
          button("Modifier", "secondary", () => {
            resetTypeForm();
            editingTypeId = type.id;
            $("#exTypeName").value = type.name;
            $("#exTypeColor").value = type.color;
            $("#exTypeBlocks").checked = type.blocks_booking;
            $("#exTypeFormTitle").textContent = `Modifier le libellé (${type.name})`;
            $("#exTypeSubmit").textContent = "Enregistrer";
            $("#exTypeCancel").hidden = false;
          }),
          button("Supprimer", "secondary danger", async () => {
            if (!(await confirmDelete("Supprimer le libellé ?", `Le libellé « ${type.name} » sera supprimé.`))) return;
            try {
              await request(`/exception-types/${type.id}`, { method: "DELETE" });
              if (editingTypeId === type.id) resetTypeForm();
              await loadTypes();
              await loadList();
            } catch (error) {
              typeError.textContent = error.message;
            }
          }),
        ],
      ));
    }
  }

  async function loadList() {
    const start = new Date();
    const end = new Date(start.getFullYear() + 1, start.getMonth(), start.getDate());
    const items = await request(`/schedule-exceptions?start=${isoDay(start)}&end=${isoDay(end)}`);
    const list = $("#exceptionList");
    list.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "hint";
      empty.textContent = "Aucune exception à venir.";
      list.append(empty);
    }
    for (const item of items) {
      const period = item.start_date === item.end_date
        ? frDate(item.start_date)
        : `${frDate(item.start_date)} → ${frDate(item.end_date)}`;
      const hours = item.start_time || item.end_time
        ? ` · ${item.start_time || "00:00"}–${item.end_time || "24:00"}`
        : "";
      const scope = [item.room || "Tous les agendas", item.resource].filter(Boolean).join(" · ");
      list.append(row(
        `${item.type} · ${period}${hours}`,
        [scope, item.note].filter(Boolean).join(" — "),
        item.color,
        [
          button("Modifier", "secondary", () => {
            resetForm();
            editingId = item.id;
            $("#exType").value = item.type_id;
            setRoomPicks(item.room_id ? [item.room_id] : []);
            $("#exDoctor").value = item.resource_id || "";
            $("#exStart").value = item.start_date;
            $("#exEnd").value = item.end_date;
            $("#exStartTime").value = item.start_time || "";
            $("#exEndTime").value = item.end_time || "";
            setAllDay(!item.start_time && !item.end_time);
            $("#exNote").value = item.note || "";
            $("#exceptionFormTitle").textContent = "Modifier l’exception";
            $("#exceptionSubmit").textContent = "Enregistrer";
            $("#exceptionCancelEdit").hidden = false;
            dialog.scrollTop = 0;
          }),
          button("Supprimer", "secondary danger", async () => {
            if (!(await confirmDelete("Supprimer l’exception ?", `${item.type} · ${period}`))) return;
            try {
              await request(`/schedule-exceptions/${item.id}`, { method: "DELETE" });
              if (editingId === item.id) resetForm();
              await loadList();
              document.dispatchEvent(new CustomEvent("blocks:changed"));
            } catch (error) {
              errorBox.textContent = error.message;
            }
          }),
        ],
      ));
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.textContent = "";
    errorBox.classList.remove("ok");
    try {
      const wasEditing = Boolean(editingId);
      const targets = roomPicks();
      if (!targets.length) targets.push(null);
      let affected = 0;
      for (const [index, roomId] of targets.entries()) {
        const edit = wasEditing && index === 0;
        const result = await request(edit ? `/schedule-exceptions/${editingId}` : "/schedule-exceptions", {
          method: edit ? "PUT" : "POST",
          headers: json,
          body: JSON.stringify({
            type_id: $("#exType").value,
            room_id: roomId,
            resource_id: $("#exDoctor").value || null,
            start_date: $("#exStart").value,
            end_date: $("#exEnd").value,
            start_time: $("#exAllDay").checked ? null : $("#exStartTime").value || null,
            end_time: $("#exAllDay").checked ? null : $("#exEndTime").value || null,
            note: $("#exNote").value.trim() || null,
          }),
        });
        affected += result.affected_appointments || 0;
      }
      const saved = { affected_appointments: affected };
      resetForm();
      await loadList();
      document.dispatchEvent(new CustomEvent("blocks:changed"));
      errorBox.classList.add("ok");
      errorBox.textContent = (wasEditing ? "Exception modifiée." : "Exception ajoutée.")
        + (saved.affected_appointments
          ? ` Attention : ${saved.affected_appointments} rendez-vous existant(s) sur cette période.`
          : "");
    } catch (error) {
      errorBox.textContent = error.message;
    }
  });

  $("#exTypeSubmit").addEventListener("click", async () => {
    typeError.textContent = "";
    const name = $("#exTypeName").value.trim();
    if (!name) {
      typeError.textContent = "Saisissez un nom de libellé.";
      return;
    }
    try {
      await request(editingTypeId ? `/exception-types/${editingTypeId}` : "/exception-types", {
        method: editingTypeId ? "PUT" : "POST",
        headers: json,
        body: JSON.stringify({ name, color: $("#exTypeColor").value, blocks_booking: $("#exTypeBlocks").checked }),
      });
      resetTypeForm();
      await loadTypes();
      await loadList();
      document.dispatchEvent(new CustomEvent("blocks:changed"));
    } catch (error) {
      typeError.textContent = error.message;
    }
  });

  $("#exTypeCancel").addEventListener("click", resetTypeForm);
  $("#exAllDay").addEventListener("change", () => setAllDay($("#exAllDay").checked));
  $("#exceptionCancelEdit").addEventListener("click", resetForm);

  async function openDialog(day, roomId) {
    try {
      const [rooms, resources] = await Promise.all([request("/rooms"), request("/resources")]);
      fillRoomList(rooms);
      fillSelect($("#exDoctor"), resources.filter((item) => item.kind === "DOCTOR"), "Tous les médecins");
      resetForm();
      resetTypeForm();
      if (day) {
        $("#exStart").value = day;
        $("#exEnd").value = day;
      }
      if (roomId) setRoomPicks([String(roomId)]);
      await loadTypes();
      await loadList();
      dialog.showModal();
    } catch (error) {
      const message = $("#message");
      message.textContent = error.message;
      message.classList.add("error");
    }
  }

  $("#manageExceptions").addEventListener("click", () => openDialog(null));
  document.addEventListener("exceptions:open", (event) => openDialog(event.detail?.day, event.detail?.roomId));
})();
