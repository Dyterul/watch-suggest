/* CineMatch Admin-Dashboard: Abo-Toggles, Profile, Avatare. */
(() => {
  "use strict";

  const state = {
    overview: null,
    household: [],
    presetUser: null,
    savingHousehold: false,
    toastTimer: null,
  };

  const $ = (selector) => document.querySelector(selector);
  const els = {
    statusRow: $("#status-row"),
    error: $("#admin-error"),
    household: $("#household-toggle"),
    householdNote: $("#household-note"),
    apiExample: $("#api-example"),
    usersGrid: $("#users-grid"),
    userCount: $("#user-count"),
    refreshBtn: $("#refresh-btn"),
    saveState: $("#save-state"),
    presetDialog: $("#preset-dialog"),
    presetGrid: $("#preset-grid"),
    presetSub: $("#preset-sub"),
    presetClose: $("#preset-close"),
    toast: $("#toast"),
    region: $("#region-label"),
  };

  init();

  function init() {
    bindEvents();
    loadOverview();
  }

  function bindEvents() {
    els.refreshBtn.addEventListener("click", () => loadOverview(true));
    els.presetClose.addEventListener("click", () => els.presetDialog.close());
    els.presetDialog.addEventListener("click", (event) => {
      if (event.target === els.presetDialog) els.presetDialog.close();
    });
    els.usersGrid.addEventListener("click", onUserAction);
    els.usersGrid.addEventListener("change", onAvatarFile);
    els.presetGrid.addEventListener("click", onPresetClick);
  }

  // ------------------------------------------------------------------ API

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: { Accept: "application/json", ...(options.headers || {}) },
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const data = await response.json();
        if (typeof data.detail === "string") detail = data.detail;
        else if (data.detail) detail = JSON.stringify(data.detail);
      } catch (_) {
        /* keine JSON-Antwort */
      }
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    if (response.status === 204) return null;
    return response.json();
  }

  async function loadOverview(showSpinner = false) {
    if (showSpinner) {
      els.refreshBtn.disabled = true;
      els.refreshBtn.textContent = "Lädt …";
    }
    hideError();
    try {
      const overview = await api("/admin/api/overview");
      state.overview = overview;
      state.household = Array.isArray(overview.household_providers)
        ? overview.household_providers.slice()
        : [];
      renderStatus();
      renderHousehold();
      renderUsers();
      renderApiExample();
      els.region.textContent = overview.status?.region || "DE";
    } catch (error) {
      if (error.status === 401) {
        showError("Login fehlgeschlagen. Bitte Seite neu laden und Zugangsdaten eingeben.");
      } else if (error.status === 503) {
        showError("Admin ist deaktiviert: In der .env fehlt ADMIN_PASSWORD.");
      } else {
        showError(`Dashboard konnte nicht geladen werden: ${error.message}`);
      }
    } finally {
      els.refreshBtn.disabled = false;
      els.refreshBtn.textContent = "Aktualisieren";
    }
  }

  // --------------------------------------------------------------- Status

  function renderStatus() {
    const overview = state.overview;
    if (!overview) return;
    const status = overview.status || {};
    const users = overview.users || [];
    const pills = [
      {
        label: status.tmdb_configured ? "TMDB verbunden" : "TMDB fehlt",
        tone: status.tmdb_configured ? "ok" : "warn",
        hint: status.tmdb_configured ? "Quiz & Suche aktiv" : "Kein API-Key gesetzt",
      },
      {
        label: status.home_assistant_configured ? "Home Assistant verbunden" : "Home Assistant fehlt",
        tone: status.home_assistant_configured ? "ok" : "warn",
        hint: status.home_assistant_configured ? status.ha_link_service : "HA_BASE_URL/HA_TOKEN fehlen",
      },
      {
        label: `${users.length} ${users.length === 1 ? "Profil" : "Profile"}`,
        tone: "neutral",
        hint: "registrierte Personen",
      },
      {
        label: `Region ${status.region || "DE"}`,
        tone: "neutral",
        hint: "Provider-Filter",
      },
    ];

    els.statusRow.replaceChildren();
    pills.forEach((pill) => {
      const item = document.createElement("div");
      item.className = `status-pill status-${pill.tone}`;
      const dot = document.createElement("span");
      dot.className = "status-dot";
      dot.setAttribute("aria-hidden", "true");
      const body = document.createElement("div");
      const label = document.createElement("strong");
      label.textContent = pill.label;
      const hint = document.createElement("span");
      hint.textContent = pill.hint;
      body.append(label, hint);
      item.append(dot, body);
      els.statusRow.append(item);
    });
  }

  // ------------------------------------------------------------- Household

  function renderHousehold() {
    const overview = state.overview;
    if (!overview) return;
    els.household.replaceChildren();
    (overview.providers || []).forEach((provider) => {
      const active = state.household.includes(provider.slug);
      const row = document.createElement("label");
      row.className = "switch-row";
      row.style.setProperty("--accent", provider.accent || "#6366f1");

      const identity = document.createElement("div");
      identity.className = "switch-identity";
      const dot = document.createElement("span");
      dot.className = "provider-dot";
      dot.setAttribute("aria-hidden", "true");
      const names = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = provider.name;
      const id = document.createElement("span");
      id.className = "muted small";
      id.textContent = `TMDB-Provider ${provider.tmdb_id} · Region ${overview.status?.region || "DE"}`;
      names.append(name, id);
      identity.append(dot, names);

      const control = document.createElement("span");
      control.className = "switch";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = provider.slug;
      input.checked = active;
      input.setAttribute("aria-label", `${provider.name} als aktives Abo markieren`);
      input.addEventListener("change", () => {
        row.classList.toggle("is-on", input.checked);
        saveHousehold();
      });
      const track = document.createElement("span");
      track.className = "switch-track";
      track.setAttribute("aria-hidden", "true");
      const knob = document.createElement("span");
      knob.className = "switch-knob";
      track.append(knob);
      control.append(input, track);

      row.classList.toggle("is-on", active);
      row.append(identity, control);
      els.household.append(row);
    });
    const activeNames = (overview.providers || [])
      .filter((provider) => state.household.includes(provider.slug))
      .map((provider) => provider.name);
    els.householdNote.textContent = activeNames.length
      ? `Aktiv: ${activeNames.join(", ")}. Änderungen gelten sofort für alle Empfehlungen.`
      : "Kein Abo aktiv – Empfehlungen sind blockiert, bis mindestens ein Dienst aktiviert ist.";
  }

  async function saveHousehold() {
    if (state.savingHousehold) return;
    state.savingHousehold = true;
    const inputs = Array.from(els.household.querySelectorAll("input[type=checkbox]"));
    inputs.forEach((input) => {
      input.disabled = true;
    });
    const selected = inputs.filter((input) => input.checked).map((input) => input.value);
    setSaveState("Wird gespeichert …");

    try {
      const data = await api("/admin/api/household", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ providers: selected }),
      });
      state.household = data.providers || selected;
      setSaveState("Gespeichert ✓");
      els.householdNote.textContent = data.provider_names?.length
        ? `Aktiv: ${data.provider_names.join(", ")}. Änderungen gelten sofort für alle Empfehlungen.`
        : "Kein Abo aktiv – Empfehlungen sind blockiert, bis mindestens ein Dienst aktiviert ist.";
      showToast("Haushalts-Abos aktualisiert.");
    } catch (error) {
      setSaveState("Fehler beim Speichern", true);
      showToast(`Speichern fehlgeschlagen: ${error.message}`, true);
      renderHousehold();
    } finally {
      state.savingHousehold = false;
      inputs.forEach((input) => {
        input.disabled = false;
      });
    }
  }

  function setSaveState(text, isError = false) {
    els.saveState.textContent = text;
    els.saveState.classList.toggle("is-error", isError);
    if (text === "Gespeichert ✓") {
      window.setTimeout(() => {
        if (els.saveState.textContent === "Gespeichert ✓") els.saveState.textContent = "";
      }, 2500);
    }
  }

  // ----------------------------------------------------------------- Users

  function renderUsers() {
    const users = state.overview?.users || [];
    els.usersGrid.replaceChildren();
    els.userCount.textContent = users.length
      ? `${users.length} ${users.length === 1 ? "Person ist" : "Personen sind"} registriert. Avatare werden unter /static/avatars/ gespeichert.`
      : "Noch niemand hat das Onboarding abgeschlossen.";

    if (!users.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      const icon = document.createElement("span");
      icon.className = "empty-icon";
      icon.textContent = "🍿";
      const title = document.createElement("strong");
      title.textContent = "Noch keine Profile";
      const text = document.createElement("p");
      text.textContent = "Sobald sich jemand über /onboard anmeldet, erscheint die Person hier.";
      const link = document.createElement("a");
      link.className = "btn btn-secondary";
      link.href = "/onboard";
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "Onboarding öffnen";
      empty.append(icon, title, text, link);
      els.usersGrid.append(empty);
      return;
    }

    users.forEach((user) => els.usersGrid.append(userCard(user)));
  }

  function userCard(user) {
    const card = document.createElement("article");
    card.className = "user-card";
    card.dataset.user = user.id;

    const head = document.createElement("div");
    head.className = "user-head";
    head.append(avatar(user));

    const identity = document.createElement("div");
    identity.className = "user-identity";
    const name = document.createElement("h3");
    name.textContent = user.name;
    const ip = document.createElement("p");
    ip.className = "user-ip";
    const ipLabel = document.createElement("span");
    ipLabel.textContent = "IP";
    const ipValue = document.createElement("code");
    ipValue.textContent = user.ip || "nicht erfasst";
    ip.append(ipLabel, ipValue);
    identity.append(name, ip);
    head.append(identity);

    const meta = document.createElement("div");
    meta.className = "user-meta";

    const services = document.createElement("div");
    services.className = "badges";
    (user.provider_names || []).forEach((providerName) => {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = providerName;
      services.append(badge);
    });
    if (!(user.provider_names || []).length) {
      const badge = document.createElement("span");
      badge.className = "badge badge-muted";
      badge.textContent = "Keine Dienste";
      services.append(badge);
    }
    meta.append(services);

    const counts = document.createElement("p");
    counts.className = "user-counts";
    const stats = user.counts || {};
    counts.textContent = `👍 ${stats.liked ?? 0}   ·   👎 ${stats.disliked ?? 0}   ·   ⏭️ ${stats.skipped ?? 0}   ·   ${user.swipes ?? 0} Swipes`;
    meta.append(counts);

    if ((user.top_genres || []).length) {
      const genres = document.createElement("p");
      genres.className = "user-genres";
      const label = document.createElement("span");
      label.className = "muted small";
      label.textContent = "Top-Genres: ";
      genres.append(label);
      genres.append(
        document.createTextNode(user.top_genres.map((genre) => genre.name).join(" · "))
      );
      meta.append(genres);
    }

    const ha = document.createElement("p");
    ha.className = "user-ha";
    if (user.ha_entity_id) {
      ha.textContent = `⌂ ${user.ha_entity_id}${user.custom_user_id ? ` · ${user.custom_user_id}` : ""}`;
    } else {
      ha.textContent = "⌂ Noch keinem Home-Assistant-Tracker zugeordnet";
      ha.classList.add("muted");
    }
    meta.append(ha);

    const actions = document.createElement("div");
    actions.className = "user-actions";

    const uploadLabel = document.createElement("label");
    uploadLabel.className = "btn btn-ghost btn-small";
    uploadLabel.textContent = "Foto hochladen";
    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = "image/png,image/jpeg,image/webp";
    fileInput.dataset.user = user.id;
    fileInput.className = "visually-hidden";
    uploadLabel.append(fileInput);

    const presetButton = actionButton("preset", user.id, "Vorlage", "btn-ghost");
    const resetButton = actionButton("reset", user.id, "Zurücksetzen", "btn-ghost");
    const deleteButton = actionButton("delete", user.id, "Löschen", "btn-danger-ghost");

    actions.append(uploadLabel, presetButton, resetButton, deleteButton);

    card.append(head, meta, actions);
    return card;
  }

  function avatar(user) {
    const wrapper = document.createElement("div");
    wrapper.className = "avatar avatar-admin";
    const initial = document.createElement("span");
    initial.textContent = (user.name || "?").trim().charAt(0).toUpperCase();
    wrapper.append(initial);
    if (user.avatar_url) {
      const image = document.createElement("img");
      image.src = user.avatar_url;
      image.alt = `Profilbild von ${user.name}`;
      image.addEventListener("error", () => image.remove());
      image.addEventListener("load", () => image.classList.add("is-loaded"));
      wrapper.append(image);
    }
    return wrapper;
  }

  function actionButton(action, userId, label, variant) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `btn ${variant} btn-small`;
    button.dataset.action = action;
    button.dataset.user = userId;
    button.textContent = label;
    return button;
  }

  async function onUserAction(event) {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const userId = button.dataset.user;
    const user = (state.overview?.users || []).find((item) => item.id === userId);
    if (!user) return;

    if (button.dataset.action === "preset") {
      openPresetDialog(user);
      return;
    }

    if (button.dataset.action === "reset") {
      const confirmed = window.confirm(
        `Profil von ${user.name} zurücksetzen? Swipes und Geschmack werden gelöscht, Avatar und HA-Zuordnung bleiben.`
      );
      if (!confirmed) return;
      button.disabled = true;
      try {
        await api(`/admin/api/users/${encodeURIComponent(user.id)}/reset`, {
          method: "POST",
        });
        showToast(`Profil von ${user.name} zurückgesetzt.`);
        await loadOverview();
      } catch (error) {
        showToast(`Zurücksetzen fehlgeschlagen: ${error.message}`, true);
        button.disabled = false;
      }
      return;
    }

    if (button.dataset.action === "delete") {
      const confirmed = window.confirm(
        `Profil von ${user.name} endgültig löschen? Das kann nicht rückgängig gemacht werden.`
      );
      if (!confirmed) return;
      button.disabled = true;
      try {
        await api(`/admin/api/users/${encodeURIComponent(user.id)}`, {
          method: "DELETE",
        });
        showToast(`Profil von ${user.name} gelöscht.`);
        await loadOverview();
      } catch (error) {
        showToast(`Löschen fehlgeschlagen: ${error.message}`, true);
        button.disabled = false;
      }
    }
  }

  async function onAvatarFile(event) {
    const input = event.target.closest('input[type="file"][data-user]');
    if (!input || !input.files?.length) return;
    const file = input.files[0];
    const maxBytes = 3 * 1024 * 1024;
    if (file.size > maxBytes) {
      showToast("Das Bild ist größer als 3 MB.", true);
      input.value = "";
      return;
    }
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      showToast("Bitte PNG, JPEG oder WebP wählen.", true);
      input.value = "";
      return;
    }

    const userId = input.dataset.user;
    const form = new FormData();
    form.append("file", file);
    const label = input.closest("label");
    label?.classList.add("is-busy");
    setSaveState("Avatar wird hochgeladen …");

    try {
      await api(`/admin/api/users/${encodeURIComponent(userId)}/avatar`, {
        method: "POST",
        body: form,
      });
      setSaveState("Gespeichert ✓");
      showToast("Profilbild aktualisiert.");
      await loadOverview();
    } catch (error) {
      setSaveState("Upload fehlgeschlagen", true);
      showToast(`Upload fehlgeschlagen: ${error.message}`, true);
      label?.classList.remove("is-busy");
      input.value = "";
    }
  }

  // -------------------------------------------------------------- Presets

  function openPresetDialog(user) {
    state.presetUser = user;
    els.presetSub.textContent = `Vorlage für ${user.name}`;
    els.presetGrid.replaceChildren();

    (state.overview?.presets || []).forEach((preset) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "preset-option";
      button.dataset.preset = preset.id;
      const image = document.createElement("img");
      image.src = preset.url;
      image.alt = `Avatar-Vorlage ${preset.label}`;
      image.loading = "lazy";
      const label = document.createElement("span");
      label.textContent = preset.label;
      button.append(image, label);
      els.presetGrid.append(button);
    });

    if (typeof els.presetDialog.showModal === "function") {
      els.presetDialog.showModal();
    } else {
      els.presetDialog.setAttribute("open", "");
    }
  }

  async function onPresetClick(event) {
    const button = event.target.closest("button[data-preset]");
    if (!button || !state.presetUser) return;
    button.disabled = true;
    try {
      await api(
        `/admin/api/users/${encodeURIComponent(state.presetUser.id)}/avatar/preset`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ preset: button.dataset.preset }),
        }
      );
      els.presetDialog.close();
      showToast("Avatar-Vorlage übernommen.");
      await loadOverview();
    } catch (error) {
      button.disabled = false;
      showToast(`Vorlage konnte nicht gesetzt werden: ${error.message}`, true);
    }
  }

  // ---------------------------------------------------------------- Misc

  function renderApiExample() {
    const origin = window.location.origin;
    els.apiExample.textContent =
      `curl -X POST ${origin}/api/v1/recommendations \\\n` +
      `  -H 'Content-Type: application/json' \\\n` +
      `  -d '{"media_type":"movie","limit":10}'\n\n` +
      `# gezielt für Personen:\n` +
      `curl -X POST ${origin}/api/v1/recommendations \\\n` +
      `  -H 'Content-Type: application/json' \\\n` +
      `  -d '{"users":["Lea","Jonas"],"media_type":"tv","limit":5}'`;
  }

  function showToast(message, isError = false) {
    window.clearTimeout(state.toastTimer);
    els.toast.textContent = message;
    els.toast.classList.toggle("is-error", isError);
    els.toast.hidden = false;
    state.toastTimer = window.setTimeout(() => {
      els.toast.hidden = true;
    }, 4200);
  }

  function showError(message) {
    els.error.textContent = message;
    els.error.hidden = false;
  }

  function hideError() {
    els.error.hidden = true;
    els.error.textContent = "";
  }
})();
