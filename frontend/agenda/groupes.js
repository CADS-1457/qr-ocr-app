"use strict";

// Administration des groupes d'agendas (ensembles de salles affichés ensemble).
(() => {
  const api = `${window.AppAuth.API_BASE}/api/agenda`;
  const dialog = document.querySelector("#groupsDialog");
  const form = document.querySelector("#groupForm");
  const nameInput = document.querySelector("#groupName");
  const roomsBox = document.querySelector("#groupRooms");
  const errorBox = document.querySelector("#groupError");
  const list = document.querySelector("#groupList");
  let editingId = null;
  let rooms = [];
  let groups = [];

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

  const boxes = () => [...roomsBox.querySelectorAll("input")];

  function resetForm() {
    editingId = null;
    nameInput.value = "";
    for (const box of boxes()) box.checked = false;
    document.querySelector("#groupFormTitle").textContent = "Nouveau groupe";
    document.querySelector("#groupSubmit").textContent = "Créer le groupe";
    document.querySelector("#groupCancelEdit").hidden = true;
    errorBox.textContent = "";
  }

  function renderRooms() {
    roomsBox.replaceChildren();
    for (const room of rooms) {
      const label = document.createElement("label");
      label.className = "checkbox";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.value = room.id;
      label.append(box, document.createTextNode(` ${room.name}`));
      roomsBox.append(label);
    }
  }

  function renderList() {
    list.replaceChildren();
    if (!groups.length) {
      const empty = document.createElement("p");
      empty.className = "hint";
      empty.textContent = "Aucun groupe créé.";
      list.append(empty);
      return;
    }
    const names = new Map(rooms.map((room) => [room.id, room.name]));
    for (const group of groups) {
      const row = document.createElement("div");
      row.className = "block-row";
      const info = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = group.name;
      const detail = document.createElement("span");
      detail.textContent = group.room_ids.map((id) => names.get(id) || "?").join(" · ");
      info.append(title, detail);
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "secondary";
      edit.textContent = "Modifier";
      edit.addEventListener("click", () => {
        resetForm();
        editingId = group.id;
        nameInput.value = group.name;
        for (const box of boxes()) box.checked = group.room_ids.includes(box.value);
        document.querySelector("#groupFormTitle").textContent = `Modifier le groupe (${group.name})`;
        document.querySelector("#groupSubmit").textContent = "Enregistrer";
        document.querySelector("#groupCancelEdit").hidden = false;
        dialog.scrollTop = 0;
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "secondary danger";
      remove.textContent = "Supprimer";
      remove.addEventListener("click", async () => {
        const confirmed = await (window.confirmAction?.(
          "Supprimer le groupe ?",
          `Le groupe « ${group.name} » sera supprimé. Les agendas et rendez-vous ne sont pas touchés.`,
          "Supprimer",
          true,
        ) ?? Promise.resolve(false));
        if (!confirmed) return;
        try {
          await request(`/agenda-groups/${group.id}`, { method: "DELETE" });
          if (editingId === group.id) resetForm();
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
    groups = await request("/agenda-groups");
    renderList();
    document.dispatchEvent(new CustomEvent("groups:changed", { detail: groups }));
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.textContent = "";
    errorBox.classList.remove("ok");
    const room_ids = boxes().filter((box) => box.checked).map((box) => box.value);
    try {
      if (!room_ids.length) throw new Error("Choisissez au moins un agenda.");
      await request(editingId ? `/agenda-groups/${editingId}` : "/agenda-groups", {
        method: editingId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: nameInput.value.trim(), room_ids }),
      });
      const wasEditing = Boolean(editingId);
      resetForm();
      await refresh();
      errorBox.classList.add("ok");
      errorBox.textContent = wasEditing ? "Groupe modifié." : "Groupe créé.";
    } catch (error) {
      errorBox.textContent = error.message;
    }
  });

  document.querySelector("#groupCancelEdit").addEventListener("click", resetForm);
  document.querySelector("#manageGroups").addEventListener("click", async () => {
    try {
      rooms = await request("/rooms");
      renderRooms();
      resetForm();
      groups = await request("/agenda-groups");
      renderList();
      dialog.showModal();
    } catch (error) {
      document.querySelector("#message").textContent = error.message;
      document.querySelector("#message").classList.add("error");
    }
  });
})();
