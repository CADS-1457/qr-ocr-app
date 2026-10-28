"use strict";

(() => {
  const api = `${window.AppAuth.API_BASE}/api/agenda`;
  const calendar = document.querySelector("#calendar");
  const message = document.querySelector("#message");
  const HOUR_HEIGHTS = [32, 40, 48, 64, 80, 104];
  let HOUR_HEIGHT = HOUR_HEIGHTS.includes(Number(localStorage.getItem("calendarHourHeight")))
    ? Number(localStorage.getItem("calendarHourHeight")) : 48;
  let hideBooked = localStorage.getItem("calendarHideBooked") === "1";
  const EYE_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.9 17.9A10.5 10.5 0 0 1 12 19C5.6 19 2 12 2 12a18 18 0 0 1 4.1-5.1M9.9 5.2A9.7 9.7 0 0 1 12 5c6.4 0 10 7 10 7a18 18 0 0 1-2.2 3.2"/><path d="M14.1 14.1a3 3 0 0 1-4.2-4.2"/><path d="m2 2 20 20"/></svg>';
  const DEFAULT_FIRST_HOUR = 7;
  const DEFAULT_LAST_HOUR = 20;
  const MAX_MONTH_EVENTS = 3;
  const STATE_LABELS = {
    PLANNED: "Planifié",
    URGENT: "Urgence",
    PRESENT: "Présent",
    WAITING: "En attente",
    IN_PROGRESS: "En cours",
    RECOVERY: "Réveil",
    DISCHARGED: "Sorti",
    CANCELLED: "Annulé",
    NO_SHOW: "Absent",
  };

  let statusList = [];
  let statusColors = {};
  const statusColor = (state) => statusColors[state] || "#94a3b8";
  const statusLabel = (state) => statusList.find((item) => item.code === state)?.label || STATE_LABELS[state] || state;
  const statusEdge = (state) => Boolean(statusList.find((item) => item.code === state)?.edge);

  const savedView = localStorage.getItem("calendarView");
  let view = ["day", "week", "month"].includes(savedView) ? savedView : "week";
  let cursor = startOfDay(new Date());
  let events = [];
  let availability = { blocks: [], restricted: new Set() };
  let exceptions = [];
  let loadToken = 0;
  let currentEvent = null;
  let editingId = null;
  let suppressClick = false;
  const CLOSED_STATES = ["CANCELLED", "NO_SHOW", "DISCHARGED"];
  const rooms = [];

  function startOfDay(date) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate());
  }

  function addDays(date, count) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate() + count);
  }

  function mondayOf(date) {
    return addDays(date, -((date.getDay() + 6) % 7));
  }

  function isoDay(date) {
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${date.getFullYear()}-${month}-${day}`;
  }

  const sameDay = (a, b) => isoDay(a) === isoDay(b);
  const formatTime = (date) =>
    date.toLocaleTimeString("fr-BE", { hour: "2-digit", minute: "2-digit" });

  function toLocalInput(date) {
    return `${isoDay(date)}T${String(date.getHours()).padStart(2, "0")}:${String(
      date.getMinutes(),
    ).padStart(2, "0")}`;
  }

  function showMessage(text, isError = false) {
    message.textContent = text;
    message.classList.toggle("error", isError);
  }

  async function request(path, options = {}) {
    const response = await fetch(`${api}${path}`, {
      ...options,
      headers: window.AppAuth.headers(options.headers || {}),
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const reason = data?.detail || `Erreur HTTP ${response.status}`;
      throw new Error(typeof reason === "string" ? reason : JSON.stringify(reason));
    }
    return data;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function tempChip() {
    const chip = el("span", "temp-chip");
    chip.innerHTML = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>';
    chip.title = "Patient temporaire : à réconcilier avec la base patient à l'accueil";
    chip.setAttribute("role", "img");
    chip.setAttribute("aria-label", "Patient temporaire");
    return chip;
  }

  function populate(select, entries, placeholder) {
    const current = select.multiple ? pickedIds(select) : [select.value];
    select.replaceChildren();
    if (!select.multiple) {
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = placeholder;
      select.append(empty);
    }
    for (const entry of entries) {
      const option = document.createElement("option");
      option.value = entry.id;
      option.textContent = entry.display_name || entry.name;
      option.selected = current.includes(entry.id);
      select.append(option);
    }
    if (!select.multiple) select.value = current[0];
    select._refresh?.();
  }

  // Filtres à choix multiples (Salle, Médecin) : le <select multiple> reste la source de vérité.
  const pickedIds = (select) => [...select.selectedOptions].map((option) => option.value).filter(Boolean);
  const singlePick = (select) => {
    const ids = pickedIds(select);
    return ids.length === 1 ? ids[0] : "";
  };
  const roomPicks = () => pickedIds(document.querySelector("#roomFilter"));
  const doctorPicks = () => pickedIds(document.querySelector("#doctorFilter"));
  function clearPicks(select) {
    for (const option of select.options) option.selected = false;
    select._refresh?.();
  }
  function initMultiFilter(box) {
    const select = box.querySelector("select");
    const summary = box.querySelector("summary");
    const list = box.querySelector(".multi-list");
    select._refresh = () => {
      list.replaceChildren();
      for (const option of select.options) {
        const label = el("label", "multi-option");
        const input = document.createElement("input");
        input.type = "checkbox";
        input.checked = option.selected;
        input.addEventListener("change", () => {
          option.selected = input.checked;
          select._refresh();
          select.dispatchEvent(new Event("change"));
        });
        label.append(input, document.createTextNode(option.textContent));
        list.append(label);
      }
      const names = [...select.selectedOptions].map((option) => option.textContent);
      summary.textContent = names.length === 0 ? box.dataset.all
        : names.length === 1 ? names[0] : `${names.length} sélectionnés`;
    };
    document.addEventListener("click", (event) => {
      if (!box.contains(event.target)) box.querySelector("details").open = false;
    });
  }
  document.querySelectorAll(".multi-filter").forEach(initMultiFilter);

  function visibleRange() {
    if (view === "day") return [cursor, addDays(cursor, 1)];
    if (view === "week") {
      const first = mondayOf(cursor);
      return [first, addDays(first, 7)];
    }
    const first = mondayOf(new Date(cursor.getFullYear(), cursor.getMonth(), 1));
    return [first, addDays(first, 42)];
  }

  function periodLabel() {
    const fmt = (date, options) => date.toLocaleDateString("fr-BE", options);
    if (view === "day") {
      return fmt(cursor, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    }
    if (view === "month") return fmt(cursor, { month: "long", year: "numeric" });
    const first = mondayOf(cursor);
    const last = addDays(first, 6);
    const sameMonth = first.getMonth() === last.getMonth();
    return `${fmt(first, sameMonth ? { day: "numeric" } : { day: "numeric", month: "short" })} – ${fmt(
      last,
      { day: "numeric", month: "long", year: "numeric" },
    )}`;
  }

  // Le bord gauche suit le statut (ou l'urgence) ; le fond reste celui de la plage / du type.
  function edgeColor(event) {
    if (statusEdge(event.state)) return statusColor(event.state);
    if (/urgen/i.test(event.appointment_type || "")) return "#dc2626";
    return null;
  }

  function eventColor(event) {
    const transferred = event.lane_room_id && event.lane_room_id !== event.room_id;
    const laneColor = transferred ? rooms.find((room) => room.id === event.lane_room_id)?.color : null;
    return laneColor || event.type_color || event.room_color || "#0f4c81";
  }

  let hoverCard = null;
  let hoverTimer = null;
  const hideHoverCard = () => {
    clearTimeout(hoverTimer);
    if (hoverCard) hoverCard.hidden = true;
  };
  function showHoverCard(node, event) {
    if (!hoverCard) {
      hoverCard = el("div", "event-hover-card");
      hoverCard.setAttribute("role", "tooltip");
      hoverCard.hidden = true;
      document.body.append(hoverCard);
    }
    const rows = [
      ["Heure", `${formatTime(new Date(event.starts_at))} – ${formatTime(new Date(event.ends_at))}`],
      ["Type", event.appointment_type],
      ["Salle", event.room],
      ["Médecin", event.doctor],
      ["Statut", statusLabel(event.state), statusColor(event.state)],
      ["Produit(s)", event.planned_products > 0 ? event.planned_products_label || String(event.planned_products) : ""],
      ["Notes", event.notes?.trim()],
    ].filter(([, value]) => value);
    const title = el("strong", "", event.patient_name);
    if (event.patient_temporary) title.append(" (patient temporaire)");
    hoverCard.replaceChildren(title);
    for (const [label, value, color] of rows) {
      const row = el("div", "ehc-row");
      const cell = el("span", "ehc-value", value);
      if (color) {
        const dot = el("span", "status-dot");
        dot.style.background = color;
        cell.prepend(dot);
      }
      row.append(el("span", "ehc-label", label), cell);
      hoverCard.append(row);
    }
    hoverCard.hidden = false;
    const rect = node.getBoundingClientRect();
    const width = hoverCard.offsetWidth;
    const height = hoverCard.offsetHeight;
    let left = rect.right + 8;
    if (left + width > window.innerWidth - 8) left = Math.max(8, rect.left - width - 8);
    const top = Math.min(Math.max(8, rect.top), Math.max(8, window.innerHeight - height - 8));
    hoverCard.style.left = `${left}px`;
    hoverCard.style.top = `${top}px`;
  }
  function attachHoverCard(node, event) {
    node.addEventListener("mouseenter", () => {
      clearTimeout(hoverTimer);
      hoverTimer = setTimeout(() => showHoverCard(node, event), 450);
    });
    node.addEventListener("mouseleave", hideHoverCard);
    node.addEventListener("pointerdown", hideHoverCard);
  }

  function eventTitle(event) {
    return [
      event.patient_name,
      `${formatTime(new Date(event.starts_at))} – ${formatTime(new Date(event.ends_at))}`,
      event.appointment_type,
      event.room,
    ]
      .filter(Boolean)
      .join(" · ");
  }

  const PRODUCT_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="m3 8 9 5 9-5"/><path d="M12 13v8"/></svg>';
  const DOCTOR_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="7" r="4"/><path d="M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1"/></svg>';
  const TRASH_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg>';
  const SAVE_ICON = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 3h12l4 4v14H3V3z"/><path d="M7 3v6h10V3"/><path d="M7 21v-8h10v8"/></svg>';
  const CAMERA_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg>';
  const productsTitle = (event) => `Produit(s) prévu(s) :\n${event.planned_products_label || event.planned_products}`;

  function plannedStockSection(event) {
    const section = el("section", "planned-stock");
    section.append(el("h3", "", "Produits prévus (QR code)"));
    const list = el("ul", "planned-list");
    const message = el("p", "planned-message");
    message.setAttribute("role", "status");
    const editable = !["COMPLETED", ...CLOSED_STATES].includes(event.state);
    const base = `/appointments/${event.id}/planned-stock`;

    const render = (rows) => {
      list.replaceChildren();
      if (!rows.length) list.append(el("li", "planned-empty", "Aucun produit prévu."));
      for (const row of rows) {
        const item = el("li");
        const detail = [row.lot_number && `lot ${row.lot_number}`, row.serial_number].filter(Boolean).join(" · ");
        const count = Number(row.reserved_quantity > 0 ? row.reserved_quantity : row.used_quantity);
        const qty = `${count} ${row.reserved_quantity > 0 ? "prévu" : "utilisé"}`;
        item.append(el("span", "", `${row.item_name}${detail ? ` (${detail})` : ""} — ${qty}`));
        if (editable && row.reserved_quantity > 0) {
          const remove = el("button", "planned-remove");
          remove.type = "button";
          remove.title = "Retirer ce produit";
          remove.setAttribute("aria-label", "Retirer ce produit");
          remove.innerHTML = TRASH_ICON;
          remove.addEventListener("click", () => send(`${base}/${row.stock_unit_id}`, { method: "DELETE" }));
          item.append(remove);
        }
        list.append(item);
      }
    };
    const send = async (path, options) => {
      message.textContent = "";
      try {
        const rows = await request(path, options);
        render(rows);
        // L'icône du calendrier suit immédiatement l'ajout ou le retrait.
        event.planned_products = rows.filter((row) => row.reserved_quantity > 0 || row.used_quantity > 0).length;
        load();
      } catch (error) {
        message.textContent = error.message;
      }
    };
    section.append(list, message);
    if (editable) {
      const form = el("div", "planned-form");
      const code = el("input");
      code.placeholder = "Scanner ou saisir le code du produit";
      code.autocomplete = "off";
      const qty = el("input");
      qty.type = "number";
      qty.min = "1";
      qty.value = "1";
      qty.setAttribute("aria-label", "Quantité");
      const add = el("button", "planned-save");
      add.type = "button";
      add.title = "Ajouter le produit";
      add.setAttribute("aria-label", "Ajouter le produit");
      add.innerHTML = SAVE_ICON;
      const camera = el("button", "planned-camera");
      camera.type = "button";
      camera.title = "Activer la caméra";
      camera.setAttribute("aria-label", "Activer la caméra");
      camera.innerHTML = CAMERA_ICON;
      const submit = async (value) => {
        const text = String(value || "").trim();
        if (!text) return;
        await send(base, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ code: text, quantity: Number(qty.value) || 1 }),
        });
        code.value = "";
        code.focus();
      };
      add.addEventListener("click", () => submit(code.value));
      code.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          submit(code.value);
        }
      });
      const reader = el("div");
      reader.id = "plannedReader";
      camera.addEventListener("click", async () => {
        try {
          if (!window.Html5Qrcode) {
            await new Promise((resolve, reject) => {
              const script = document.createElement("script");
              script.src = "https://unpkg.com/html5-qrcode";
              script.onload = resolve;
              script.onerror = () => reject(new Error("Caméra indisponible."));
              document.head.append(script);
            });
          }
          const scanner = new window.Html5Qrcode("plannedReader");
          await scanner.start({ facingMode: "environment" }, { fps: 10, qrbox: 220 }, async (decoded) => {
            await scanner.stop().catch(() => {});
            reader.replaceChildren();
            submit(decoded);
          });
        } catch (error) {
          message.textContent = error.message || "Caméra indisponible.";
        }
      });
      form.append(code, camera, qty, add);
      section.append(form, reader);
    }
    request(base).then(render).catch((error) => { message.textContent = error.message; });
    return section;
  }

  // Surréservation : la salle autorise plusieurs rendez-vous simultanés et la plage de ce rendez-vous n'est pas pleine.
  function overbookSeats(event) {
    const room = rooms.find((item) => item.id === event.room_id);
    const limit = room?.max_parallel || 1;
    if (limit < 2) return null;
    const start = new Date(event.starts_at).getTime();
    const end = new Date(event.ends_at).getTime();
    if (end <= Date.now()) return null;
    const others = events.filter((item) => item.room_id === event.room_id
      && !["CANCELLED", "NO_SHOW", "POSTPONED"].includes(item.state)
      && new Date(item.starts_at).getTime() < end && new Date(item.ends_at).getTime() > start);
    const points = [start, ...others.map((item) => new Date(item.starts_at).getTime()).filter((time) => time > start && time < end)];
    const used = Math.max(...points.map((point) => others.filter((item) =>
      new Date(item.starts_at).getTime() <= point && new Date(item.ends_at).getTime() > point).length));
    return used < limit ? { used, limit } : null;
  }

  // Changement rapide de statut depuis la fiche du rendez-vous.
  function statusPicker(event) {
    const cell = el("dd", "status-pick");
    const dot = el("span", "status-dot");
    dot.style.background = statusColor(event.state);
    const select = el("select", "status-select");
    select.setAttribute("aria-label", "Changer le statut");
    const options = Object.fromEntries(
      statusList.filter((item) => item.active || item.code === event.state).map((item) => [item.code, item.label]),
    );
    if (!options[event.state]) options[event.state] = statusLabel(event.state);
    for (const [code, label] of Object.entries(options)) {
      const option = el("option", "", label);
      option.value = code;
      select.append(option);
    }
    select.value = event.state;
    select.addEventListener("change", async () => {
      select.disabled = true;
      try {
        await request(`/appointments/${event.id}/status`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ new_state: select.value, comment: "Statut modifié depuis le calendrier" }),
        });
        document.querySelector("#detailDialog").close();
        showMessage(`Statut : ${options[select.value]}.`);
      } catch (error) {
        select.value = event.state;
        showMessage(error.message, true);
      }
      await load();
    });
    cell.append(dot, select);
    return cell;
  }

  function openDetail(event) {
    const start = new Date(event.starts_at);
    const end = new Date(event.ends_at);
    const body = document.querySelector("#detailBody");
    const title = el("h2", "temp-title");
    title.append(el("span", "temp-name", event.patient_name));
    if (event.patient_temporary) title.append(tempChip());
    body.replaceChildren(title);
    const list = el("dl", "detail-list");
    const rows = [
      [
        "Date",
        start.toLocaleDateString("fr-BE", {
          weekday: "long",
          day: "numeric",
          month: "long",
          year: "numeric",
        }),
      ],
      ["Heure", `${formatTime(start)} – ${formatTime(end)}`],
      ["Statut", statusLabel(event.state)],
      ["Type", event.appointment_type],
      ["Salle", event.current_location && event.current_location !== event.room
        ? `${event.current_location} (venant de : ${event.room})`
        : event.room],
      ["Médecin", event.doctor],
      ["Opérateur", event.operator],
      ["Notes", event.notes],
    ];
    for (const [label, value] of rows) {
      if (!value) continue;
      if (label === "Statut") {
        list.append(el("dt", "", label), statusPicker(event));
        continue;
      }
      list.append(el("dt", "", label), el("dd", "", value));
    }
    body.append(list);
    body.append(plannedStockSection(event));
    currentEvent = event;
    if (event.current_location && event.current_location !== event.room) {
      // « Venant de » = salle précédente réelle d'après l'historique des mouvements du jour.
      const roomCell = [...list.querySelectorAll("dt")].find((dt) => dt.textContent === "Salle")?.nextElementSibling;
      request(`/appointments/${event.id}/movements?day=${isoDay(start)}`).then((moves) => {
        const last = moves.filter((move) => move.appointment_id === event.id && move.from_room
          && move.from_room !== move.to_room).pop();
        if (roomCell && currentEvent === event && last) {
          roomCell.textContent = `${event.current_location} (venant de : ${last.from_room})`;
        }
      }).catch(() => {});
    }
    const open = !CLOSED_STATES.includes(event.state);
    document.querySelector("#detailEdit").hidden = !open;
    document.querySelector("#detailCancel").hidden = !open;
    document.querySelector("#detailDelete").hidden = event.state !== "PLANNED";
    document.querySelector("#detailReconcile").hidden = !event.patient_temporary;
    const overbook = document.querySelector("#detailOverbook");
    const seats = open ? overbookSeats(event) : null;
    overbook.hidden = !seats;
    if (seats) {
      overbook.title = `Ajouter un patient sur cette plage (${seats.used}/${seats.limit} places, surréservation)`;
      overbook.setAttribute("aria-label", overbook.title);
    }
    document.querySelector("#detailOpen").href = `agenda.html?day=${isoDay(start)}`;
    const transferLink = document.querySelector("#detailTransfer");
    transferLink.hidden = !open || !sameDay(start, new Date());
    document.querySelector("#detailDialog").showModal();
  }

  const payloadOf = (event, changes = {}) => ({
    patient_id: event.patient_id,
    appointment_type_id: event.appointment_type_id || null,
    room_id: event.room_id,
    doctor_id: event.doctor_id || null,
    operator_id: event.operator_id || null,
    starts_at: event.starts_at,
    ends_at: event.ends_at,
    phone: event.phone || null,
    notes: event.notes || null,
    ...changes,
  });

  // Déplace un rendez-vous en conservant sa durée ; le serveur contrôle plages et conflits.
  async function moveEvent(event, newStart, newRoom = null) {
    const duration = new Date(event.ends_at) - new Date(event.starts_at);
    if (!newRoom && newStart.getTime() === new Date(event.starts_at).getTime()) return;
    try {
      await request(`/appointments/${event.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          payloadOf(event, {
            starts_at: newStart.toISOString(),
            ends_at: new Date(newStart.getTime() + duration).toISOString(),
            ...(newRoom ? { room_id: newRoom } : {}),
          }),
        ),
      });
      const roomName = newRoom && rooms.find((room) => room.id === newRoom)?.name;
      showMessage(
        `Rendez-vous déplacé${roomName ? ` vers ${roomName}` : ""} au ${newStart.toLocaleDateString("fr-BE")} à ${formatTime(newStart)}.`,
      );
    } catch (error) {
      showMessage(error.message, true);
    }
    await load();
  }

  // Glisser-déposer d'un rendez-vous dans la grille jour/semaine (pas de 15 min).
  function enableDrag(node, event, firstHour) {
    if (CLOSED_STATES.includes(event.state)) return;
    node.addEventListener("pointerdown", (down) => {
      if (down.button !== 0) return;
      const origin = { x: down.clientX, y: down.clientY };
      const grab = down.clientY - node.getBoundingClientRect().top;
      let dragging = false;
      const onMove = (move) => {
        const dx = move.clientX - origin.x;
        const dy = move.clientY - origin.y;
        if (!dragging && Math.hypot(dx, dy) < 6) return;
        if (!dragging) {
          dragging = true;
          node.classList.add("dragging");
        }
        node.style.transform = `translate(${dx}px, ${dy}px)`;
      };
      const onUp = async (up) => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        if (!dragging) return;
        node.classList.remove("dragging");
        node.style.transform = "";
        suppressClick = true;
        setTimeout(() => { suppressClick = false; }, 300);
        const target = document
          .elementsFromPoint(up.clientX, up.clientY)
          .find((item) => item.classList?.contains("cal-col"));
        if (!target) return;
        const raw = ((up.clientY - grab - target.getBoundingClientRect().top) / HOUR_HEIGHT) * 60 + firstHour * 60;
        const minutes = Math.max(0, Math.round(raw / 15) * 15);
        const [year, month, day] = target.dataset.date.split("-").map(Number);
        let newRoom = null;
        const laneRooms = target._laneRooms || [];
        if (laneRooms.length > 1) {
          const rect = target.getBoundingClientRect();
          const laneAt = Math.min(
            laneRooms.length - 1,
            Math.max(0, Math.floor(((up.clientX - rect.left) / rect.width) * laneRooms.length)),
          );
          const currentRoom = event.current_location_id || event.room_id;
          if (laneRooms[laneAt] && laneRooms[laneAt] !== currentRoom) newRoom = laneRooms[laneAt];
        }
        let newStart = new Date(year, month - 1, day, 0, minutes);
        if (newRoom) {
          // Changement de salle : l'heure de début est celle de la manipulation.
          newStart = new Date();
          newStart.setSeconds(0, 0);
        }
        await moveEvent(event, newStart, newRoom);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    });
  }

  // Répartit les événements qui se chevauchent sur des colonnes côte à côte.
  function layoutDay(dayEvents) {
    const sorted = [...dayEvents].sort(
      (a, b) => new Date(a.starts_at) - new Date(b.starts_at) || new Date(b.ends_at) - new Date(a.ends_at),
    );
    const placed = [];
    let cluster = [];
    let clusterEnd = 0;
    const flush = () => {
      const columns = [];
      for (const item of cluster) {
        let index = columns.findIndex((end) => end <= item.start);
        if (index === -1) {
          index = columns.length;
          columns.push(0);
        }
        columns[index] = item.end;
        item.column = index;
      }
      for (const item of cluster) item.columns = columns.length;
      placed.push(...cluster);
      cluster = [];
    };
    for (const event of sorted) {
      const item = { event, start: new Date(event.starts_at).getTime(), end: new Date(event.ends_at).getTime() };
      if (cluster.length && item.start >= clusterEnd) flush();
      cluster.push(item);
      clusterEnd = Math.max(clusterEnd, item.end);
    }
    if (cluster.length) flush();
    return placed;
  }

  // Chevauchements : seuls les rendez-vous d'un même agenda (surréservation) se partagent la voie.
  function layoutDayByLane(dayEvents, lanes) {
    const byLane = new Map();
    for (const event of dayEvents) {
      const lane = lanes.ofEvent(event);
      if (!byLane.has(lane)) byLane.set(lane, []);
      byLane.get(lane).push(event);
    }
    return [...byLane.values()].flatMap((group) => layoutDay(group));
  }

  function eventsOfDay(day) {
    const start = day.getTime();
    const end = addDays(day, 1).getTime();
    return events.filter(
      (event) => new Date(event.starts_at) < end && new Date(event.ends_at) > start,
    );
  }

  const toMinutes = (hhmm) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));
  const blocksOfDay = (day) => availability.blocks.filter((block) => block.date === isoDay(day));
  const exceptionsOfDay = (day) => exceptions.filter(
    (item) => item.start_date <= isoDay(day) && item.end_date >= isoDay(day),
  );
  const exceptionHours = (item) =>
    item.start_time || item.end_time ? `${item.start_time || "00:00"}–${item.end_time || "24:00"}` : "";
  const exceptionLabel = (item) =>
    [item.type, exceptionHours(item), item.room_id ? item.room : "", item.resource, item.note]
      .filter(Boolean).join(" · ");
  const zoneName = (block) =>
    [block.room, block.resource, block.label].filter(Boolean).join(" · ") || "Plage";

  // Une colonne (« voie ») par agenda (salle + médecin) : plages et rendez-vous d'un même
  // agenda restent alignés, sans jamais recouvrir ceux d'un autre agenda.
  function agendaLanes(dayBlocks, dayEvents, forcedRooms = []) {
    const keyOf = (room, resource) => `${room}|${resource || ""}`;
    const lanes = new Map();
    for (const block of dayBlocks) {
      const key = keyOf(block.room_id, block.resource_id);
      if (!lanes.has(key)) {
        lanes.set(key, { key, room: block.room_id, label: [block.room, block.resource].filter(Boolean).join(" · "), index: 0 });
      }
    }
    for (const room of forcedRooms) {
      if (![...lanes.values()].some((lane) => lane.room === room.id)) {
        lanes.set(keyOf(room.id, ""), { key: keyOf(room.id, ""), room: room.id, label: room.name, index: 0 });
      }
    }
      const eventLane = (event) => {
      const laneRoom = event.lane_room_id || event.room_id;
      const exact = keyOf(laneRoom, event.doctor_id);
      if (lanes.has(exact)) return exact;
      const generic = keyOf(laneRoom, "");
      if (lanes.has(generic)) return generic;
      const sameRoom = [...lanes.values()].find((lane) => lane.room === laneRoom);
      if (sameRoom) return sameRoom.key;
      lanes.set(generic, { key: generic, room: laneRoom, label: event.lane_room || event.room || "", index: 0 });
      return generic;
    };
    const eventKeys = new Map(dayEvents.map((event) => [event, eventLane(event)]));
    const roomRank = (id) => {
      const position = rooms.findIndex((room) => room.id === id);
      return position < 0 ? rooms.length : position;
    };
    [...lanes.values()]
      .sort((a, b) => roomRank(a.room) - roomRank(b.room) || a.label.localeCompare(b.label, "fr"))
      .forEach((lane, index) => {
        lane.index = index;
      });
    return {
      count: Math.max(lanes.size, 1),
      labels: [...lanes.values()].sort((a, b) => a.index - b.index).map((lane) => lane.label),
      rooms: [...lanes.values()].sort((a, b) => a.index - b.index).map((lane) => lane.room),
      ofBlock: (block) => lanes.get(keyOf(block.room_id, block.resource_id)).index,
      ofEvent: (event) => lanes.get(eventKeys.get(event)).index,
    };
  }

  async function reorderRooms(visibleRooms, from, to) {
    if (from === to || Number.isNaN(from)) return;
    const moved = [...visibleRooms];
    moved.splice(to, 0, moved.splice(from, 1)[0]);
    const ids = [...new Set(moved)];
    const rest = rooms.map((room) => room.id).filter((id) => !ids.includes(id));
    try {
      await request("/rooms-order", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ room_ids: [...ids, ...rest] }),
      });
      const rows = await request("/rooms");
      rooms.splice(0, rooms.length, ...rows);
      render();
    } catch (error) {
      showMessage(error.message, true);
    }
  }

  function renderLegend() {
    const legend = document.querySelector("#calendarLegend");
    const seen = new Map();
    for (const block of availability.blocks) {
      const key = `${block.room_id}|${block.resource_id || ""}|${block.color}`;
      if (!seen.has(key)) seen.set(key, block);
    }
    legend.replaceChildren();
    legend.hidden = seen.size === 0;
    if (!seen.size) return;
    legend.append(el("strong", "", "Plages réservables :"));
    for (const block of seen.values()) {
      const item = el("span", "legend-item");
      const dot = el("i", "legend-dot");
      dot.style.setProperty("--zone-color", block.color || "#16a34a");
      item.append(dot, document.createTextNode([block.room, block.resource].filter(Boolean).join(" · ")));
      legend.append(item);
    }
    const off = el("span", "legend-item");
    off.append(el("i", "legend-dot legend-off"), document.createTextNode("Hors plage (non réservable)"));
    legend.append(off);
  }

  const hideToggle = document.querySelector("#toggleBooked");
  function refreshHideToggle() {
    const title = hideBooked ? "Réafficher les rendez-vous" : "Masquer les rendez-vous déjà pris pour voir les places libres";
    hideToggle.setAttribute("aria-pressed", String(hideBooked));
    hideToggle.title = title;
    hideToggle.setAttribute("aria-label", title);
    hideToggle.innerHTML = hideBooked ? EYE_OFF_ICON : EYE_ICON;
    calendar.classList.toggle("hide-booked", hideBooked);
  }
  hideToggle.addEventListener("click", () => {
    hideBooked = !hideBooked;
    localStorage.setItem("calendarHideBooked", hideBooked ? "1" : "0");
    refreshHideToggle();
  });
  refreshHideToggle();
  const isRestrictedScope = () => {
    const picked = roomPicks();
    return picked.length > 0 && picked.every((room) => availability.restricted.has(room));
  };

  // Créneaux d'une plage déjà complets (plafond de la salle atteint) : [début, fin] en minutes.
  function takenSlots(block, day, active) {
    const step = block.slot_minutes || 30;
    const room = rooms.find((item) => item.id === block.room_id);
    const limit = Math.max(1, room?.max_parallel || 1);
    const own = active.filter((event) => event.room_id === block.room_id
      && (!block.resource_id || !event.doctor_id || event.doctor_id === block.resource_id));
    const base = new Date(day.getFullYear(), day.getMonth(), day.getDate()).getTime();
    const slots = [];
    for (let minute = toMinutes(block.start_time); minute + step <= toMinutes(block.end_time); minute += step) {
      const from = base + minute * 60_000;
      const to = from + step * 60_000;
      const used = own.filter((event) => new Date(event.starts_at) < to && new Date(event.ends_at) > from).length;
      slots.push({ from: minute, to: minute + step, past: to <= Date.now(), taken: used >= limit });
    }
    return slots;
  }
  const activeEventsOfDay = (day) =>
    eventsOfDay(day).filter((event) => !["CANCELLED", "NO_SHOW", "POSTPONED"].includes(event.state));

  // Créneaux encore libres : plages réservables moins les rendez-vous actifs (plafond de la salle), hors passé.
  function freeSlotsOfDay(day) {
    const dayBlocks = blocksOfDay(day);
    if (!dayBlocks.length) return null;
    const active = activeEventsOfDay(day);
    let free = 0;
    for (const block of dayBlocks) {
      free += takenSlots(block, day, active).filter((slot) => !slot.past && !slot.taken).length;
    }
    return free;
  }

  function renderTimeGrid(days) {
    let firstHour = DEFAULT_FIRST_HOUR;
    let lastHour = DEFAULT_LAST_HOUR;
    for (const day of days) {
      for (const block of blocksOfDay(day)) {
        firstHour = Math.min(firstHour, Math.floor(toMinutes(block.start_time) / 60));
        const end = toMinutes(block.end_time);
        lastHour = Math.max(lastHour, end % 60 ? Math.floor(end / 60) : end / 60 - 1);
      }
    }
    for (const event of events) {
      const start = new Date(event.starts_at);
      const end = new Date(event.ends_at);
      if (days.some((day) => sameDay(day, start))) firstHour = Math.min(firstHour, start.getHours());
      if (days.some((day) => sameDay(day, end))) {
        lastHour = Math.max(lastHour, end.getMinutes() ? end.getHours() : end.getHours() - 1);
      }
    }
    const hours = lastHour - firstHour + 1;
    const root = el("div", "cal-root");
    root.style.setProperty("--days", String(days.length));
    root.style.setProperty("--hours", String(hours));
    root.style.setProperty("--hour-height", `${HOUR_HEIGHT}px`);

    const head = el("div", "cal-head");
    const zoom = el("div", "cal-zoom");
    const zoomButton = (label, title, delta) => {
      const button = el("button", "cal-zoom-button", label);
      button.type = "button";
      button.title = title;
      button.setAttribute("aria-label", title);
      button.disabled = !HOUR_HEIGHTS[HOUR_HEIGHTS.indexOf(HOUR_HEIGHT) + delta];
      button.addEventListener("click", () => {
        HOUR_HEIGHT = HOUR_HEIGHTS[HOUR_HEIGHTS.indexOf(HOUR_HEIGHT) + delta];
        localStorage.setItem("calendarHourHeight", String(HOUR_HEIGHT));
        render();
      });
      return button;
    };
    zoom.append(zoomButton("−", "Réduire la hauteur des lignes", -1), zoomButton("+", "Agrandir la hauteur des lignes", 1));
    head.append(zoom);
    for (const day of days) {
      const cell = el("div", sameDay(day, new Date()) ? "is-today" : "");
      cell.append(
        document.createTextNode(day.toLocaleDateString("fr-BE", { weekday: "short" })),
        el("span", "day-number", String(day.getDate())),
      );
      const free = freeSlotsOfDay(day);
      if (free !== null) {
        const badge = el("span", `day-free${free ? "" : " is-full"}`, free ? `${free} dispo` : "Complet");
        badge.title = free ? `${free} créneau(x) libre(s)` : "Aucun créneau libre";
        cell.append(badge);
      }
      head.append(cell);
      if (["admin", "superadmin"].includes(window.AppAuth.user()?.role)) {
        cell.classList.add("is-blockable");
        cell.title = "Cliquer pour bloquer cette journée ou une plage d’heures";
        cell.addEventListener("click", (click) => {
          if (click.target.closest(".lane-heads, .day-free")) return;
          document.dispatchEvent(new CustomEvent("exceptions:open", { detail: { day: isoDay(day) } }));
        });
      }
    }
    root.append(head);

    const scroll = el("div", "cal-scroll");
    const grid = el("div", "cal-grid");
    const hoursColumn = el("div", "cal-hours");
    for (let hour = firstHour; hour <= lastHour; hour += 1) {
      const label = el("span", "", `${String(hour).padStart(2, "0")}:00`);
      label.style.top = `${(hour - firstHour) * HOUR_HEIGHT}px`;
      if (hour === firstHour) label.style.transform = "none";
      hoursColumn.append(label);
    }
    grid.append(hoursColumn);

    for (const day of days) {
      const column = el("div", `cal-col${sameDay(day, new Date()) ? " is-today" : ""}`);
      column.dataset.date = isoDay(day);
      const dayBlocks = blocksOfDay(day);
      if (isRestrictedScope()) column.classList.add("is-restricted");
      const dayEvents = eventsOfDay(day);
      const inGroup = groupRoomIds();
      const forcedRooms = inGroup
        ? rooms.filter((room) => inGroup.has(room.id))
        : roomPicks().length > 1 ? rooms.filter((room) => roomPicks().includes(room.id))
          : view === "day" && !roomPicks().length ? rooms : [];
      const lanes = agendaLanes(dayBlocks, dayEvents, forcedRooms);
      const laneCount = lanes.count;
      column.style.setProperty("--lanes", String(laneCount));
      column._laneRooms = lanes.rooms;
      if (laneCount > 1) column.classList.add("has-lanes");
      if (days.length === 1 && laneCount > 1) {
        const strip = el("div", "lane-heads");
        const canReorder = ["admin", "superadmin"].includes(window.AppAuth.user()?.role);
        lanes.labels.forEach((label, position) => {
          const head = el("span", "", label);
          if (canReorder) {
            head.addEventListener("click", (click) => {
              click.stopPropagation();
              document.dispatchEvent(new CustomEvent("exceptions:open", {
                detail: { day: isoDay(day), roomId: lanes.rooms[position] },
              }));
            });
            head.draggable = true;
            head.classList.add("reorderable");
            head.title = "Cliquer pour bloquer cette salle · glisser pour changer l'ordre des colonnes";
            head.addEventListener("dragstart", (dragEvent) => {
              dragEvent.dataTransfer.setData("text/plain", String(position));
              dragEvent.dataTransfer.effectAllowed = "move";
            });
            head.addEventListener("dragover", (dragEvent) => dragEvent.preventDefault());
            head.addEventListener("drop", (dropEvent) => {
              dropEvent.preventDefault();
              reorderRooms(lanes.rooms, Number(dropEvent.dataTransfer.getData("text/plain")), position);
            });
          }
          strip.append(head);
        });
        head.lastElementChild.append(strip);
      }
      for (const item of exceptionsOfDay(day)) {
        const position = item.room_id ? lanes.rooms.indexOf(item.room_id) : -1;
        if (item.room_id && position < 0) continue;
        const band = el("div", "cal-exception", exceptionLabel(item));
        band.style.setProperty("--exception-color", item.color || "#64748b");
        band.title = exceptionLabel(item);
        const fromMin = item.start_time ? toMinutes(item.start_time) : 0;
        const toMin = item.end_time ? toMinutes(item.end_time) : 24 * 60;
        const bandTop = Math.max(0, (fromMin / 60 - firstHour) * HOUR_HEIGHT);
        const bandBottom = Math.max(bandTop, (toMin / 60 - firstHour) * HOUR_HEIGHT);
        band.style.top = `${bandTop}px`;
        band.style.height = `${bandBottom - bandTop}px`;
        band.style.bottom = "auto";
        band.style.left = item.room_id ? `${(position / laneCount) * 100}%` : "0";
        band.style.width = item.room_id ? `${100 / laneCount}%` : "100%";
        column.append(band);
      }
      for (const block of dayBlocks) {
        const startMin = toMinutes(block.start_time);
        const endMin = toMinutes(block.end_time);
        const lane = lanes.ofBlock(block);
        const zone = el("div", "cal-zone");
        zone._block = block;
        zone.style.setProperty("--zone-color", block.color || "#16a34a");
        zone.style.top = `${(startMin / 60 - firstHour) * HOUR_HEIGHT}px`;
        zone.style.height = `${((endMin - startMin) / 60) * HOUR_HEIGHT}px`;
        zone.style.left = `${(lane / laneCount) * 100}%`;
        zone.style.width = `${100 / laneCount}%`;
        const slotPx = (block.slot_minutes / 60) * HOUR_HEIGHT;
        if (slotPx >= 8) zone.style.setProperty("--slot-height", `${slotPx}px`);
        zone.title = `${zoneName(block)} · ${block.start_time} – ${block.end_time} · créneaux de ${block.slot_minutes} min`;
        zone.append(el("span", "cal-zone-label", zoneName(block)));
        column.append(zone);
        // Créneaux complets : barrés et non cliquables quand les rendez-vous sont masqués.
        for (const slot of takenSlots(block, day, activeEventsOfDay(day))) {
          if (!slot.taken) continue;
          const taken = el("div", "cal-slot-taken");
          taken.title = "Créneau déjà pris";
          taken.style.top = `${(slot.from / 60 - firstHour) * HOUR_HEIGHT}px`;
          taken.style.height = `${((slot.to - slot.from) / 60) * HOUR_HEIGHT}px`;
          taken.style.left = zone.style.left;
          taken.style.width = zone.style.width;
          column.append(taken);
        }
      }
      column.addEventListener("click", (clickEvent) => {
        if (suppressClick) return;
        const zoneNode = clickEvent.target.closest?.(".cal-zone");
        if (clickEvent.target !== column && !zoneNode) return;
        const rect = column.getBoundingClientRect();
        const laneAt = Math.min(
          laneCount - 1,
          Math.max(0, Math.floor(((clickEvent.clientX - rect.left) / rect.width) * laneCount)),
        );
        const raw = ((clickEvent.clientY - rect.top) / HOUR_HEIGHT) * 60 + firstHour * 60;
        const block = zoneNode
          ? zoneNode._block
          : dayBlocks.find(
            (entry) => lanes.ofBlock(entry) === laneAt
              && raw >= toMinutes(entry.start_time) && raw < toMinutes(entry.end_time),
          );
        if (!block && isRestrictedScope()) {
          showMessage("Ce créneau est hors des plages de rendez-vous de cette salle.", true);
          return;
        }
        const step = block ? block.slot_minutes : 30;
        const origin = block ? toMinutes(block.start_time) : 0;
        const minutes = origin + Math.floor((raw - origin) / step) * step;
        const start = new Date(day.getFullYear(), day.getMonth(), day.getDate(), 0, minutes);
        const laneRoom = days.length === 1 ? lanes.rooms[laneAt] : null;
        openCreate(start, step, block?.room_id || laneRoom, { laneRoom });
      });
      const dayStart = new Date(day.getFullYear(), day.getMonth(), day.getDate(), firstHour).getTime();
      const dayEnd = addDays(day, 1).getTime();
      for (const entry of layoutDayByLane(dayEvents, lanes)) {
        const { event, column: index, columns } = entry;
        const lane = lanes.ofEvent(event);
        const start = Math.max(new Date(event.starts_at).getTime(), dayStart);
        const end = Math.min(new Date(event.ends_at).getTime(), dayEnd);
        const top = ((start - dayStart) / 3_600_000) * HOUR_HEIGHT;
        const height = Math.max(((end - start) / 3_600_000) * HOUR_HEIGHT, 20);
        const block = el("button", `cal-event ${event.state}`);
        block.type = "button";
        attachHoverCard(block, event);
        block.style.setProperty("--event-color", eventColor(event));
        if (edgeColor(event)) block.style.setProperty("--edge-color", edgeColor(event));
        block.style.top = `${top}px`;
        block.style.height = `${height - 2}px`;
        const laneWidth = 100 / laneCount;
        block.style.left = `calc(${lane * laneWidth + (index / columns) * laneWidth}% + 2px)`;
        block.style.width = `calc(${laneWidth / columns}% - 4px)`;
        block.append(
          el("strong", "", event.patient_name),
          el("span", "", `${formatTime(new Date(event.starts_at))} · ${event.appointment_type || event.room}`),
        );
        if (event.patient_temporary) {
          block.classList.add("temp-patient");
          const title = block.querySelector("strong");
          title.replaceChildren(el("span", "temp-name", event.patient_name), tempChip());
        }
        if (event.show_status) {
          const dot = el("span", "status-dot");
          dot.style.background = statusColor(event.state);
          dot.title = statusLabel(event.state);
          block.querySelector("strong").prepend(dot);
        }
        if (height >= 62 && event.notes?.trim()) block.append(el("span", "cal-event-note", event.notes.trim()));
        if (event.doctor) {
          if (event.show_doctor) block.append(el("span", "cal-event-doctor", event.doctor));
          const badge = el("span", "cal-event-doctor-icon");
          badge.title = `Médecin : ${event.doctor}`;
          badge.innerHTML = DOCTOR_ICON;
          block.append(badge);
        }
        block.addEventListener("click", () => {
          if (!suppressClick) openDetail(event);
        });
        if (event.planned_products > 0) {
          const products = el("span", "cal-event-products");
          products.title = productsTitle(event);
          products.innerHTML = PRODUCT_ICON;
          block.append(products);
        }
        if (!CLOSED_STATES.includes(event.state) && sameDay(new Date(event.starts_at), new Date())) {
          const transfer = el("span", "cal-event-transfer");
          transfer.setAttribute("role", "link");
          transfer.title = "Transférer le patient (ex. salle de réveil)";
          transfer.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 8h14"/><path d="m14 4 4 4-4 4"/><path d="M20 16H6"/><path d="m10 12-4 4 4 4"/></svg>';
          transfer.addEventListener("pointerdown", (down) => down.stopPropagation());
          transfer.addEventListener("click", (click) => {
            click.stopPropagation();
            window.openTransferDialog(event);
          });
          block.append(transfer);
        }
        enableDrag(block, event, firstHour);
        column.append(block);
      }
      if (sameDay(day, new Date())) {
        const now = new Date();
        const position = ((now.getHours() * 60 + now.getMinutes()) / 60 - firstHour) * HOUR_HEIGHT;
        if (position >= 0 && position <= hours * HOUR_HEIGHT) {
          const line = el("div", "cal-now");
          line.style.top = `${position}px`;
          column.append(line);
        }
      }
      grid.append(column);
    }
    scroll.append(grid);
    root.append(scroll);
    calendar.replaceChildren(root);
    const today = days.some((day) => sameDay(day, new Date()));
    scroll.scrollTop = Math.max(
      ((today ? new Date().getHours() : 8) - firstHour - 1) * HOUR_HEIGHT,
      0,
    );
  }

  function renderMonth() {
    const [first] = visibleRange();
    const root = el("div", "cal-root");
    const head = el("div", "month-head");
    for (let i = 0; i < 7; i += 1) {
      head.append(
        el("div", "", addDays(first, i).toLocaleDateString("fr-BE", { weekday: "short" })),
      );
    }
    const grid = el("div", "month-grid");
    for (let i = 0; i < 42; i += 1) {
      const day = addDays(first, i);
      const classes = ["month-cell"];
      if (day.getMonth() !== cursor.getMonth()) classes.push("other-month");
      if (sameDay(day, new Date())) classes.push("is-today");
      const cell = el("div", classes.join(" "));
      cell.append(el("span", "day-number", String(day.getDate())));
      for (const item of exceptionsOfDay(day)) {
        const tag = el("div", "month-exception", exceptionLabel(item));
        tag.style.setProperty("--exception-color", item.color || "#64748b");
        tag.title = exceptionLabel(item);
        cell.append(tag);
      }
      const dayEvents = eventsOfDay(day);
      for (const event of dayEvents.slice(0, MAX_MONTH_EVENTS)) {
        const chip = el(
          "button",
          "month-event",
          `${formatTime(new Date(event.starts_at))} ${event.patient_name}`,
        );
        chip.type = "button";
        if (event.patient_temporary) {
          chip.classList.add("temp-patient");
          chip.replaceChildren(
            el("span", "temp-name", `${formatTime(new Date(event.starts_at))} ${event.patient_name}`),
            tempChip(),
          );
        }
        if (!CLOSED_STATES.includes(event.state)) {
          chip.draggable = true;
          chip.addEventListener("dragstart", (drag) => {
            drag.dataTransfer.setData("text/plain", event.id);
            drag.dataTransfer.effectAllowed = "move";
          });
        }
        attachHoverCard(chip, event);
        if (event.planned_products > 0) {
          const products = el("span", "month-event-products");
          products.title = productsTitle(event);
          products.innerHTML = PRODUCT_ICON;
          chip.append(products);
        }
        chip.style.setProperty("--event-color", eventColor(event));
        if (edgeColor(event)) chip.style.setProperty("--edge-color", edgeColor(event));
        chip.addEventListener("click", (clickEvent) => {
          clickEvent.stopPropagation();
          openDetail(event);
        });
        cell.append(chip);
      }
      if (dayEvents.length > MAX_MONTH_EVENTS) {
        const more = el("div", "month-more", `+ ${dayEvents.length - MAX_MONTH_EVENTS} autre(s)`);
        more.addEventListener("click", (clickEvent) => {
          clickEvent.stopPropagation();
          cursor = day;
          setView("day");
        });
        cell.append(more);
      }
      cell.addEventListener("dragover", (drag) => {
        drag.preventDefault();
        cell.classList.add("drop-target");
      });
      cell.addEventListener("dragleave", () => cell.classList.remove("drop-target"));
      cell.addEventListener("drop", async (drag) => {
        drag.preventDefault();
        cell.classList.remove("drop-target");
        const moved = events.find((item) => item.id === drag.dataTransfer.getData("text/plain"));
        if (!moved) return;
        const old = new Date(moved.starts_at);
        await moveEvent(
          moved,
          new Date(day.getFullYear(), day.getMonth(), day.getDate(), old.getHours(), old.getMinutes()),
        );
      });
      cell.addEventListener("click", () => {
        cursor = day;
        setView("day");
      });
      grid.append(cell);
    }
    root.append(head, grid);
    calendar.replaceChildren(root);
  }

  function render() {
    document.querySelector("#periodLabel").textContent = periodLabel();
    document.querySelector(".calendar-page")?.setAttribute("data-view", view);
    for (const button of document.querySelectorAll("[data-view]")) {
      button.classList.toggle("active", button.dataset.view === view);
      button.setAttribute("aria-pressed", String(button.dataset.view === view));
    }
    if (view === "month") {
      document.querySelector("#calendarLegend").hidden = true;
      return renderMonth();
    }
    renderLegend();
    if (view === "day") return renderTimeGrid([cursor]);
    const first = mondayOf(cursor);
    return renderTimeGrid(Array.from({ length: 7 }, (_, i) => addDays(first, i)));
  }

  // Groupes d'agendas : ensemble de salles affiché ensemble (le filtre « Salle » est alors ignoré).
  let groups = [];
  const groupFilter = () => document.querySelector("#groupFilter");
  function groupRoomIds() {
    const group = groups.find((entry) => entry.id === groupFilter().value);
    return group ? new Set(group.room_ids) : null;
  }

  function populateGroups() {
    const saved = groupFilter().value || localStorage.getItem("calendarGroup") || "";
    populate(groupFilter(), groups, "Aucun");
    groupFilter().value = groups.some((group) => group.id === saved) ? saved : "";
  }

  async function loadGroups() {
    try {
      groups = await request("/agenda-groups");
    } catch {
      groups = [];
    }
    populateGroups();
  }

  async function load() {
    const token = ++loadToken;
    const [start, end] = visibleRange();
    const params = new URLSearchParams({
      start: start.toISOString(),
      end: end.toISOString(),
    });
    const room = singlePick(document.querySelector("#roomFilter"));
    const doctor = singlePick(document.querySelector("#doctorFilter"));
    const pickedRooms = roomPicks();
    const pickedDoctors = doctorPicks();
    if (room) params.set("room_id", room);
    if (doctor) params.set("doctor_id", doctor);
    try {
      const availabilityParams = new URLSearchParams({ start: isoDay(start), end: isoDay(end) });
      if (room) availabilityParams.set("room_id", room);
      if (doctor) availabilityParams.set("doctor_id", doctor);
      const [data, slots, exceptionRows, colorRows] = await Promise.all([
        request(`/range-view?${params}`),
        request(`/availability?${availabilityParams}`),
        request(`/schedule-exceptions?start=${isoDay(start)}&end=${isoDay(end)}`).catch(() => []),
        request("/statuses").catch(() => statusList),
      ]);
      if (token !== loadToken) return;
      statusList = colorRows;
      statusColors = Object.fromEntries(colorRows.map((item) => [item.code, item.color]));
      const inGroup = groupRoomIds();
      const roomOk = (id) => !pickedRooms.length || pickedRooms.includes(id);
      const doctorOk = (id) => !pickedDoctors.length || pickedDoctors.includes(id);
      exceptions = exceptionRows.filter((item) =>
        (!item.room_id || roomOk(item.room_id))
        && (!item.resource_id || doctorOk(item.resource_id))
        && (!inGroup || !item.room_id || inGroup.has(item.room_id)));
      // Un patient transféré hors du groupe reste dans la colonne de sa salle d'origine : le groupe n'affiche que ses salles.
      const withLane = (list) => list.map((event) => {
        const moved = event.current_location_id && (!inGroup || inGroup.has(event.current_location_id));
        return {
          ...event,
          lane_room_id: moved ? event.current_location_id : event.room_id,
          lane_room: moved ? event.current_location : event.room,
        };
      });
      events = withLane((inGroup ? data.filter((event) => inGroup.has(event.room_id)) : data)
        .filter((event) => roomOk(event.room_id) && doctorOk(event.doctor_id)));
      availability = {
        blocks: (inGroup ? slots.blocks.filter((block) => inGroup.has(block.room_id)) : slots.blocks)
          .filter((block) => roomOk(block.room_id) && (!block.resource_id || doctorOk(block.resource_id))),
        restricted: new Set(slots.restricted_room_ids),
      };
      showMessage(events.length ? "" : "Aucun rendez-vous sur cette période.");
    } catch (error) {
      if (token !== loadToken) return;
      events = [];
      availability = { blocks: [], restricted: new Set() };
      exceptions = [];
      showMessage(error.message, true);
    }
    render();
  }

  function setView(next) {
    view = next;
    localStorage.setItem("calendarView", view);
    load();
  }

  function shift(direction) {
    if (view === "day") cursor = addDays(cursor, direction);
    else if (view === "week") cursor = addDays(cursor, 7 * direction);
    else cursor = new Date(cursor.getFullYear(), cursor.getMonth() + direction, 1);
    load();
  }

  async function loadReferenceData() {
    const [roomRows, typeRows, resourceRows, patientRows] = await Promise.all([
      request("/rooms"),
      request("/appointment-types"),
      request("/resources"),
      request("/patients"),
    ]);
    rooms.splice(0, rooms.length, ...roomRows);
    const doctors = resourceRows.filter((resource) => resource.kind === "DOCTOR");
    populate(document.querySelector("#roomFilter"), roomRows, "Toutes");
    populate(document.querySelector("#doctorFilter"), doctors, "Tous");
    populate(document.querySelector("#appointmentRoom"), roomRows, "Choisir une salle");
    populate(document.querySelector("#appointmentType"), typeRows, "Sans type");
    populate(document.querySelector("#doctor"), doctors, "Aucun médecin");
    populate(
      document.querySelector("#operator"),
      resourceRows.filter((resource) => resource.kind === "OPERATOR"),
      "Aucun opérateur",
    );
    populate(
      document.querySelector("#patient"),
      patientRows.map(patientOption),
      "Choisir un patient",
    );
    document.querySelector("#newAppointment").disabled = roomRows.length === 0;
  }

  const isAdminUser = () => ["admin", "superadmin"].includes(window.AppAuth.user()?.role);

  let patientFilterTimer;
  let patientMatches = [];
  let patientActive = -1;
  const patientPanel = () => document.querySelector("#patientResults");
  function setPatientPanel(open) {
    patientPanel().hidden = !open;
    document.querySelector("#patientFilter").setAttribute("aria-expanded", String(open));
  }
  function highlightPatient(index) {
    const items = [...patientPanel().children];
    patientActive = items.length ? (index + items.length) % items.length : -1;
    items.forEach((item, i) => item.classList.toggle("active", i === patientActive));
    items[patientActive]?.scrollIntoView({ block: "nearest" });
  }
  function renderPatientPanel() {
    const panel = patientPanel();
    panel.replaceChildren();
    for (const row of patientMatches) {
      const item = document.createElement("div");
      item.setAttribute("role", "option");
      item.dataset.id = row.id;
      item.className = "patient-result";
      const name = document.createElement("span");
      name.textContent = row.display_name;
      const born = document.createElement("small");
      born.textContent = row.is_temporary ? "temporaire" : new Date(`${row.date_naissance}T00:00:00`).toLocaleDateString("fr-BE");
      item.append(name, born);
      panel.append(item);
    }
    if (!patientMatches.length) {
      const empty = document.createElement("div");
      empty.className = "patient-result empty";
      empty.textContent = "Aucun patient trouvé";
      panel.append(empty);
    }
    patientActive = -1;
  }
  function choosePatient(id) {
    const select = document.querySelector("#patient");
    select.value = String(id);
    select._refresh?.();
    select.dispatchEvent(new Event("change", { bubbles: true }));
    setPatientPanel(false);
  }
  async function refreshPatientOptions(keepId) {
    const select = document.querySelector("#patient");
    const params = new URLSearchParams();
    const text = document.querySelector("#patientFilter").value.trim();
    if (text) params.set("query", text);
    const kept = keepId ?? select.value;
    if (kept) params.set("include_id", kept);
    try {
      const rows = await request(`/patients?${params}`);
      populate(select, rows.map(patientOption), "Choisir un patient");
      select.value = kept || "";
      patientMatches = rows;
      renderPatientPanel();
    } catch (error) {
      showMessage(error.message, true);
    }
  }
  const patientOption = (patient) => ({
    id: patient.id,
    display_name: patient.is_temporary ? `${patient.display_name} (temporaire)` : patient.display_name,
  });
  const schedulePatientRefresh = () => {
    clearTimeout(patientFilterTimer);
    patientFilterTimer = setTimeout(refreshPatientOptions, 250);
  };
  const patientFilterInput = document.querySelector("#patientFilter");
  patientFilterInput.addEventListener("input", () => { setPatientPanel(true); schedulePatientRefresh(); });
  patientFilterInput.addEventListener("focus", () => setPatientPanel(true));
  patientFilterInput.addEventListener("blur", () => setPatientPanel(false));
  patientFilterInput.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setPatientPanel(true);
      highlightPatient(patientActive + (event.key === "ArrowDown" ? 1 : -1));
    } else if (event.key === "Enter" && !patientPanel().hidden && patientActive >= 0) {
      event.preventDefault();
      choosePatient(patientPanel().children[patientActive].dataset.id);
    } else if (event.key === "Escape" && !patientPanel().hidden) {
      event.stopPropagation();
      event.preventDefault();
      setPatientPanel(false);
    }
  });
  // mousedown (et non click) pour choisir avant que le champ perde le focus.
  patientPanel().addEventListener("mousedown", (event) => {
    event.preventDefault();
    const item = event.target.closest(".patient-result[data-id]");
    if (item) choosePatient(item.dataset.id);
  });

  // Fermeture bloquante couvrant le créneau (salle et médecin connus) : seuls les administrateurs peuvent forcer.
  function closureFor(start, end, roomId, doctorId) {
    const day = isoDay(start);
    const from = start.getHours() * 60 + start.getMinutes();
    const to = isoDay(end) === day ? end.getHours() * 60 + end.getMinutes() : 24 * 60;
    return exceptions.find((item) => item.blocks_booking
      && item.start_date <= day && item.end_date >= day
      && (!item.room_id || item.room_id === roomId)
      && (!item.resource_id || item.resource_id === doctorId)
      && from < (item.end_time ? toMinutes(item.end_time) : 24 * 60)
      && to > (item.start_time ? toMinutes(item.start_time) : 0));
  }

  function openCreate(start, durationMinutes = 30, roomId = "", extra = {}) {
    const base = start || new Date();
    if (!start) {
      base.setMinutes(Math.ceil(base.getMinutes() / 30) * 30, 0, 0);
    }
    // La colonne cliquée prime ; sinon le filtre Salle, sinon l'unique salle du groupe affiché.
    const groupRooms = groupRoomIds();
    const room = extra.laneRoom
      || singlePick(document.querySelector("#roomFilter"))
      || roomId
      || (groupRooms && groupRooms.size === 1 ? [...groupRooms][0] : "");
    if (!isAdminUser()) {
      const closed = closureFor(base, new Date(base.getTime() + durationMinutes * 60_000), room, extra.doctorId || singlePick(document.querySelector("#doctorFilter")));
      if (closed) {
        showMessage(`Planning indisponible : ${exceptionLabel(closed)}. Aucun rendez-vous ne peut être ajouté.`, true);
        return;
      }
    }
    document.querySelector("#appointmentForm").reset();
    refreshPatientOptions("");
    document.querySelector("#startsAt").value = toLocalInput(base);
    document.querySelector("#endsAt").value = toLocalInput(
      new Date(base.getTime() + durationMinutes * 60_000),
    );
    if (room) document.querySelector("#appointmentRoom").value = room;
    syncShowDoctorDefault();
    if (extra.doctorId) document.querySelector("#doctor").value = extra.doctorId;
    if (extra.typeId) document.querySelector("#appointmentType").value = extra.typeId;
    document.querySelector("#appointmentError").textContent = "";
    document.querySelector("#freeSlots").hidden = true;
    editingId = null;
    document.querySelector("#appointmentTitle").textContent = "Nouveau rendez-vous";
    document.querySelector("#appointmentSubmit").textContent = "Créer";
    document.querySelector("#appointmentDialog").showModal();
  }

  // Nouveau rendez-vous : la case reprend le réglage de la salle choisie (modifiable pour ce rendez-vous).
  function syncShowDoctorDefault() {
    const room = rooms.find((item) => item.id === document.querySelector("#appointmentRoom").value);
    document.querySelector("#showDoctor").checked = Boolean(room?.show_doctor);
    document.querySelector("#showStatus").checked = Boolean(room?.show_status);
  }
  document.querySelector("#appointmentRoom").addEventListener("change", () => {
    if (!editingId) syncShowDoctorDefault();
  });

  function openEdit(event) {
    const value = (selector, content) => { document.querySelector(selector).value = content ?? ""; };
    document.querySelector("#appointmentForm").reset();
    editingId = event.id;
    refreshPatientOptions(event.patient_id).then(() => value("#patient", event.patient_id));
    value("#patient", event.patient_id);
    value("#appointmentType", event.appointment_type_id);
    value("#appointmentRoom", event.room_id);
    value("#doctor", event.doctor_id);
    value("#operator", event.operator_id);
    document.querySelector("#showDoctor").checked = Boolean(event.show_doctor);
    document.querySelector("#showStatus").checked = Boolean(event.show_status);
    value("#startsAt", toLocalInput(new Date(event.starts_at)));
    value("#endsAt", toLocalInput(new Date(event.ends_at)));
    value("#notes", event.notes);
    document.querySelector("#appointmentError").textContent = "";
    document.querySelector("#freeSlots").hidden = true;
    document.querySelector("#appointmentTitle").textContent = `Modifier le rendez-vous de ${event.patient_name}`;
    document.querySelector("#appointmentSubmit").textContent = "Enregistrer";
    document.querySelector("#detailDialog").close();
    document.querySelector("#appointmentDialog").showModal();
  }

  // Fenêtre de confirmation de l'application (remplace window.confirm) ; la croix ou Échap = refus.
  function confirmAction(title, text, yesLabel, danger = false) {
    const dialog = document.querySelector("#confirmDialog");
    const yes = document.querySelector("#confirmYes");
    document.querySelector("#confirmTitle").textContent = title;
    document.querySelector("#confirmText").textContent = text;
    yes.textContent = yesLabel;
    yes.classList.toggle("danger", danger);
    return new Promise((resolve) => {
      let answer = false;
      const finish = () => {
        yes.removeEventListener("click", onYes);
        document.querySelector("#confirmNo").removeEventListener("click", onNo);
        dialog.removeEventListener("close", finish);
        resolve(answer);
      };
      const onYes = () => { answer = true; dialog.close(); };
      const onNo = () => dialog.close();
      yes.addEventListener("click", onYes);
      document.querySelector("#confirmNo").addEventListener("click", onNo);
      dialog.addEventListener("close", finish);
      dialog.showModal();
    });
  }
  window.confirmAction = confirmAction;

  document.querySelector("#detailEdit").addEventListener("click", () => openEdit(currentEvent));
  document.querySelector("#detailOverbook").addEventListener("click", () => {
    const event = currentEvent;
    document.querySelector("#detailDialog").close();
    const start = new Date(event.starts_at);
    const minutes = Math.max(5, Math.round((new Date(event.ends_at) - start) / 60_000));
    openCreate(start, minutes, event.room_id, { laneRoom: event.room_id, doctorId: event.doctor_id || "" });
    if (document.querySelector("#appointmentDialog").open) {
      showMessage("Surréservation : recherchez le patient à ajouter sur cette plage.");
      document.querySelector("#patientFilter").focus();
    }
  });
  document.querySelector("#detailFollowUp").addEventListener("click", () => {
    window.openDayFollowUp(currentEvent);
  });
  document.querySelector("#detailTransfer").addEventListener("click", () => {
    document.querySelector("#detailDialog").close();
    window.openTransferDialog(currentEvent);
  });
  document.querySelector("#detailCancel").addEventListener("click", async () => {
    if (!currentEvent) return;
    const confirmed = await confirmAction(
      "Annuler le rendez-vous",
      `Le rendez-vous de ${currentEvent.patient_name} sera marqué « Annulé » (il reste dans l'historique).`,
      "Annuler le rendez-vous",
    );
    if (!confirmed) return;
    try {
      await request(`/appointments/${currentEvent.id}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_state: "CANCELLED", comment: "Annulé depuis le calendrier" }),
      });
      document.querySelector("#detailDialog").close();
      showMessage("Rendez-vous annulé.");
    } catch (error) {
      showMessage(error.message, true);
    }
    await load();
  });
  document.querySelector("#detailDelete").addEventListener("click", async () => {
    if (!currentEvent) return;
    const confirmed = await confirmAction(
      "Supprimer le rendez-vous",
      `Le rendez-vous de ${currentEvent.patient_name} sera supprimé définitivement (une trace reste dans le journal d'audit).`,
      "Supprimer",
      true,
    );
    if (!confirmed) return;
    try {
      await request(`/appointments/${currentEvent.id}`, { method: "DELETE" });
      document.querySelector("#detailDialog").close();
      showMessage("Rendez-vous supprimé.");
    } catch (error) {
      showMessage(error.message, true);
    }
    await load();
  });

  const newPatientDialog = document.querySelector("#newPatientDialog");
  const patientError = document.querySelector("#npError");
  document.querySelector("#npTemporary").addEventListener("change", (event) => {
    document.querySelector("#npDob").required = !event.target.checked;
  });
  const eidStatus = document.querySelector("#npEidStatus");
  const IDENTITY_INPUTS = {
    prenoms: "#npPrenoms", lieu_naissance: "#npLieu", sexe: "#npSexe", nationalite: "#npNationalite",
    adresse: "#npAdresse", code_postal: "#npCodePostal", commune: "#npCommune",
  };
  let cardPhoto = null;
  const formatNN = (value) => {
    const d = String(value || "").replace(/\D/g, "");
    return d.length === 11 ? `${d.slice(0, 2)}.${d.slice(2, 4)}.${d.slice(4, 6)}-${d.slice(6, 9)}.${d.slice(9)}` : d;
  };
  function showCardPhoto(photo) {
    cardPhoto = photo || null;
    const img = document.querySelector("#npPhoto");
    img.hidden = !cardPhoto;
    document.querySelector("#npPhotoEmpty").hidden = Boolean(cardPhoto);
    if (cardPhoto) img.src = `data:image/jpeg;base64,${cardPhoto}`;
  }
  const matchBox = document.querySelector("#npMatch");
  let matchedPatient = null;
  let forceCreate = false;
  const hideMatch = () => { matchBox.hidden = true; matchedPatient = null; };
  for (const id of ["#npNom", "#npPrenom", "#npDob"]) {
    document.querySelector(id).addEventListener("input", () => { forceCreate = false; hideMatch(); });
  }
  // Patient déjà connu (même nom, prénom et date de naissance) : mise à jour ou nouvelle fiche au choix.
  async function findExistingPatient() {
    const nom = document.querySelector("#npNom").value.trim();
    const prenom = document.querySelector("#npPrenom").value.trim();
    const dob = document.querySelector("#npDob").value;
    const nn = document.querySelector("#npNational").value.trim();
    if ((!nom || !prenom || !dob) && !nn) return null;
    if (document.querySelector("#npTemporary").checked) return null;
    const params = new URLSearchParams({ nom, prenom, date_naissance: dob });
    const national = document.querySelector("#npNational").value.trim();
    if (national) params.set("numero_national", national);
    return request(`/patients/match?${params}`);
  }
  async function offerMatch() {
    const existing = await findExistingPatient();
    if (!existing) return false;
    matchedPatient = existing;
    const born = new Date(`${existing.date_naissance}T00:00:00`).toLocaleDateString("fr-BE");
    document.querySelector("#npMatchText").textContent =
      `Ce patient est déjà connu : ${existing.display_name}, né(e) le ${born} (n° ${existing.id}). Mettre à jour sa fiche avec les informations saisies, ou créer un nouveau patient ?`;
    matchBox.hidden = false;
    return true;
  }
  const identityPayload = () => ({
    ...Object.fromEntries(Object.entries(IDENTITY_INPUTS).map(([key, selector]) => [key, document.querySelector(selector).value.trim() || null])),
    numero_national: document.querySelector("#npNational").value.trim() || null,
    photo: cardPhoto,
  });
  function selectPatientOption(id, label) {
    const select = document.querySelector("#patient");
    if (![...select.options].some((option) => option.value === String(id))) {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = label;
      select.append(option);
    }
    select.value = String(id);
    select._refresh?.();
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }
  document.querySelector("#npMatchUpdate").addEventListener("click", async () => {
    patientError.textContent = "";
    try {
      const body = identityPayload();
      if (Object.values(body).some(Boolean)) {
        await request(`/patients/${matchedPatient.id}/identity`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }
      selectPatientOption(matchedPatient.id, matchedPatient.display_name);
      showMessage("Fiche du patient mise à jour.");
      newPatientDialog.close();
    } catch (error) {
      patientError.textContent = error.message;
    }
  });
  document.querySelector("#npMatchCreate").addEventListener("click", () => {
    forceCreate = true;
    hideMatch();
    document.querySelector("#newPatientForm").requestSubmit();
  });
  document.querySelector("#npReadEid").addEventListener("click", async () => {
    hideMatch();
    forceCreate = false;
    eidStatus.textContent = "Lecture de la carte…";
    patientError.textContent = "";
    try {
      const card = await window.EidReader.read();
      document.querySelector("#npNom").value = card.nom || "";
      document.querySelector("#npPrenom").value = card.prenom || "";
      if (card.date_naissance) document.querySelector("#npDob").value = card.date_naissance;
      for (const [key, selector] of Object.entries(IDENTITY_INPUTS)) {
        document.querySelector(selector).value = card[key] || "";
      }
      document.querySelector("#npNational").value = formatNN(card.numero_national);
      showCardPhoto(card.photo);
      document.querySelector(".np-identity").open = true;
      document.querySelector("#npTemporary").checked = false;
      document.querySelector("#npDob").required = true;
      eidStatus.textContent = card.date_naissance
        ? "Carte lue. Vérifiez les informations avant de créer le patient."
        : "Carte lue, mais la date de naissance est à saisir.";
      await offerMatch();
    } catch (error) {
      eidStatus.textContent = "";
      patientError.textContent = error.message;
    }
  });
  document.querySelector("#newPatientBtn").addEventListener("click", () => {
    eidStatus.textContent = "";
    hideMatch();
    forceCreate = false;
    showCardPhoto(null);
    document.querySelector("#newPatientForm").reset();
    document.querySelector("#npDob").required = true;
    document.querySelector(".np-identity").open = false;
    patientError.textContent = "";
    newPatientDialog.showModal();
    document.querySelector("#npNom").focus();
  });
  document.querySelector("#npCancel").addEventListener("click", () => newPatientDialog.close());
  document.querySelector("#newPatientForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    patientError.textContent = "";
    const field = (id) => document.querySelector(id).value.trim();
    try {
      if (!forceCreate && await offerMatch()) return;
      const created = await request("/patients", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          nom: field("#npNom"),
          prenom: field("#npPrenom"),
          date_naissance: field("#npDob") || null,
          mobile: field("#npMobile") || null,
          email: field("#npEmail") || null,
          language: field("#npLanguage") || null,
          temporary: document.querySelector("#npTemporary").checked,
          force: forceCreate,
          numero_national: field("#npNational") || null,
          photo: cardPhoto,
          ...Object.fromEntries(Object.entries(IDENTITY_INPUTS).map(([key, selector]) => [key, field(selector) || null])),
        }),
      });
      const select = document.querySelector("#patient");
      const option = document.createElement("option");
      option.value = created.id;
      option.textContent = created.is_temporary ? `${created.display_name} (temporaire)` : created.display_name;
      select.append(option);
      select.value = String(created.id);
      select._refresh?.();
      select.dispatchEvent(new Event("change", { bubbles: true }));
      newPatientDialog.close();
    } catch (error) {
      patientError.textContent = error.message;
    }
  });

  // Réconciliation des patients temporaires avec la base patient (à l'accueil).
  const temporaryDialog = document.querySelector("#temporaryDialog");
  const reconcileDialog = document.querySelector("#reconcileDialog");
  const rcError = document.querySelector("#rcError");
  const rcField = (id) => document.querySelector(id).value.trim();
  let reconcileId = null;
  let reconcileTimer;

  const reconcileMode = () => document.querySelector("input[name=reconcileMode]:checked").value;
  function syncReconcileMode() {
    const merge = reconcileMode() === "merge";
    document.querySelector("#reconcileConvert").hidden = merge;
    document.querySelector("#reconcileMerge").hidden = !merge;
  }
  document.querySelectorAll("input[name=reconcileMode]").forEach((radio) => radio.addEventListener("change", syncReconcileMode));

  async function searchReconcileTargets() {
    const term = rcField("#rcSearch");
    const target = document.querySelector("#rcTarget");
    target.replaceChildren();
    if (term.length < 2) return;
    const rows = await request(`/patients?query=${encodeURIComponent(term)}`);
    for (const row of rows.filter((patient) => !patient.is_temporary)) {
      const option = document.createElement("option");
      option.value = row.id;
      option.textContent = `${row.display_name} · né(e) le ${row.date_naissance} · n° ${row.id}`;
      target.append(option);
    }
    if (!target.options.length) target.append(new Option("Aucun dossier trouvé", "", false, false));
  }
  document.querySelector("#rcSearch").addEventListener("input", () => {
    clearTimeout(reconcileTimer);
    reconcileTimer = setTimeout(() => searchReconcileTargets().catch((error) => { rcError.textContent = error.message; }), 250);
  });

  async function openReconcile(patientId) {
    rcError.textContent = "";
    reconcileId = patientId;
    document.querySelector("#reconcileForm").reset();
    syncReconcileMode();
    document.querySelector("#rcTarget").replaceChildren();
    try {
      const temp = (await request("/patients/temporary")).find((row) => row.id === patientId);
      if (!temp) throw new Error("Ce patient n'est plus temporaire.");
      document.querySelector("#reconcileTitle").textContent = `Réconcilier ${temp.prenom} ${temp.nom}`;
      document.querySelector("#rcNom").value = temp.nom;
      document.querySelector("#rcPrenom").value = temp.prenom;
      document.querySelector("#rcMobile").value = temp.mobile || "";
      document.querySelector("#rcEmail").value = temp.email || "";
      document.querySelector("#rcSearch").value = temp.nom;
      searchReconcileTargets().catch(() => {});
      reconcileDialog.showModal();
    } catch (error) {
      showMessage(error.message, true);
    }
  }

  async function renderTemporaryList() {
    const list = document.querySelector("#temporaryList");
    list.replaceChildren(el("p", "hint", "Chargement…"));
    try {
      const rows = await request("/patients/temporary");
      list.replaceChildren();
      if (!rows.length) list.append(el("p", "hint", "Aucun patient temporaire à réconcilier."));
      for (const row of rows) {
        const card = el("article", "deleted-appointment is-temp");
        const info = el("div", "deleted-appointment-info");
        const name = el("strong", "temp-title");
        name.append(el("span", "temp-name", `${row.prenom} ${row.nom}`), tempChip());
        info.append(
          name,
          el("span", "", `Créé le ${new Date(row.created_at).toLocaleDateString("fr-BE")} · ${row.appointments} rendez-vous`),
        );
        if (row.mobile || row.email) info.append(el("span", "", [row.mobile, row.email].filter(Boolean).join(" · ")));
        const go = el("button", "secondary", "Réconcilier");
        go.type = "button";
        go.addEventListener("click", () => {
          temporaryDialog.close();
          openReconcile(row.id);
        });
        card.append(info, go);
        list.append(card);
      }
    } catch (error) {
      list.replaceChildren(el("p", "form-error", error.message));
    }
  }

  document.querySelector("#manageTemporary").addEventListener("click", () => {
    temporaryDialog.showModal();
    renderTemporaryList();
  });
  document.querySelector("#detailReconcile").addEventListener("click", () => {
    if (!currentEvent) return;
    document.querySelector("#detailDialog").close();
    openReconcile(currentEvent.patient_id);
  });
  document.querySelector("#rcCancel").addEventListener("click", () => reconcileDialog.close());
  document.querySelector("#reconcileForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    rcError.textContent = "";
    const merge = reconcileMode() === "merge";
    const body = merge
      ? { target_id: Number(document.querySelector("#rcTarget").value) || null }
      : {
        nom: rcField("#rcNom"),
        prenom: rcField("#rcPrenom"),
        date_naissance: rcField("#rcDob") || null,
        mobile: rcField("#rcMobile") || null,
        email: rcField("#rcEmail") || null,
        language: rcField("#rcLanguage") || null,
      };
    if (merge && !body.target_id) {
      rcError.textContent = "Sélectionnez le dossier patient existant.";
      return;
    }
    try {
      await request(`/patients/${reconcileId}/reconcile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      reconcileDialog.close();
      showMessage(merge ? "Patient lié au dossier existant." : "Patient intégré à la base patient.");
      await loadReferenceData();
      await load();
    } catch (error) {
      rcError.textContent = error.message;
    }
  });

  // Durée de la prestation : plage de la salle (et du médecin) à l'heure choisie, sinon type, sinon durée précédente.
  let previousDuration = 0;
  let durationToken = 0;
  const pickValue = (selector) => document.querySelector(selector).value;
  document.querySelector("#appointmentForm").addEventListener("focusin", () => {
    const span = new Date(pickValue("#endsAt")) - new Date(pickValue("#startsAt"));
    if (span > 0) previousDuration = span;
  });

  async function adaptDuration() {
    const start = new Date(pickValue("#startsAt"));
    if (Number.isNaN(start.getTime())) return;
    const token = ++durationToken;
    const room = pickValue("#appointmentRoom");
    const doctor = pickValue("#doctor");
    let minutes = 0;
    try {
      if (room) {
        const day = isoDay(start);
        const params = new URLSearchParams({ start: day, end: isoDay(addDays(start, 1)), room_id: room });
        if (doctor) params.set("doctor_id", doctor);
        const { blocks } = await request(`/availability?${params}`);
        const clock = formatTime(start);
        const matching = blocks.filter((block) => block.date === day && block.room_id === room
          && block.start_time <= clock && clock < block.end_time
          && (!block.resource_id || block.resource_id === doctor));
        const best = matching.find((block) => block.resource_id && block.resource_id === doctor) || matching[0];
        if (best) minutes = best.slot_minutes;
      }
      if (!minutes && pickValue("#appointmentType")) {
        const types = await request("/appointment-types");
        minutes = types.find((entry) => entry.id === pickValue("#appointmentType"))?.duration_minutes || 0;
      }
    } catch {
      /* on retombe sur la durée précédente */
    }
    if (token !== durationToken) return;
    const span = minutes ? minutes * 60_000 : previousDuration || 30 * 60_000;
    document.querySelector("#endsAt").value = toLocalInput(new Date(start.getTime() + span));
    previousDuration = span;
  }
  for (const selector of ["#startsAt", "#appointmentRoom", "#doctor"]) {
    document.querySelector(selector).addEventListener("change", adaptDuration);
  }

  document.querySelector("#appointmentType").addEventListener("change", async () => {
    // La durée proposée suit le type choisi.
    try {
      const types = await request("/appointment-types");
      const type = types.find((entry) => entry.id === document.querySelector("#appointmentType").value);
      const start = new Date(document.querySelector("#startsAt").value);
      if (type && !type.duration_minutes && !Number.isNaN(start.getTime())) {
        await adaptDuration();
      } else if (type?.duration_minutes && !Number.isNaN(start.getTime())) {
        document.querySelector("#endsAt").value = toLocalInput(
          new Date(start.getTime() + type.duration_minutes * 60_000),
        );
      }
    } catch {
      /* la durée reste modifiable manuellement */
    }
  });

  // En modification, le choix est toujours enregistré ; en création, seulement s'il diffère de la salle.
  function showDoctorChoice() {
    const checked = document.querySelector("#showDoctor").checked;
    if (editingId) return checked;
    const room = rooms.find((item) => item.id === document.querySelector("#appointmentRoom").value);
    return checked === Boolean(room?.show_doctor) ? null : checked;
  }

  function showStatusChoice() {
    const checked = document.querySelector("#showStatus").checked;
    if (editingId) return checked;
    const room = rooms.find((item) => item.id === document.querySelector("#appointmentRoom").value);
    return checked === Boolean(room?.show_status) ? null : checked;
  }

  document.querySelector("#appointmentForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = (selector) => document.querySelector(selector).value;
    try {
      const body = JSON.stringify({
          patient_id: Number(value("#patient")),
          appointment_type_id: value("#appointmentType") || null,
          room_id: value("#appointmentRoom"),
          doctor_id: value("#doctor") || null,
          operator_id: value("#operator") || null,
          starts_at: new Date(value("#startsAt")).toISOString(),
          ends_at: new Date(value("#endsAt")).toISOString(),
          notes: value("#notes").trim() || null,
          phone: editingId ? currentEvent?.phone || null : null,
          show_doctor: showDoctorChoice(),
          show_status: showStatusChoice(),
      });
      await request(editingId ? `/appointments/${editingId}` : "/appointments", {
        method: editingId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
      document.querySelector("#appointmentDialog").close();
      await load();
      showMessage(editingId ? "Rendez-vous modifié." : "Rendez-vous créé.");
    } catch (error) {
      document.querySelector("#appointmentError").textContent = error.message;
      if (/occup|créneau|plage/i.test(error.message)) showFreeSlots();
    }
  });

  // Salle occupée : propose les plages libres (même salle / médecin, durée voulue) sous le message.
  async function showFreeSlots() {
    const box = document.querySelector("#freeSlots");
    box.replaceChildren();
    box.hidden = true;
    const start = new Date(pickValue("#startsAt"));
    const end = new Date(pickValue("#endsAt"));
    if (Number.isNaN(start.getTime()) || end <= start || !pickValue("#appointmentRoom")) return;
    const minutes = Math.round((end - start) / 60_000);
    const params = new URLSearchParams({
      start: isoDay(start),
      end: isoDay(addDays(start, 14)),
      duration: minutes,
      room_id: pickValue("#appointmentRoom"),
      limit: 8,
    });
    if (pickValue("#doctor")) params.set("doctor_id", pickValue("#doctor"));
    try {
      const { suggestions } = await request(`/suggestions?${params}`);
      const free = suggestions.filter((item) => new Date(item.starts_at) >= new Date());
      box.hidden = false;
      box.append(el("strong", "", free.length ? "Plages libres proposées :" : "Aucune plage libre dans les 15 prochains jours."));
      for (const item of free) {
        const from = new Date(item.starts_at);
        const to = new Date(item.ends_at);
        const label = `${from.toLocaleDateString("fr-BE", { weekday: "short", day: "numeric", month: "short" })} · ${formatTime(from)}–${formatTime(to)}${item.room ? ` · ${item.room}` : ""}`;
        const choice = el("button", "ghost", label);
        choice.type = "button";
        choice.addEventListener("click", () => {
          document.querySelector("#startsAt").value = toLocalInput(from);
          document.querySelector("#endsAt").value = toLocalInput(to);
          document.querySelector("#appointmentError").textContent = "";
          box.hidden = true;
          box.replaceChildren();
        });
        box.append(choice);
      }
    } catch {
      /* la liste est facultative */
    }
  }

  document.querySelector("#prev").addEventListener("click", () => shift(-1));
  document.querySelector("#next").addEventListener("click", () => shift(1));
  document.querySelector("#today").addEventListener("click", () => {
    cursor = startOfDay(new Date());
    load();
  });
  document.querySelectorAll("[data-view]").forEach((button) =>
    button.addEventListener("click", () => setView(button.dataset.view)),
  );
  document.querySelector("#roomFilter").addEventListener("change", () => {
    if (roomPicks().length) {
      groupFilter().value = "";
      localStorage.removeItem("calendarGroup");
    }
    load();
  });
  groupFilter().addEventListener("change", () => {
    if (groupFilter().value) clearPicks(document.querySelector("#roomFilter"));
    localStorage.setItem("calendarGroup", groupFilter().value);
    load();
  });
  document.addEventListener("groups:changed", (event) => {
    groups = event.detail;
    populateGroups();
    load();
  });
  document.querySelector("#doctorFilter").addEventListener("change", load);
  document.querySelector("#newAppointment").addEventListener("click", () => openCreate());
  const deletedDialog = document.querySelector("#deletedAppointmentsDialog");
  const deletedList = document.querySelector("#deletedAppointmentsList");
  const deletedMessage = document.querySelector("#deletedAppointmentsMessage");
  const deletedSearch = document.querySelector("#deletedAppointmentsSearch");
  let deletedRows = [];
  function renderDeletedRows() {
    deletedList.replaceChildren();
    const query = deletedSearch.value.trim().toLocaleLowerCase("fr");
    const rows = deletedRows.filter((item) => [
      item.patient_id,
      item.patient_name,
      item.appointment_type,
      item.room,
      item.doctor,
      item.notes,
      item.starts_at,
      new Date(item.starts_at).toLocaleDateString("fr-BE"),
    ].filter(Boolean).join(" ").toLocaleLowerCase("fr").includes(query));
    if (!rows.length) {
      deletedList.append(el("p", "deleted-empty",
        deletedRows.length ? "Aucun rendez-vous ne correspond à cette recherche." : "Aucun rendez-vous supprimé à récupérer."));
      return;
    }
    for (const item of rows) {
      const card = el("article", "deleted-appointment");
      const start = new Date(item.starts_at);
      const end = new Date(item.ends_at);
      const info = el("div", "deleted-appointment-info");
      info.append(
        el("strong", "", item.patient_name),
        el("span", "", `${start.toLocaleDateString("fr-BE", { weekday: "long", day: "numeric", month: "long", year: "numeric" })} · ${formatTime(start)}–${formatTime(end)}`),
        el("span", "", [item.appointment_type, item.room, item.doctor].filter(Boolean).join(" · ")),
      );
      const actions = el("div", "deleted-appointment-actions");
      const restore = el("button", "", "Récupérer à la date initiale");
      restore.type = "button";
      const newDate = el("input");
      newDate.type = "datetime-local";
      newDate.value = toLocalInput(start);
      newDate.setAttribute("aria-label", `Nouvelle date pour ${item.patient_name}`);
      const reschedule = el("button", "secondary", "Rechercher une plage libre");
      reschedule.type = "button";
      const proposals = el("div", "deleted-slot-proposals");
      const restoreAt = async (startsAt) => {
        restore.disabled = true;
        reschedule.disabled = true;
        deletedMessage.textContent = "";
        try {
          const body = startsAt ? { starts_at: new Date(startsAt).toISOString() } : {};
          await request(`/deleted-appointments/${item.id}/restore`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          await load();
          deletedDialog.close();
        } catch (error) {
          deletedMessage.textContent = error.message;
          restore.disabled = false;
          reschedule.disabled = false;
        }
      };
      restore.addEventListener("click", () => restoreAt(null));
      reschedule.addEventListener("click", async () => {
        const requestedStart = new Date(newDate.value);
        if (!newDate.value || Number.isNaN(requestedStart.getTime())) {
          deletedMessage.textContent = "Choisissez une date et une heure valides.";
          return;
        }
        deletedMessage.textContent = "";
        proposals.replaceChildren();
        reschedule.disabled = true;
        try {
          const duration = Math.max(1, Math.round((end - start) / 60_000));
          const searchEnd = addDays(new Date(requestedStart.getFullYear(), requestedStart.getMonth(), requestedStart.getDate()), 15);
          const params = new URLSearchParams({
            start: isoDay(requestedStart),
            end: isoDay(searchEnd),
            duration: String(duration),
            room_id: item.room_id,
            limit: "8",
            preferred_time: formatTime(requestedStart),
          });
          if (item.doctor_id) params.set("doctor_id", item.doctor_id);
          const result = await request(`/suggestions?${params}`);
          const slots = result.suggestions.filter((slot) => new Date(slot.starts_at) >= requestedStart);
          proposals.append(el("strong", "", slots.length
            ? "Plages libres à partir de la date choisie :"
            : "Aucune plage libre trouvée à partir de cette date."));
          for (const slot of slots) {
            const slotStart = new Date(slot.starts_at);
            const slotEnd = new Date(slot.ends_at);
            const label = `${slotStart.toLocaleDateString("fr-BE", { weekday: "short", day: "numeric", month: "short" })} · ${formatTime(slotStart)}–${formatTime(slotEnd)}`;
            const choose = el("button", "ghost", label);
            choose.type = "button";
            choose.addEventListener("click", () => restoreAt(slot.starts_at));
            proposals.append(choose);
          }
        } catch (error) {
          deletedMessage.textContent = error.message;
        } finally {
          reschedule.disabled = false;
        }
      });
      const search = el("button", "icon-button deleted-search");
      search.type = "button";
      search.title = "Rechercher un autre créneau (salle, médecin, période…)";
      search.setAttribute("aria-label", search.title);
      search.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>';
      search.addEventListener("click", () => {
        document.dispatchEvent(new CustomEvent("assistant:open-restore", {
          detail: {
            id: item.id,
            typeId: item.appointment_type_id || "",
            duration: Math.max(1, Math.round((end - start) / 60_000)),
            roomId: item.room_id,
            doctorId: item.doctor_id || "",
          },
        }));
      });
      actions.append(restore, newDate, reschedule, search);
      card.append(info, actions, proposals);
      deletedList.append(card);
    }
  }
  async function renderDeletedAppointments() {
    deletedMessage.textContent = "";
    try {
      deletedRows = await request("/deleted-appointments");
      renderDeletedRows();
    } catch (error) {
      deletedList.replaceChildren();
      deletedMessage.textContent = error.message;
    }
  }
  deletedSearch.addEventListener("input", renderDeletedRows);
  document.querySelector("#openDeletedAppointments").addEventListener("click", async () => {
    deletedSearch.value = "";
    await renderDeletedAppointments();
    deletedDialog.showModal();
  });
  const patientDialog = document.querySelector("#patientSearchDialog");
  const patientInput = document.querySelector("#patientSearchInput");
  const patientMessage = document.querySelector("#patientSearchMessage");
  const patientPast = document.querySelector("#patientSearchPast");
  const patientList = document.querySelector("#patientSearchList");
  let patientTimer = null;
  let patientSeq = 0;
  let patientRows = [];
  function renderPatientRows() {
    patientList.replaceChildren();
    const now = new Date();
    const showPast = patientPast.getAttribute("aria-pressed") === "true";
    const future = patientRows.filter((item) => new Date(item.ends_at) >= now)
      .sort((a, b) => new Date(a.starts_at) - new Date(b.starts_at));
    const past = showPast ? patientRows.filter((item) => new Date(item.ends_at) < now) : [];
    const rows = [...future, ...past];
    if (!rows.length) {
      const hidden = patientRows.length - future.length;
      patientList.append(el("p", "deleted-empty", patientRows.length
        ? `Aucun rendez-vous à venir.${hidden ? ` Cochez « Afficher les rendez-vous précédents » (${hidden}).` : ""}`
        : "Aucun rendez-vous trouvé pour ce patient."));
      return;
    }
    for (const item of rows) {
      const start = new Date(item.starts_at);
      const end = new Date(item.ends_at);
      const card = el("article", "deleted-appointment patient-appointment");
      const info = el("div", "deleted-appointment-info");
      info.append(
        el("strong", "", `${item.patient_name} (n° ${item.patient_id})`),
        el("span", "", `${start.toLocaleDateString("fr-BE", { weekday: "long", day: "numeric", month: "long", year: "numeric" })} · ${formatTime(start)}–${formatTime(end)}`),
        el("span", "", [item.appointment_type, item.room, item.doctor, statusLabel(item.state)].filter(Boolean).join(" · ")),
      );
      if (item.patient_temporary) {
        const title = info.firstElementChild;
        title.classList.add("temp-title");
        title.replaceChildren(el("span", "temp-name", title.textContent), tempChip());
      }
      if (item.notes?.trim()) info.append(el("span", "patient-appointment-note", `💬 ${item.notes.trim()}`));
      const go = el("button", "secondary", "Afficher dans l’agenda");
      go.type = "button";
      go.addEventListener("click", () => {
        cursor = startOfDay(start);
        clearPicks(document.querySelector("#roomFilter"));
        clearPicks(document.querySelector("#doctorFilter"));
        groupFilter().value = "";
        localStorage.removeItem("calendarGroup");
        patientDialog.close();
        setView("day");
      });
      card.classList.add(end < now ? "is-past" : "is-future");
      card.append(info, go);
      patientList.append(card);
    }
  }
  async function searchPatientAppointments() {
    const term = patientInput.value.trim();
    const seq = ++patientSeq;
    patientMessage.textContent = "";
    patientRows = [];
    patientList.replaceChildren();
    if (term.length < 2) return;
    try {
      const rows = await request(`/patient-appointments?query=${encodeURIComponent(term)}`);
      if (seq !== patientSeq) return;
      patientRows = rows;
      renderPatientRows();
    } catch (error) {
      patientMessage.textContent = error.message;
    }
  }
  patientPast.addEventListener("click", () => {
    patientPast.setAttribute("aria-pressed", String(patientPast.getAttribute("aria-pressed") !== "true"));
    renderPatientRows();
  });
  patientInput.addEventListener("input", () => {
    clearTimeout(patientTimer);
    patientTimer = setTimeout(searchPatientAppointments, 250);
  });
  document.querySelector("#openPatientSearch").addEventListener("click", () => {
    patientInput.value = "";
    patientPast.setAttribute("aria-pressed", "false");
    patientRows = [];
    patientMessage.textContent = "";
    patientDialog.showModal();
    patientInput.focus();
  });
  document.addEventListener("blocks:changed", load);
  const agendaMenu = document.querySelector("#agendaMenu");
  agendaMenu.addEventListener("click", (event) => {
    if (event.target.closest(".agenda-menu-list > *")) agendaMenu.open = false;
  });
  document.addEventListener("click", (event) => {
    if (!agendaMenu.contains(event.target)) agendaMenu.open = false;
  });
  document.addEventListener("assistant:pick", async (event) => {
    const { start, duration, roomId, doctorId, typeId, restoreId } = event.detail;
    if (restoreId) {
      try {
        await request(`/deleted-appointments/${restoreId}/restore`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            starts_at: new Date(start).toISOString(),
            room_id: roomId,
            doctor_id: doctorId || null,
            appointment_type_id: typeId || null,
            duration_minutes: duration,
          }),
        });
        await load();
        deletedDialog.close();
      } catch (error) {
        deletedMessage.textContent = error.message;
        if (!deletedDialog.open) deletedDialog.showModal();
      }
      return;
    }
    openCreate(new Date(start), duration, roomId, { doctorId, typeId });
    // La salle proposée prime sur le filtre du calendrier.
    document.querySelector("#appointmentRoom").value = roomId;
  });
  document.querySelectorAll("[data-close]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelector(`#${button.dataset.close}`).close();
    });
  });

  const roleLabel = (role) =>
    role === "superadmin" ? "SuperAdmin" : role === "admin" ? "administrateur" : "utilisateur";

  function renderAuthState() {
    const user = window.AppAuth.user();
    document.querySelector("#loginForm").hidden = Boolean(user);
    document.querySelector("#authBar").hidden = !user;
    const isAdminUser = ["admin", "superadmin"].includes(user?.role);
    document.querySelector("#agendaMenu").hidden = !isAdminUser;
    document.querySelector("#signedInAs").textContent = user
      ? `Connecté : ${user.username} (${roleLabel(user.role)})`
      : "";
  }

  function showAuthStatus(text, type) {
    const status = document.querySelector("#authStatus");
    status.textContent = text;
    status.className = text ? `status ${type}` : "status";
  }

  document.querySelector("#loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = document.querySelector("#loginButton");
    button.disabled = true;
    try {
      await window.AppAuth.login(
        document.querySelector("#loginUsername").value.trim(),
        document.querySelector("#loginPassword").value,
      );
      document.querySelector("#loginPassword").value = "";
      showAuthStatus("", "");
      await initialize();
    } catch (error) {
      showAuthStatus(`Connexion impossible : ${error.message}`, "error");
    } finally {
      button.disabled = false;
    }
  });

  document.querySelector("#logoutButton").addEventListener("click", () => {
    window.AppAuth.logout();
    events = [];
    initialize();
  });

  async function initialize() {
    renderAuthState();
    try {
      const authResponse = await fetch(`${window.AppAuth.API_BASE}/api/auth/me`, {
        headers: window.AppAuth.headers(),
      });
      if (!authResponse.ok) {
        throw new Error("Connexion requise : utilisez le menu « Connexion » en haut à droite.");
      }
      await loadReferenceData();
      await loadGroups();
      await load();
    } catch (error) {
      events = [];
      render();
      showMessage(error.message, true);
    }
  }

  initialize();
})();
