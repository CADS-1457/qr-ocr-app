"use strict";

(() => {
  const api = `${window.AppAuth.API_BASE}/api/agenda`;
  const board = document.querySelector("#board");
  const message = document.querySelector("#message");
  const dayInput = document.querySelector("#day");
  const rooms = [];
  const appointmentTypes = [];
  const resources = [];
  const patients = [];
  const stockLocations = [];
  const stockItems = [];
  const stockUnits = [];

  const localDate = (value = new Date()) => {
    const offset = value.getTimezoneOffset() * 60_000;
    return new Date(value.getTime() - offset).toISOString().slice(0, 10);
  };

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

  function populate(select, entries, placeholder, selected = "") {
    select.replaceChildren();
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = placeholder;
    select.append(empty);
    for (const entry of entries) {
      const option = document.createElement("option");
      option.value = entry.id;
      option.textContent = entry.display_name || entry.path || entry.name;
      if (String(entry.id) === String(selected)) option.selected = true;
      select.append(option);
    }
  }

  const STATE_LABELS = {
    PLANNED: "Planifié",
    PRESENT: "Présent",
    WAITING: "En attente",
    IN_PROGRESS: "En cours",
    RECOVERY: "Réveil",
    BILLING: "Facturation",
    COMPLETED: "Terminé",
    DISCHARGED: "Sorti",
    CANCELLED: "Annulé",
    NO_SHOW: "Absent",
  };

  function textElement(tag, value, className) {
    const element = document.createElement(tag);
    element.textContent = value || "";
    if (className) element.className = className;
    return element;
  }

  function renderAppointments(appointments) {
    board.replaceChildren();
    if (!appointments.length) {
      board.append(textElement("p", "Aucun rendez-vous pour cette journée."));
      return;
    }
    for (const appointment of appointments) {
      const card = document.createElement("article");
      card.className = `appointment-card ${appointment.state}`;
      card.append(
        textElement("div", STATE_LABELS[appointment.state] || appointment.state, "appointment-status"),
        textElement("h3", appointment.patient_name),
        textElement(
          "p",
          `${new Date(appointment.starts_at).toLocaleTimeString("fr-BE", {
            hour: "2-digit",
            minute: "2-digit",
          })} – ${new Date(appointment.ends_at).toLocaleTimeString("fr-BE", {
            hour: "2-digit",
            minute: "2-digit",
          })} · ${appointment.appointment_type || "Rendez-vous"}`,
        ),
        textElement(
          "p",
          `${appointment.current_location || appointment.room}${
            appointment.doctor ? ` · ${appointment.doctor}` : ""
          }`,
          "appointment-meta",
        ),
      );
      if (appointment.recovery_comment) {
        card.append(textElement("p", appointment.recovery_comment));
      }
      const transferButton = textElement("button", "Transférer");
      transferButton.type = "button";
      transferButton.addEventListener("click", () => openTransfer(appointment));
      const isToday = new Date(appointment.starts_at).toDateString() === new Date().toDateString();
      if (isToday) card.append(transferButton);
      board.append(card);
    }
  }

  let loadedAppointments = [];

  async function loadAppointments() {
    if (!dayInput.value) return;
    const data = await request(`/day-view?day=${encodeURIComponent(dayInput.value)}`);
    loadedAppointments = data;
    renderAppointments(data);
  }

  async function loadReferenceData() {
    const [
      roomRows,
      typeRows,
      resourceRows,
      patientRows,
      stockLocationRows,
      stockItemRows,
      stockUnitRows,
    ] = await Promise.all([
      request("/rooms"),
      request("/appointment-types"),
      request("/resources"),
      request("/patients"),
      request("/stock-locations"),
      request("/stock-items"),
      request("/stock-units"),
    ]);
    rooms.splice(0, rooms.length, ...roomRows);
    appointmentTypes.splice(0, appointmentTypes.length, ...typeRows);
    resources.splice(0, resources.length, ...resourceRows);
    patients.splice(0, patients.length, ...patientRows);
    stockLocations.splice(0, stockLocations.length, ...stockLocationRows);
    stockItems.splice(0, stockItems.length, ...stockItemRows);
    stockUnits.splice(0, stockUnits.length, ...stockUnitRows);
    populate(document.querySelector("#location"), rooms, "Choisir une salle");
    populate(document.querySelector("#appointmentRoom"), rooms, "Choisir une salle");
    populate(document.querySelector("#appointmentType"), appointmentTypes, "Sans type");
    populate(document.querySelector("#followUpType"), appointmentTypes, "Choisir un type");
    populate(
      document.querySelector("#doctor"),
      resources.filter((resource) => resource.kind === "DOCTOR"),
      "Aucun médecin",
    );
    populate(
      document.querySelector("#operator"),
      resources.filter((resource) => resource.kind === "OPERATOR"),
      "Aucun opérateur",
    );
    populate(
      document.querySelector("#patient"),
      patients.map((patient) => ({ id: patient.id, display_name: patient.display_name })),
      "Choisir un patient",
    );
    populate(
      document.querySelector("#stockLocationParent"),
      stockLocations,
      "Emplacement principal (racine)",
    );
    populate(document.querySelector("#stockLocationRoom"), rooms, "");
    document.querySelector("#stockLocationRoom").firstElementChild.remove();
    populate(document.querySelector("#editLocation"), stockLocations, "Choisir un emplacement");
    populate(document.querySelector("#editLocationRooms"), rooms, "");
    document.querySelector("#editLocationRooms").firstElementChild.remove();
    document.querySelector("#newAppointment").disabled = rooms.length === 0;
    refreshEditors();
  }

  const editors = [
    { select: "#editRoom", list: () => rooms, empty: "Choisir une salle",
      fill: (row) => {
        document.querySelector("#editRoomName").value = row?.name || "";
        document.querySelector("#editRoomKind").value = row?.kind || "OTHER";
        document.querySelector("#editRoomColor").value = (row?.color || "#64748b").toLowerCase();
        document.querySelector("#editRoomCapacity").value = row?.capacity || 1;
      },
      body: () => ({
        name: document.querySelector("#editRoomName").value.trim(),
        kind: document.querySelector("#editRoomKind").value,
        color: document.querySelector("#editRoomColor").value,
        capacity: Number(document.querySelector("#editRoomCapacity").value),
      }),
      path: "/rooms", save: "#saveRoom" },
    { select: "#editType", list: () => appointmentTypes, empty: "Choisir un type",
      fill: (row) => {
        document.querySelector("#editTypeName").value = row?.name || "";
        document.querySelector("#editTypeDuration").value = row?.duration_minutes ?? 30;
        document.querySelector("#editTypeColor").value = (row?.color || "#2563eb").toLowerCase();
      },
      body: () => ({
        name: document.querySelector("#editTypeName").value.trim(),
        duration_minutes: Number(document.querySelector("#editTypeDuration").value),
        color: document.querySelector("#editTypeColor").value,
      }),
      path: "/appointment-types", save: "#saveType" },
    { select: "#editResource", list: () => resources, empty: "Choisir une ressource",
      fill: (row) => {
        document.querySelector("#editResourceKind").value = row?.kind || "DOCTOR";
        document.querySelector("#editResourceName").value = row?.display_name || "";
      },
      body: () => ({
        kind: document.querySelector("#editResourceKind").value,
        display_name: document.querySelector("#editResourceName").value.trim(),
      }),
      path: "/resources", save: "#saveResource" },
  ];

  function refreshEditors() {
    for (const editor of editors) {
      const select = document.querySelector(editor.select);
      populate(select, editor.list(), editor.empty, select.value);
      editor.fill(editor.list().find((row) => String(row.id) === select.value));
    }
  }

  for (const editor of editors) {
    const select = document.querySelector(editor.select);
    select.addEventListener("change", () =>
      editor.fill(editor.list().find((row) => String(row.id) === select.value)),
    );
    document.querySelector(editor.save).addEventListener("click", async () => {
      if (!select.value) {
        showMessage("Faites d’abord un choix dans la liste à modifier.", true);
        return;
      }
      try {
        await request(`${editor.path}/${select.value}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(editor.body()),
        });
        await loadReferenceData();
        await loadAppointments();
        showMessage("Modification enregistrée.");
      } catch (error) {
        showMessage(error.message, true);
      }
    });
  }

  function renderStockConsumptions() {
    const container = document.querySelector("#consumptions");
    container.replaceChildren();
    if (!stockUnits.length) {
      container.append(textElement("p", "Aucune unité de stock disponible."));
      return;
    }
    for (const unit of stockUnits) {
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

  function openTransfer(appointment) {
    document.querySelector("#appointmentId").value = appointment.id;
    document.querySelector("#state").value = appointment.state;
    document.querySelector("#handover").value = "";
    document.querySelector("#recovery").value = "";
    document.querySelector("#discharge").value = "";
    document.querySelector("#followup").checked = false;
    document.querySelector("#followUpTypeField").hidden = true;
    document.querySelector("#followUpDateField").hidden = true;
    renderStockConsumptions();
    document.querySelector("#transferDialog").showModal();
  }

  function dateTimeLocal(value) {
    const offset = value.getTimezoneOffset() * 60_000;
    return new Date(value.getTime() - offset).toISOString().slice(0, 16);
  }

  function setAppointmentTimes(durationMinutes = 30) {
    const start = new Date();
    start.setSeconds(0, 0);
    start.setMinutes(Math.ceil(start.getMinutes() / 15) * 15);
    const end = new Date(start.getTime() + durationMinutes * 60_000);
    document.querySelector("#startsAt").value = dateTimeLocal(start);
    document.querySelector("#endsAt").value = dateTimeLocal(end);
  }

  async function createConfiguration(path, payload) {
    await request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await loadReferenceData();
    showMessage("Configuration enregistrée.");
  }

  document.querySelector("#reload").addEventListener("click", async () => {
    try {
      await loadAppointments();
      showMessage("Agenda actualisé.");
    } catch (error) {
      showMessage(error.message, true);
    }
  });
  dayInput.addEventListener("change", () => {
    loadAppointments().catch((error) => showMessage(error.message, true));
  });

  document.querySelector("#newAppointment").addEventListener("click", () => {
    setAppointmentTimes();
    document.querySelector("#appointmentDialog").showModal();
  });

  document.querySelector("#patient").addEventListener("change", async (event) => {
    const patientId = event.target.value;
    if (!patientId) return;
    try {
      const contact = await request(`/patients/${encodeURIComponent(patientId)}/contact`);
      if (contact.mobile) document.querySelector("#phone").value = contact.mobile;
    } catch (error) {
      showMessage(error.message, true);
    }
  });

  document.querySelector("#appointmentType").addEventListener("change", (event) => {
    const appointmentType = appointmentTypes.find(
      (entry) => String(entry.id) === event.target.value,
    );
    if (!appointmentType) return;
    const start = new Date(document.querySelector("#startsAt").value);
    if (Number.isNaN(start.getTime())) return;
    if (!appointmentType.duration_minutes) return;
    document.querySelector("#endsAt").value = dateTimeLocal(
      new Date(start.getTime() + appointmentType.duration_minutes * 60_000),
    );
  });

  document.querySelector("#appointmentForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const value = (selector) => document.querySelector(selector).value;
    const payload = {
      patient_id: Number(value("#patient")),
      appointment_type_id: value("#appointmentType") || null,
      room_id: value("#appointmentRoom"),
      doctor_id: value("#doctor") || null,
      operator_id: value("#operator") || null,
      starts_at: new Date(value("#startsAt")).toISOString(),
      ends_at: new Date(value("#endsAt")).toISOString(),
      phone: value("#phone").trim() || null,
      notes: value("#notes").trim() || null,
    };
    try {
      await request("/appointments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      document.querySelector("#appointmentDialog").close();
      await loadAppointments();
      showMessage("Rendez-vous créé.");
    } catch (error) {
      showMessage(error.message, true);
    }
  });

  document.querySelector("#followup").addEventListener("change", (event) => {
    document.querySelector("#followUpTypeField").hidden = !event.target.checked;
    document.querySelector("#followUpDateField").hidden = !event.target.checked;
    document.querySelector("#followUpType").required = event.target.checked;
  });

  document.querySelector("#location").addEventListener("change", (event) => {
    const room = rooms.find((entry) => entry.id === event.target.value);
    if (room?.default_state) document.querySelector("#state").value = room.default_state;
  });

  document.querySelector("#transferForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const isFollowUpRequired = document.querySelector("#followup").checked;
    const payload = {
      to_location_id: document.querySelector("#location").value,
      new_state: document.querySelector("#state").value,
      handover_comment: document.querySelector("#handover").value.trim() || null,
      recovery_comment: document.querySelector("#recovery").value.trim() || null,
      discharge_instructions: document.querySelector("#discharge").value.trim() || null,
      follow_up: {
        required: isFollowUpRequired,
        appointment_type_id: isFollowUpRequired
          ? document.querySelector("#followUpType").value
          : null,
        target_date: isFollowUpRequired
          ? document.querySelector("#followUpDate").value || null
          : null,
      },
      consumptions: [...document.querySelectorAll(".stock-consumption-row")]
        .filter((row) => row.querySelector(".stock-consumption-use").checked)
        .map((row) => ({
          stock_unit_id: row.querySelector(".stock-consumption-use").value,
          quantity: row.querySelector(".stock-consumption-quantity").value,
        })),
    };
    try {
      await request(
        `/appointments/${encodeURIComponent(document.querySelector("#appointmentId").value)}/transfer`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      document.querySelector("#transferDialog").close();
      await loadAppointments();
      showMessage("Transfert enregistré.");
    } catch (error) {
      showMessage(error.message, true);
    }
  });

  document.querySelector("#configuration").addEventListener("click", () => {
    document.querySelector("#configurationDialog").showModal();
  });

  document.querySelector("#addRoom").addEventListener("click", async () => {
    const name = document.querySelector("#roomName");
    if (!name.reportValidity()) return;
    try {
      await createConfiguration("/rooms", {
        name: name.value.trim(),
        kind: document.querySelector("#roomKind").value,
      });
      name.value = "";
    } catch (error) {
      showMessage(error.message, true);
    }
  });

  document.querySelector("#addType").addEventListener("click", async () => {
    const name = document.querySelector("#typeName");
    if (!name.reportValidity()) return;
    try {
      await createConfiguration("/appointment-types", {
        name: name.value.trim(),
        duration_minutes: Number(document.querySelector("#typeDuration").value),
      });
      name.value = "";
    } catch (error) {
      showMessage(error.message, true);
    }
  });

  document.querySelector("#addResource").addEventListener("click", async () => {
    const name = document.querySelector("#resourceName");
    if (!name.reportValidity()) return;
    try {
      await createConfiguration("/resources", {
        kind: document.querySelector("#resourceKind").value,
        display_name: name.value.trim(),
      });
      name.value = "";
    } catch (error) {
      showMessage(error.message, true);
    }
  });

  const selectedValues = (select) =>
    Array.from(select.selectedOptions).map((option) => option.value);

  document.querySelector("#editLocation").addEventListener("change", (event) => {
    const location = stockLocations.find((entry) => entry.id === event.target.value);
    const linked = new Set(location?.room_ids || []);
    for (const option of document.querySelector("#editLocationRooms").options) {
      option.selected = linked.has(option.value);
    }
  });

  document.querySelector("#saveLocationRooms").addEventListener("click", async () => {
    const locationId = document.querySelector("#editLocation").value;
    if (!locationId) {
      showMessage("Choisissez un emplacement.", true);
      return;
    }
    try {
      await request(`/stock-locations/${locationId}/rooms`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          room_ids: selectedValues(document.querySelector("#editLocationRooms")),
        }),
      });
      showMessage("Salles de l’emplacement enregistrées.");
      await loadReferenceData();
      document.querySelector("#editLocation").value = locationId;
      document.querySelector("#editLocation").dispatchEvent(new Event("change"));
    } catch (error) {
      showMessage(error.message, true);
    }
  });

  document.querySelector("#addStockLocation").addEventListener("click", async () => {
    const name = document.querySelector("#stockLocationName");
    if (!name.reportValidity()) return;
    try {
      await createConfiguration("/stock-locations", {
        name: name.value.trim(),
        parent_id: document.querySelector("#stockLocationParent").value || null,
        room_ids: selectedValues(document.querySelector("#stockLocationRoom")),
      });
      name.value = "";
    } catch (error) {
      showMessage(error.message, true);
    }
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
    const signedIn = Boolean(user);
    document.querySelector("#loginForm").hidden = signedIn;
    document.querySelector("#authBar").hidden = !signedIn;
    document.querySelector("#signedInAs").textContent = signedIn
      ? `Connecté : ${user.username} (${roleLabel(user.role)})`
      : "";
    document.querySelector("#configuration").hidden = !["admin", "superadmin"].includes(user?.role);
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
    board.replaceChildren();
    initialize();
  });

  async function initialize() {
    const requestedDay = new URLSearchParams(window.location.search).get("day");
    dayInput.value = /^\d{4}-\d{2}-\d{2}$/.test(requestedDay || "") ? requestedDay : localDate();
    renderAuthState();
    try {
      const authResponse = await fetch(
        `${window.AppAuth.API_BASE}/api/auth/me`,
        { headers: window.AppAuth.headers() },
      );
      if (!authResponse.ok) {
        throw new Error("Connexion requise : utilisez le menu « Connexion » en haut à droite.");
      }
      await loadReferenceData();
      await loadAppointments();
      showMessage("Agenda chargé.");
      const user = window.AppAuth.user();
      const params = new URLSearchParams(window.location.search);
      const transferId = params.get("transfer");
      if (transferId) {
        params.delete("transfer");
        history.replaceState(null, "", `${window.location.pathname}${params.size ? `?${params}` : ""}`);
        const target = (loadedAppointments || []).find((item) => item.id === transferId);
        if (target) openTransfer(target);
      }
      if (params.get("config") === "1" && ["admin", "superadmin"].includes(user?.role)) {
        params.delete("config");
        params.delete("embed");
        history.replaceState(null, "", `${window.location.pathname}${params.size ? `?${params}` : ""}`);
        document.querySelector("#configurationDialog").showModal();
      }
    } catch (error) {
      showMessage(error.message, true);
    }
  }

  const embedded = new URLSearchParams(window.location.search).get("embed") === "1";
  if (embedded) {
    document.documentElement.classList.add("embed");
    document.querySelector("#configurationDialog").addEventListener("close", () => {
      window.parent.postMessage({ type: "agenda-config-closed" }, window.location.origin);
    });
  }

  initialize();
})();
