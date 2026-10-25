"use strict";

(() => {
  const api = `${window.AppAuth.API_BASE}/api/agenda`;
  const calendar = document.querySelector("#calendar");
  const message = document.querySelector("#message");
  const HOUR_HEIGHT = 48;
  const DEFAULT_FIRST_HOUR = 7;
  const DEFAULT_LAST_HOUR = 20;
  const MAX_MONTH_EVENTS = 3;
  const STATE_LABELS = {
    PLANNED: "Planifié",
    PRESENT: "Présent",
    WAITING: "En attente",
    IN_PROGRESS: "En cours",
    RECOVERY: "Réveil",
    DISCHARGED: "Sorti",
    CANCELLED: "Annulé",
    NO_SHOW: "Absent",
  };

  const savedView = localStorage.getItem("calendarView");
  let view = ["day", "week", "month"].includes(savedView) ? savedView : "week";
  let cursor = startOfDay(new Date());
  let events = [];
  let availability = { blocks: [], restricted: new Set() };
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

  function populate(select, entries, placeholder) {
    const current = select.value;
    select.replaceChildren();
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = placeholder;
    select.append(empty);
    for (const entry of entries) {
      const option = document.createElement("option");
      option.value = entry.id;
      option.textContent = entry.display_name || entry.name;
      select.append(option);
    }
    select.value = current;
  }

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

  function eventColor(event) {
    const transferred = event.lane_room_id && event.lane_room_id !== event.room_id;
    const laneColor = transferred ? rooms.find((room) => room.id === event.lane_room_id)?.color : null;
    return laneColor || event.type_color || event.room_color || "#0f4c81";
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

  function openDetail(event) {
    const start = new Date(event.starts_at);
    const end = new Date(event.ends_at);
    const body = document.querySelector("#detailBody");
    body.replaceChildren(el("h2", "", event.patient_name));
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
      ["Statut", STATE_LABELS[event.state] || event.state],
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
      list.append(el("dt", "", label), el("dd", "", value));
    }
    body.append(list);
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
    document.querySelector("#detailOpen").href = `agenda.html?day=${isoDay(start)}`;
    const transferLink = document.querySelector("#detailTransfer");
    transferLink.hidden = !open;
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
  const isRestrictedScope = () => {
    const room = document.querySelector("#roomFilter").value;
    return Boolean(room) && availability.restricted.has(room);
  };

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
    head.append(el("div"));
    for (const day of days) {
      const cell = el("div", sameDay(day, new Date()) ? "is-today" : "");
      cell.append(
        document.createTextNode(day.toLocaleDateString("fr-BE", { weekday: "short" })),
        el("span", "day-number", String(day.getDate())),
      );
      head.append(cell);
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
        : view === "day" && !document.querySelector("#roomFilter").value ? rooms : [];
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
            head.draggable = true;
            head.classList.add("reorderable");
            head.title = "Glisser pour changer l'ordre des colonnes";
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
        block.title = eventTitle(event);
        block.style.setProperty("--event-color", eventColor(event));
        block.style.top = `${top}px`;
        block.style.height = `${height - 2}px`;
        const laneWidth = 100 / laneCount;
        block.style.left = `calc(${lane * laneWidth + (index / columns) * laneWidth}% + 2px)`;
        block.style.width = `calc(${laneWidth / columns}% - 4px)`;
        block.append(
          el("strong", "", event.patient_name),
          el("span", "", `${formatTime(new Date(event.starts_at))} · ${event.appointment_type || event.room}`),
        );
        block.addEventListener("click", () => {
          if (!suppressClick) openDetail(event);
        });
        if (!CLOSED_STATES.includes(event.state)) {
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
      const dayEvents = eventsOfDay(day);
      for (const event of dayEvents.slice(0, MAX_MONTH_EVENTS)) {
        const chip = el(
          "button",
          "month-event",
          `${formatTime(new Date(event.starts_at))} ${event.patient_name}`,
        );
        chip.type = "button";
        if (!CLOSED_STATES.includes(event.state)) {
          chip.draggable = true;
          chip.addEventListener("dragstart", (drag) => {
            drag.dataTransfer.setData("text/plain", event.id);
            drag.dataTransfer.effectAllowed = "move";
          });
        }
        chip.title = eventTitle(event);
        chip.style.setProperty("--event-color", eventColor(event));
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
    const room = document.querySelector("#roomFilter").value;
    const doctor = document.querySelector("#doctorFilter").value;
    if (room) params.set("room_id", room);
    if (doctor) params.set("doctor_id", doctor);
    try {
      const availabilityParams = new URLSearchParams({ start: isoDay(start), end: isoDay(end) });
      if (room) availabilityParams.set("room_id", room);
      if (doctor) availabilityParams.set("doctor_id", doctor);
      const [data, slots] = await Promise.all([
        request(`/range-view?${params}`),
        request(`/availability?${availabilityParams}`),
      ]);
      if (token !== loadToken) return;
      const inGroup = groupRoomIds();
      const withLane = (list) => list.map((event) => ({
        ...event,
        lane_room_id: event.current_location_id || event.room_id,
        lane_room: event.current_location || event.room,
      }));
      events = withLane(inGroup ? data.filter((event) => inGroup.has(event.room_id)) : data);
      availability = {
        blocks: inGroup ? slots.blocks.filter((block) => inGroup.has(block.room_id)) : slots.blocks,
        restricted: new Set(slots.restricted_room_ids),
      };
      showMessage(events.length ? "" : "Aucun rendez-vous sur cette période.");
    } catch (error) {
      if (token !== loadToken) return;
      events = [];
      availability = { blocks: [], restricted: new Set() };
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
      patientRows.map((patient) => ({ id: patient.id, display_name: patient.display_name })),
      "Choisir un patient",
    );
    document.querySelector("#newAppointment").disabled = roomRows.length === 0;
  }

  function openCreate(start, durationMinutes = 30, roomId = "", extra = {}) {
    const base = start || new Date();
    if (!start) {
      base.setMinutes(Math.ceil(base.getMinutes() / 30) * 30, 0, 0);
    }
    document.querySelector("#appointmentForm").reset();
    document.querySelector("#startsAt").value = toLocalInput(base);
    document.querySelector("#endsAt").value = toLocalInput(
      new Date(base.getTime() + durationMinutes * 60_000),
    );
    // La colonne cliquée prime ; sinon le filtre Salle, sinon l'unique salle du groupe affiché.
    const groupRooms = groupRoomIds();
    const room = extra.laneRoom
      || document.querySelector("#roomFilter").value
      || roomId
      || (groupRooms && groupRooms.size === 1 ? [...groupRooms][0] : "");
    if (room) document.querySelector("#appointmentRoom").value = room;
    if (extra.doctorId) document.querySelector("#doctor").value = extra.doctorId;
    if (extra.typeId) document.querySelector("#appointmentType").value = extra.typeId;
    document.querySelector("#appointmentError").textContent = "";
    editingId = null;
    document.querySelector("#appointmentTitle").textContent = "Nouveau rendez-vous";
    document.querySelector("#appointmentSubmit").textContent = "Créer";
    document.querySelector("#appointmentDialog").showModal();
  }

  function openEdit(event) {
    const value = (selector, content) => { document.querySelector(selector).value = content ?? ""; };
    document.querySelector("#appointmentForm").reset();
    editingId = event.id;
    value("#patient", event.patient_id);
    value("#appointmentType", event.appointment_type_id);
    value("#appointmentRoom", event.room_id);
    value("#doctor", event.doctor_id);
    value("#operator", event.operator_id);
    value("#startsAt", toLocalInput(new Date(event.starts_at)));
    value("#endsAt", toLocalInput(new Date(event.ends_at)));
    value("#notes", event.notes);
    document.querySelector("#appointmentError").textContent = "";
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

  document.querySelector("#appointmentType").addEventListener("change", async () => {
    // La durée proposée suit le type choisi.
    try {
      const types = await request("/appointment-types");
      const type = types.find((entry) => entry.id === document.querySelector("#appointmentType").value);
      const start = new Date(document.querySelector("#startsAt").value);
      if (type?.duration_minutes && !Number.isNaN(start.getTime())) {
        document.querySelector("#endsAt").value = toLocalInput(
          new Date(start.getTime() + type.duration_minutes * 60_000),
        );
      }
    } catch {
      /* la durée reste modifiable manuellement */
    }
  });

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
    }
  });

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
    if (document.querySelector("#roomFilter").value) {
      groupFilter().value = "";
      localStorage.removeItem("calendarGroup");
    }
    load();
  });
  groupFilter().addEventListener("change", () => {
    if (groupFilter().value) document.querySelector("#roomFilter").value = "";
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
  document.addEventListener("blocks:changed", load);
  const agendaMenu = document.querySelector("#agendaMenu");
  agendaMenu.addEventListener("click", (event) => {
    if (event.target.closest(".agenda-menu-list > *")) agendaMenu.open = false;
  });
  document.addEventListener("click", (event) => {
    if (!agendaMenu.contains(event.target)) agendaMenu.open = false;
  });
  document.addEventListener("assistant:pick", (event) => {
    const { start, duration, roomId, doctorId, typeId } = event.detail;
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
