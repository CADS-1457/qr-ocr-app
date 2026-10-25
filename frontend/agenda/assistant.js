"use strict";

(() => {
  const api = `${window.AppAuth.API_BASE}/api/agenda`;
  const dialog = document.querySelector("#assistantDialog");
  const form = document.querySelector("#assistantForm");
  const errorBox = document.querySelector("#assistantError");
  const summary = document.querySelector("#assistantSummary");
  const results = document.querySelector("#assistantResults");
  const field = (id) => document.querySelector(id);
  let types = [];
  let loaded = false;

  async function request(path) {
    const response = await fetch(`${api}${path}`, { headers: window.AppAuth.headers() });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const reason = data?.detail || `Erreur HTTP ${response.status}`;
      throw new Error(typeof reason === "string" ? reason : reason.map((item) => item.msg).join(" ; "));
    }
    return data;
  }

  const pad = (value) => String(value).padStart(2, "0");
  const isoDate = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const addDays = (date, count) => new Date(date.getFullYear(), date.getMonth(), date.getDate() + count);

  function fill(select, entries, placeholder) {
    select.replaceChildren(new Option(placeholder, ""));
    for (const entry of entries) select.append(new Option(entry.display_name || entry.name, entry.id));
  }

  async function loadReference() {
    if (loaded) return;
    const [rooms, typeRows, resources] = await Promise.all([
      request("/rooms"), request("/appointment-types"), request("/resources"),
    ]);
    types = typeRows;
    fill(field("#asRoom"), rooms, "Toutes les salles");
    fill(field("#asType"), typeRows, "Sans type");
    fill(field("#asDoctor"), resources.filter((item) => item.kind === "DOCTOR"), "Tous les médecins");
    loaded = true;
  }

  function applyPreset(name) {
    const today = new Date();
    if (name === "morning") {
      field("#asTimeFrom").value = "08:00";
      field("#asTimeTo").value = "12:30";
    } else if (name === "afternoon") {
      field("#asTimeFrom").value = "13:00";
      field("#asTimeTo").value = "18:00";
    } else if (name === "week") {
      field("#asFrom").value = isoDate(today);
      field("#asTo").value = isoDate(addDays(today, 7));
    } else if (name === "month") {
      field("#asFrom").value = isoDate(today);
      field("#asTo").value = isoDate(addDays(today, 30));
    }
  }

  function renderResults(data) {
    results.replaceChildren();
    summary.textContent = data.suggestions.length
      ? `${data.total} créneau(x) trouvé(s) — ${data.suggestions.length} meilleur(s) proposition(s) :`
      : "Aucun créneau libre ne correspond à ces critères. Élargissez la période, les horaires ou les jours.";
    data.suggestions.forEach((item, index) => {
      const start = new Date(item.starts_at);
      const end = new Date(item.ends_at);
      const row = document.createElement("div");
      row.className = "assistant-result";
      row.style.setProperty("--zone-color", item.color || "#0f4c81");
      const day = start.toLocaleDateString("fr-BE", { weekday: "long", day: "numeric", month: "long" });
      const hours = `${start.toLocaleTimeString("fr-BE", { hour: "2-digit", minute: "2-digit" })}–${end.toLocaleTimeString("fr-BE", { hour: "2-digit", minute: "2-digit" })}`;
      const text = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = `${index === 0 ? "★ " : ""}${day} · ${hours}`;
      const detail = document.createElement("span");
      detail.textContent = [item.room, item.doctor, item.label].filter(Boolean).join(" · ")
        + (item.alternatives ? ` (+${item.alternatives} autre(s) option(s) au même horaire)` : "");
      text.append(title, detail);
      const choose = document.createElement("button");
      choose.type = "button";
      choose.textContent = "Choisir";
      choose.addEventListener("click", () => {
        dialog.close();
        document.dispatchEvent(new CustomEvent("assistant:pick", {
          detail: {
            start: item.starts_at, duration: Math.round((end - start) / 60000), roomId: item.room_id,
            doctorId: item.doctor_id || "", typeId: field("#asType").value,
          },
        }));
      });
      row.append(text, choose);
      results.append(row);
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.textContent = "";
    const params = new URLSearchParams({
      start: field("#asFrom").value,
      end: field("#asTo").value,
      duration: Number(field("#asDuration").value) || 0,
      limit: Math.min(50, Math.max(1, Number(field("#asCount").value) || 12)),
    });
    const optional = { room_id: "#asRoom", doctor_id: "#asDoctor", time_from: "#asTimeFrom", time_to: "#asTimeTo", preferred_time: "#asPreferred" };
    for (const [key, selector] of Object.entries(optional)) {
      if (field(selector).value) params.set(key, field(selector).value);
    }
    const days = [...form.querySelectorAll('input[name="asDay"]:checked')].map((box) => box.value);
    if (!days.length) {
      errorBox.textContent = "Sélectionnez au moins un jour.";
      return;
    }
    params.set("weekdays", days.join(","));
    try {
      renderResults(await request(`/suggestions?${params}`));
    } catch (error) {
      results.replaceChildren();
      summary.textContent = "";
      errorBox.textContent = error.message;
    }
  });

  form.querySelectorAll("[data-preset]").forEach((button) =>
    button.addEventListener("click", () => applyPreset(button.dataset.preset)),
  );

  field("#asType").addEventListener("change", () => {
    const type = types.find((entry) => entry.id === field("#asType").value);
    if (type?.duration_minutes) field("#asDuration").value = type.duration_minutes;
    else field("#asDuration").value = 0;
  });

  field("#openAssistant").addEventListener("click", async () => {
    try {
      await loadReference();
    } catch (error) {
      errorBox.textContent = error.message;
    }
    if (!field("#asFrom").value) applyPreset("month");
    const filterRoom = field("#roomFilter").value;
    const filterDoctor = field("#doctorFilter").value;
    if (filterRoom) field("#asRoom").value = filterRoom;
    if (filterDoctor) field("#asDoctor").value = filterDoctor;
    dialog.showModal();
  });
})();
