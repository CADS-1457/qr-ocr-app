"use strict";

(() => {
  const api = `${window.AppAuth.API_BASE}/api/agenda`;
  const DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
  const dialog = document.querySelector("#blocksDialog");
  const form = document.querySelector("#blockForm");
  const errorBox = document.querySelector("#blockError");
  const list = document.querySelector("#blockList");
  let editingId = null;
  let blocks = [];
  let roomList = [];

  const overRoom = document.querySelector("#overRoom");
  const overMax = document.querySelector("#overMax");
  const overMessage = document.querySelector("#overMessage");

  function syncOverbooking() {
    const room = roomList.find((entry) => entry.id === overRoom.value);
    overMax.value = String(room?.max_parallel || 1);
    overMessage.textContent = "";
  }

  overRoom.addEventListener("change", syncOverbooking);
  document.querySelector("#overSave").addEventListener("click", async () => {
    overMessage.classList.remove("ok");
    const value = Number(overMax.value);
    try {
      if (!overRoom.value) throw new Error("Choisissez une salle.");
      if (!Number.isInteger(value) || value < 1 || value > 20) {
        throw new Error("Indiquez un nombre entre 1 et 20.");
      }
      await request(`/rooms/${overRoom.value}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ max_parallel: value }),
      });
      roomList.find((entry) => entry.id === overRoom.value).max_parallel = value;
      overMessage.classList.add("ok");
      overMessage.textContent =
        value === 1 ? "Surréservation désactivée." : `${value} rendez-vous simultanés autorisés.`;
      document.dispatchEvent(new CustomEvent("blocks:changed"));
    } catch (error) {
      overMessage.textContent = error.message;
    }
  });

  const stateRoom = document.querySelector("#stateRoom");
  const stateDefault = document.querySelector("#stateDefault");
  const stateMessage = document.querySelector("#stateMessage");

  function syncDefaultState() {
    const room = roomList.find((entry) => entry.id === stateRoom.value);
    stateDefault.value = room?.default_state || "";
    stateMessage.textContent = "";
  }

  stateRoom.addEventListener("change", syncDefaultState);
  document.querySelector("#stateSave").addEventListener("click", async () => {
    stateMessage.classList.remove("ok");
    try {
      if (!stateRoom.value) throw new Error("Choisissez une salle.");
      await request(`/rooms/${stateRoom.value}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ default_state: stateDefault.value }),
      });
      roomList.find((entry) => entry.id === stateRoom.value).default_state = stateDefault.value || null;
      stateMessage.classList.add("ok");
      stateMessage.textContent = stateDefault.value
        ? "Statut par défaut enregistré."
        : "Statut par défaut supprimé.";
    } catch (error) {
      stateMessage.textContent = error.message;
    }
  });

  const showRoom = document.querySelector("#doctorShowRoom");
  const showToggle = document.querySelector("#doctorShowToggle");
  const showMessage = document.querySelector("#doctorShowMessage");
  const syncShowDoctor = () => {
    showToggle.checked = Boolean(roomList.find((entry) => entry.id === showRoom.value)?.show_doctor);
    showMessage.textContent = "";
  };
  showRoom.addEventListener("change", syncShowDoctor);
  document.querySelector("#doctorShowSave").addEventListener("click", async () => {
    showMessage.classList.remove("ok");
    try {
      if (!showRoom.value) throw new Error("Choisissez une salle.");
      await request(`/rooms/${showRoom.value}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ show_doctor: showToggle.checked }),
      });
      roomList.find((entry) => entry.id === showRoom.value).show_doctor = showToggle.checked;
      showMessage.classList.add("ok");
      showMessage.textContent = showToggle.checked ? "Le médecin sera affiché." : "Affichage du médecin désactivé.";
      document.dispatchEvent(new CustomEvent("blocks:changed"));
    } catch (error) {
      showMessage.textContent = error.message;
    }
  });

  const editRoom = document.querySelector("#editRoom");
  const editMessage = document.querySelector("#editRoomMessage");

  function syncEditRoom() {
    const room = roomList.find((entry) => entry.id === editRoom.value);
    document.querySelector("#editName").value = room?.name || "";
    document.querySelector("#editKind").value = room?.kind || "OTHER";
    document.querySelector("#editColor").value = (room?.color || "#64748b").toLowerCase();
    document.querySelector("#editCapacity").value = room?.capacity || 1;
    editMessage.textContent = "";
  }

  editRoom.addEventListener("change", syncEditRoom);
  document.querySelector("#editRoomSave").addEventListener("click", async () => {
    editMessage.classList.remove("ok");
    try {
      if (!editRoom.value) throw new Error("Choisissez une salle.");
      const body = {
        name: document.querySelector("#editName").value.trim(),
        kind: document.querySelector("#editKind").value,
        color: document.querySelector("#editColor").value,
        capacity: Number(document.querySelector("#editCapacity").value),
      };
      if (!body.name) throw new Error("Le nom de la salle est obligatoire.");
      await request(`/rooms/${editRoom.value}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      editMessage.classList.add("ok");
      editMessage.textContent = "Salle modifiée.";
      document.dispatchEvent(new CustomEvent("rooms:changed"));
      await openDialog(editRoom.value);
    } catch (error) {
      editMessage.textContent = error.message;
    }
  });

  async function request(path, options = {}) {
    const response = await fetch(`${api}${path}`, {
      ...options,
      headers: window.AppAuth.headers(options.headers || {}),
    });
    if (response.status === 204) return null;
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const reason = data?.detail || `Erreur HTTP ${response.status}`;
      throw new Error(
        typeof reason === "string" ? reason : reason.map((item) => item.msg).join(" ; "),
      );
    }
    return data;
  }

  function fillSelect(select, entries, placeholder, selected = "") {
    select.replaceChildren(new Option(placeholder, ""));
    for (const entry of entries) {
      select.append(new Option(entry.display_name || entry.name, entry.id));
    }
    select.value = selected;
  }

  const checkboxes = () => [...form.querySelectorAll('input[name="weekday"]')];

  const PALETTE = [
    ["#2563EB", "Bleu"], ["#0EA5E9", "Ciel"], ["#14B8A6", "Turquoise"], ["#16A34A", "Vert"],
    ["#84CC16", "Citron vert"], ["#EAB308", "Jaune"], ["#F97316", "Orange"], ["#DC2626", "Rouge"],
    ["#DB2777", "Rose"], ["#9333EA", "Violet"], ["#6366F1", "Indigo"], ["#78716C", "Gris"],
  ];
  const palette = document.querySelector("#blockPalette");
  const customColor = document.querySelector("#blockColor");
  let selectedColor = null;

  function setColor(color) {
    selectedColor = color ? color.toUpperCase() : null;
    for (const swatch of palette.querySelectorAll("button")) {
      const active = (swatch.dataset.color || null) === selectedColor;
      swatch.classList.toggle("selected", active);
      swatch.setAttribute("aria-pressed", String(active));
    }
    const isPreset = !selectedColor || PALETTE.some(([value]) => value === selectedColor);
    document.querySelector("#blockColorCustom").classList.toggle("selected", !isPreset);
    if (selectedColor) customColor.value = selectedColor.toLowerCase();
    document.querySelector("#blockColorName").textContent = selectedColor
      ? (PALETTE.find(([value]) => value === selectedColor)?.[1] || selectedColor)
      : "Couleur de la salle";
  }

  function buildPalette() {
    const auto = document.createElement("button");
    auto.type = "button";
    auto.className = "swatch swatch-auto";
    auto.title = "Couleur de la salle";
    auto.setAttribute("aria-label", "Couleur de la salle");
    auto.textContent = "S";
    auto.addEventListener("click", () => setColor(null));
    palette.append(auto);
    for (const [value, name] of PALETTE) {
      const swatch = document.createElement("button");
      swatch.type = "button";
      swatch.className = "swatch";
      swatch.dataset.color = value;
      swatch.title = name;
      swatch.setAttribute("aria-label", name);
      swatch.style.background = value;
      swatch.addEventListener("click", () => setColor(value));
      palette.append(swatch);
    }
    customColor.addEventListener("input", () => setColor(customColor.value));
  }
  function resetForm() {
    editingId = null;
    form.querySelector("fieldset").classList.remove("is-editing");
    document.querySelector("#blockFormTitle").textContent = "Nouvelle plage";
    document.querySelector("#blockSubmit").textContent = "Ajouter la plage";
    document.querySelector("#blockCancelEdit").hidden = true;
    document.querySelector("#blockActiveField").hidden = true;
    document.querySelector("#blockRoom").disabled = false;
    for (const box of checkboxes()) {
      box.checked = false;
      box.disabled = false;
    }
    for (const id of ["#blockStart", "#blockEnd", "#blockLabel", "#blockFrom", "#blockTo"]) {
      document.querySelector(id).value = "";
    }
    document.querySelector("#blockSlot").value = "30";
    document.querySelector("#blockCount").value = "";
    document.querySelector("#blockActive").checked = true;
    setColor(null);
    document.querySelector("#blockResource").value = "";
    errorBox.textContent = "";
  }

  function startEdit(block) {
    resetForm();
    editingId = block.id;
    document.querySelector("#blockFormTitle").textContent = `Modifier la plage (${block.room})`;
    document.querySelector("#blockSubmit").textContent = "Enregistrer";
    document.querySelector("#blockCancelEdit").hidden = false;
    document.querySelector("#blockActiveField").hidden = false;
    document.querySelector("#blockRoom").value = block.room_id;
    document.querySelector("#blockRoom").disabled = true;
    document.querySelector("#blockResource").value = block.resource_id || "";
    for (const box of checkboxes()) {
      box.checked = Number(box.value) === block.weekday;
      box.disabled = true;
    }
    document.querySelector("#blockStart").value = block.start_time;
    document.querySelector("#blockEnd").value = block.end_time;
    document.querySelector("#blockSlot").value = String(block.slot_minutes);
    syncCount();
    document.querySelector("#blockLabel").value = block.label || "";
    document.querySelector("#blockFrom").value = block.valid_from || "";
    document.querySelector("#blockTo").value = block.valid_to || "";
    document.querySelector("#blockActive").checked = block.active;
    setColor(block.color || null);
    dialog.scrollTop = 0;
  }

  function renderList() {
    list.replaceChildren();
    if (!blocks.length) {
      const empty = document.createElement("p");
      empty.className = "hint";
      empty.textContent = "Aucune plage : les salles concernées sont librement réservables.";
      list.append(empty);
      return;
    }
    for (const block of blocks) {
      const row = document.createElement("div");
      row.className = `block-row${block.active ? "" : " inactive"}`;
      const info = document.createElement("div");
      const swatch = document.createElement("span");
      swatch.className = "color-dot";
      swatch.style.background = block.color || block.room_color || "#64748B";
      const title = document.createElement("strong");
      title.textContent = `${block.room} · ${DAYS[block.weekday]} ${block.start_time} – ${block.end_time}`;
      const detail = document.createElement("span");
      const parts = [
        `pas de ${block.slot_minutes} min`,
        block.resource ? `agenda ${block.resource}` : "tous agendas",
        block.label,
        block.valid_from || block.valid_to
          ? `du ${block.valid_from || "…"} au ${block.valid_to || "…"}`
          : null,
        block.active ? null : "inactive",
      ].filter(Boolean);
      detail.textContent = parts.join(" · ");
      title.prepend(swatch);
      info.append(title, detail);
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "secondary";
      edit.textContent = "Modifier";
      edit.addEventListener("click", () => startEdit(block));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "secondary danger";
      remove.textContent = "Supprimer";
      remove.addEventListener("click", async () => {
        if (!window.confirm("Supprimer cette plage ?")) return;
        try {
          await request(`/scheduling-blocks/${block.id}`, { method: "DELETE" });
          if (editingId === block.id) resetForm();
          await refresh();
        } catch (error) {
          errorBox.textContent = error.message;
        }
      });
      row.append(info, edit, remove);
      list.append(row);
    }
  }

  async function refresh() {
    const room = document.querySelector("#blockRoomFilter").value;
    blocks = await request(`/scheduling-blocks${room ? `?room_id=${encodeURIComponent(room)}` : ""}`);
    renderList();
    document.dispatchEvent(new CustomEvent("blocks:changed"));
  }

  async function openDialog(keepRoom) {
    try {
      const [rooms, resources] = await Promise.all([request("/rooms"), request("/resources")]);
      fillSelect(document.querySelector("#blockRoom"), rooms, "Choisir une salle");
      fillSelect(
        document.querySelector("#blockResource"),
        resources.filter((resource) => resource.kind === "DOCTOR"),
        "Tous les agendas",
      );
      fillSelect(document.querySelector("#blockRoomFilter"), rooms, "Toutes les salles");
      roomList = rooms;
      fillSelect(overRoom, rooms, "Choisir une salle");
      overRoom.value = document.querySelector("#roomFilter").value;
      syncOverbooking();
      fillSelect(stateRoom, rooms, "Choisir une salle");
      stateRoom.value = document.querySelector("#roomFilter").value;
      syncDefaultState();
      fillSelect(showRoom, rooms, "Choisir une salle");
      showRoom.value = document.querySelector("#roomFilter").value;
      syncShowDoctor();
      fillSelect(editRoom, rooms, "Choisir une salle");
      editRoom.value = typeof keepRoom === "string" ? keepRoom : document.querySelector("#roomFilter").value;
      syncEditRoom();
      const preselected = document.querySelector("#roomFilter").value;
      document.querySelector("#blockRoomFilter").value = preselected;
      resetForm();
      if (preselected) document.querySelector("#blockRoom").value = preselected;
      blocks = await request(
        `/scheduling-blocks${preselected ? `?room_id=${encodeURIComponent(preselected)}` : ""}`,
      );
      renderList();
      if (!dialog.open) dialog.showModal();
    } catch (error) {
      document.querySelector("#message").textContent = error.message;
      document.querySelector("#message").classList.add("error");
    }
  }

  const toMinutes = (hhmm) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));
  const toHHMM = (minutes) =>
    `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
  const overlapsValidity = (block, from, to) =>
    (!block.valid_to || !from || block.valid_to >= from) &&
    (!block.valid_from || !to || block.valid_from <= to);

  // Ajuste l'heure de fin pour obtenir un nombre entier de créneaux,
  // sans empiéter sur les plages voisines de la même salle et du même agenda.
  async function fitEnd() {
    const startValue = document.querySelector("#blockStart").value;
    const endValue = document.querySelector("#blockEnd").value;
    const step = Number(document.querySelector("#blockSlot").value);
    const room = document.querySelector("#blockRoom").value;
    if (!startValue || !endValue || !room || !Number.isInteger(step) || step < 1) return;
    const start = toMinutes(startValue);
    const end = toMinutes(endValue);
    if (end <= start) return;
    const weekdays = checkboxes()
      .filter((box) => box.checked)
      .map((box) => Number(box.value));
    const resource = document.querySelector("#blockResource").value || null;
    const from = document.querySelector("#blockFrom").value || null;
    const to = document.querySelector("#blockTo").value || null;
    let limit = 24 * 60;
    try {
      const others = await request(`/scheduling-blocks?room_id=${encodeURIComponent(room)}`);
      for (const other of others) {
        if (
          !other.active ||
          other.id === editingId ||
          !weekdays.includes(other.weekday) ||
          (other.resource_id || null) !== resource ||
          !overlapsValidity(other, from, to)
        ) {
          continue;
        }
        const otherStart = toMinutes(other.start_time);
        if (otherStart >= start) limit = Math.min(limit, otherStart);
      }
    } catch {
      return;
    }
    const wanted = Math.ceil((end - start) / step);
    const fits = Math.floor((limit - start) / step);
    const count = Math.max(1, Math.min(wanted, fits));
    const fitted = start + count * step;
    if (fitted > limit) {
      errorBox.classList.remove("ok");
      errorBox.textContent = "Pas assez de place avant la plage suivante pour un créneau complet.";
      return;
    }
    document.querySelector("#blockEnd").value = toHHMM(fitted);
    document.querySelector("#blockCount").value = String(count);
    errorBox.classList.add("ok");
    errorBox.textContent =
      fitted === end
        ? `${count} créneau(x) de ${step} min.`
        : `Fin ajustée à ${toHHMM(fitted)} : ${count} créneau(x) complet(s) de ${step} min.`;
  }

  function syncCount() {
    const start = document.querySelector("#blockStart").value;
    const end = document.querySelector("#blockEnd").value;
    const step = Number(document.querySelector("#blockSlot").value);
    const count = document.querySelector("#blockCount");
    if (start && end && step > 0 && toMinutes(end) > toMinutes(start)) {
      count.value = String(Math.round((toMinutes(end) - toMinutes(start)) / step));
    } else {
      count.value = "";
    }
  }

  // Le nombre de plages détermine l'heure de fin (début + nombre × durée).
  document.querySelector("#blockCount").addEventListener("change", () => {
    const start = document.querySelector("#blockStart").value;
    const step = Number(document.querySelector("#blockSlot").value);
    const count = Number(document.querySelector("#blockCount").value);
    if (!start || !(step > 0) || !Number.isInteger(count) || count < 1) return;
    const end = toMinutes(start) + count * step;
    if (end > 24 * 60 - 1) {
      errorBox.classList.remove("ok");
      errorBox.textContent = "Ce nombre de plages dépasse la fin de la journée.";
      return;
    }
    document.querySelector("#blockEnd").value = toHHMM(end);
    fitEnd();
  });

  for (const selector of ["#blockSlot", "#blockStart", "#blockEnd", "#blockResource"]) {
    document.querySelector(selector).addEventListener("change", async () => {
      await fitEnd();
      syncCount();
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.textContent = "";
    errorBox.classList.remove("ok");
    const value = (selector) => document.querySelector(selector).value;
    const body = {
      resource_id: value("#blockResource") || null,
      start_time: value("#blockStart"),
      end_time: value("#blockEnd"),
      slot_minutes: Number(value("#blockSlot")),
      label: value("#blockLabel").trim() || null,
      color: selectedColor,
      valid_from: value("#blockFrom") || null,
      valid_to: value("#blockTo") || null,
    };
    try {
      if (editingId) {
        const weekday = checkboxes().find((box) => box.checked);
        await request(`/scheduling-blocks/${editingId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...body,
            weekday: Number(weekday.value),
            active: document.querySelector("#blockActive").checked,
          }),
        });
      } else {
        const weekdays = checkboxes()
          .filter((box) => box.checked)
          .map((box) => Number(box.value));
        if (!weekdays.length) throw new Error("Choisissez au moins un jour.");
        await request("/scheduling-blocks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...body, room_id: value("#blockRoom"), weekdays }),
        });
      }
      const wasEditing = Boolean(editingId);
      resetForm();
      await refresh();
      errorBox.classList.add("ok");
      errorBox.textContent = wasEditing ? "Plage modifiée." : "Plage ajoutée.";
    } catch (error) {
      errorBox.classList.remove("ok");
      errorBox.textContent = error.message;
    }
  });

  document.querySelector("#blockCancelEdit").addEventListener("click", resetForm);
  document.querySelector("#blockRoomFilter").addEventListener("change", () => {
    refresh().catch((error) => {
      errorBox.textContent = error.message;
    });
  });
  buildPalette();
  setColor(null);
  document.querySelector("#manageBlocks").addEventListener("click", openDialog);
})();
