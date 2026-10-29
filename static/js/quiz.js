/* CineMatch Onboarding – Tinder-artiger Kartenstapel, Touch + Maus + Tastatur. */
(() => {
  "use strict";

  const PROVIDER_FALLBACK = [
    { slug: "netflix", name: "Netflix", tmdb_id: 8, accent: "#e50914" },
    { slug: "prime", name: "Amazon Prime Video", tmdb_id: 119, accent: "#00a8e1" },
    { slug: "disney", name: "Disney+", tmdb_id: 337, accent: "#113ccf" },
    { slug: "appletv", name: "Apple TV+", tmdb_id: 350, accent: "#c7c7cc" },
    { slug: "paramount", name: "Paramount+", tmdb_id: 531, accent: "#0064ff" },
    { slug: "crunchyroll", name: "Crunchyroll", tmdb_id: 283, accent: "#f47521" },
  ];

  const MIN_SWIPES = 3;
  const PREFETCH_THRESHOLD = 2;
  const SWIPE_DISTANCE = () => Math.max(72, window.innerWidth * 0.2);
  const SWIPE_UP_DISTANCE = () => Math.max(96, window.innerHeight * 0.12);

  const state = {
    view: "basics",
    sessionId: null,
    name: "",
    providers: [],
    cards: [],
    swipes: 0,
    maxAnswers: 20,
    busy: false,
    loading: false,
    saving: false,
    serverDone: false,
    drag: null,
  };

  const cardNodes = new Map();
  const $ = (selector) => document.querySelector(selector);

  const els = {
    basics: $("#view-basics"),
    quiz: $("#view-quiz"),
    done: $("#view-done"),
    form: $("#basics-form"),
    name: $("#name-input"),
    chips: $("#provider-chips"),
    basicsError: $("#basics-error"),
    startBtn: $("#start-btn"),
    deck: $("#deck"),
    deckEmpty: $("#deck-empty"),
    deckLoading: $("#deck-loading"),
    loadMoreBtn: $("#load-more-btn"),
    progressBar: $("#progress-bar"),
    progressTrack: $("#progress-track"),
    progressLabel: $("#progress-label"),
    queueLabel: $("#queue-label"),
    quizError: $("#quiz-error"),
    finishBtn: $("#finish-btn"),
    skipToSave: $("#skip-to-save-btn"),
    restartBtn: $("#restart-btn"),
    actions: Array.from(document.querySelectorAll(".action")),
    doneAvatar: $("#done-avatar"),
    doneTitle: $("#done-title"),
    doneSub: $("#done-sub"),
    doneGenres: $("#done-genres"),
    doneStats: $("#done-stats"),
    donePresence: $("#done-presence"),
    doneIp: $("#done-ip"),
    redoBtn: $("#redo-btn"),
  };

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const keyOf = (card) => `${card.media_type}:${card.tmdb_id}`;
  const clamp01 = (value) => Math.max(0, Math.min(1, value));

  init();

  async function init() {
    bindEvents();
    const providers = await loadProviders();
    renderProviders(providers);
  }

  function bindEvents() {
    els.form.addEventListener("submit", onStart);
    els.actions.forEach((button) => {
      button.addEventListener("click", () => act(button.dataset.action));
    });
    els.loadMoreBtn.addEventListener("click", () => loadMore(true));
    els.finishBtn.addEventListener("click", finish);
    els.skipToSave.addEventListener("click", finish);
    els.restartBtn.addEventListener("click", restartQuiz);
    els.redoBtn.addEventListener("click", restartQuiz);
    els.deck.addEventListener("pointerdown", onPointerDown);
    els.deck.addEventListener("pointermove", onPointerMove);
    els.deck.addEventListener("pointerup", onPointerUp);
    els.deck.addEventListener("pointercancel", cancelDrag);
    els.deck.addEventListener("lostpointercapture", cancelDrag);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", () => {
      if (!state.drag) renderDeck();
    });
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
        /* Antwort ohne JSON */
      }
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    if (response.status === 204) return null;
    return response.json();
  }

  async function apiPost(path, body, retries = 1) {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await api(path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } catch (error) {
        const retryable = !error.status || error.status >= 500;
        if (retryable && attempt < retries) {
          await sleep(450);
          continue;
        }
        throw error;
      }
    }
  }

  async function loadProviders() {
    try {
      const data = await api("/api/v1/providers");
      if (Array.isArray(data.providers) && data.providers.length) return data.providers;
    } catch (_) {
      /* Fallback unten */
    }
    return PROVIDER_FALLBACK;
  }

  // --------------------------------------------------------------- Basics

  function renderProviders(providers) {
    els.chips.replaceChildren();
    providers.forEach((provider) => {
      const chip = document.createElement("label");
      chip.className = "chip";
      chip.style.setProperty("--chip", provider.accent || "#6366f1");

      const input = document.createElement("input");
      input.type = "checkbox";
      input.name = "providers";
      input.value = provider.slug;

      const dot = document.createElement("span");
      dot.className = "chip-dot";
      dot.setAttribute("aria-hidden", "true");

      const label = document.createElement("span");
      label.className = "chip-name";
      label.textContent = provider.name;

      const check = document.createElement("span");
      check.className = "chip-check";
      check.setAttribute("aria-hidden", "true");
      check.textContent = "✓";

      input.addEventListener("change", () => {
        chip.classList.toggle("selected", input.checked);
        hideError(els.basicsError);
      });

      chip.append(input, dot, label, check);
      els.chips.append(chip);
    });
  }

  async function onStart(event) {
    event.preventDefault();
    hideError(els.basicsError);

    const name = els.name.value.trim().replace(/\s+/g, " ");
    const providers = Array.from(
      document.querySelectorAll('input[name="providers"]:checked')
    ).map((input) => input.value);

    if (!name) {
      showError(els.basicsError, "Bitte gib deinen Namen ein.");
      els.name.focus();
      return;
    }
    if (!providers.length) {
      showError(els.basicsError, "Wähle mindestens einen Streaming-Dienst aus.");
      return;
    }

    setButtonLoading(els.startBtn, true, "Quiz wird vorbereitet …");
    try {
      const data = await apiPost("/api/v1/quiz/start", { name, providers });
      state.sessionId = data.session_id;
      state.name = name;
      state.providers = providers;
      state.maxAnswers = Number(data.max_answers) || 20;
      state.swipes = 0;
      state.serverDone = false;
      state.cards = Array.isArray(data.cards) ? data.cards.slice() : [];
      cardNodes.clear();
      els.deck.replaceChildren();
      showView("quiz");
      renderDeck();
      updateProgress();
      if (!state.cards.length) {
        showEmptyDeck(true);
      }
    } catch (error) {
      showError(
        els.basicsError,
        error.status === 502
          ? `TMDB ist gerade nicht erreichbar: ${error.message}`
          : error.message
      );
    } finally {
      setButtonLoading(els.startBtn, false, "Geschmack kalibrieren");
    }
  }

  function setButtonLoading(button, loading, label) {
    button.disabled = loading;
    button.classList.toggle("is-loading", loading);
    const target = button.querySelector(".btn-label");
    if (target) target.textContent = label;
    else button.textContent = label;
  }

  // ---------------------------------------------------------------- Cards

  function createCardNode(card) {
    const node = document.createElement("article");
    node.className = "card";
    node.dataset.key = keyOf(card);
    node.setAttribute(
      "aria-label",
      `${card.title}${card.year ? `, ${card.year}` : ""}${card.genres?.length ? `, ${card.genres.join(", ")}` : ""}`
    );

    const media = document.createElement("div");
    media.className = "card-media";

    const placeholder = document.createElement("div");
    placeholder.className = "card-placeholder";
    const placeholderTitle = document.createElement("span");
    placeholderTitle.textContent = card.title;
    placeholder.append(placeholderTitle);

    media.append(placeholder);
    if (card.poster_url) {
      const image = document.createElement("img");
      image.src = card.poster_url;
      image.alt = "";
      image.draggable = false;
      image.decoding = "async";
      image.loading = "eager";
      image.addEventListener("error", () => {
        image.remove();
        media.classList.add("no-image");
      });
      media.append(image);
    } else {
      media.classList.add("no-image");
    }

    const scrim = document.createElement("div");
    scrim.className = "card-scrim";

    const content = document.createElement("div");
    content.className = "card-content";

    const meta = document.createElement("p");
    meta.className = "card-meta";
    const parts = [card.media_label || "Film"];
    if (card.year) parts.push(String(card.year));
    const rating = Number(card.vote_average) || 0;
    if (rating > 0) parts.push(`★ ${rating.toFixed(1)}`);
    meta.textContent = parts.join("  ·  ");

    const title = document.createElement("h2");
    title.className = "card-title";
    title.textContent = card.title;

    const badges = document.createElement("div");
    badges.className = "badges";
    (card.genres || []).slice(0, 3).forEach((genre) => {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = genre;
      badges.append(badge);
    });

    content.append(meta, title, badges);
    if (card.overview) {
      const overview = document.createElement("p");
      overview.className = "card-overview";
      overview.textContent = card.overview;
      content.append(overview);
    }

    const like = stamp("like", "GEFÄLLT MIR");
    const nope = stamp("nope", "NOPE");
    const skip = stamp("skip", "KENNE ICH NICHT");

    node.append(media, scrim, content, like, nope, skip);
    return node;
  }

  function stamp(kind, label) {
    const element = document.createElement("span");
    element.className = `stamp stamp-${kind}`;
    element.dataset.stamp = kind;
    element.textContent = label;
    return element;
  }

  function renderDeck() {
    const visible = state.cards.slice(0, 3);
    const visibleKeys = new Set(visible.map(keyOf));

    for (const [key, node] of Array.from(cardNodes.entries())) {
      if (!visibleKeys.has(key)) {
        node.remove();
        cardNodes.delete(key);
      }
    }

    visible
      .slice()
      .reverse()
      .forEach((card) => {
        const key = keyOf(card);
        let node = cardNodes.get(key);
        if (!node) {
          node = createCardNode(card);
          cardNodes.set(key, node);
        }
        const depth = visible.findIndex((item) => keyOf(item) === key);
        node.dataset.depth = String(depth);
        node.style.zIndex = String(10 - depth);
        node.classList.toggle("is-top", depth === 0);
        els.deck.append(node);
      });

    els.deckEmpty.hidden = visible.length > 0 || state.loading;
  }

  function mergeCards(cards) {
    const existing = new Set(state.cards.map(keyOf));
    (cards || []).forEach((card) => {
      if (!card || !card.tmdb_id) return;
      const key = keyOf(card);
      if (existing.has(key)) return;
      existing.add(key);
      state.cards.push(card);
    });
  }

  // ------------------------------------------------------------- Gestures

  function onPointerDown(event) {
    if (state.busy || state.loading || !state.cards.length) return;
    const node = event.target.closest(".card");
    if (!node || node.dataset.depth !== "0") return;
    if (event.pointerType === "mouse" && event.button !== 0) return;

    state.drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      dx: 0,
      dy: 0,
      node,
    };
    node.classList.add("dragging");
    try {
      node.setPointerCapture(event.pointerId);
    } catch (_) {
      /* nicht jeder Browser unterstuetzt das */
    }
    event.preventDefault();
  }

  function onPointerMove(event) {
    const drag = state.drag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    drag.dx = event.clientX - drag.startX;
    drag.dy = event.clientY - drag.startY;
    drawDrag(drag);
    event.preventDefault();
  }

  function drawDrag(drag) {
    const dx = drag.dx;
    const dy = drag.dy;
    const rotation = Math.max(-16, Math.min(16, dx * 0.05));
    drag.node.style.transform = `translate3d(${dx}px, ${dy}px, 0) rotate(${rotation}deg)`;

    const vertical = Math.abs(dy) > Math.abs(dx);
    setStampOpacity(drag.node, "like", clamp01(dx / SWIPE_DISTANCE()));
    setStampOpacity(drag.node, "nope", clamp01(-dx / SWIPE_DISTANCE()));
    setStampOpacity(
      drag.node,
      "skip",
      vertical && dy < 0 ? clamp01(-dy / SWIPE_UP_DISTANCE()) : 0
    );
  }

  function onPointerUp(event) {
    const drag = state.drag;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const { dx, dy, node } = drag;
    state.drag = null;
    node.classList.remove("dragging");
    try {
      node.releasePointerCapture(event.pointerId);
    } catch (_) {
      /* ignorieren */
    }

    const horizontal = Math.abs(dx) >= Math.abs(dy);
    let action = null;
    if (horizontal && dx >= SWIPE_DISTANCE()) action = "like";
    else if (horizontal && dx <= -SWIPE_DISTANCE()) action = "dislike";
    else if (!horizontal && dy <= -SWIPE_UP_DISTANCE()) action = "skip";

    if (action) {
      act(action);
    } else {
      resetCard(node);
    }
  }

  function cancelDrag() {
    if (!state.drag) return;
    const node = state.drag.node;
    state.drag = null;
    node.classList.remove("dragging");
    resetCard(node);
  }

  function resetCard(node) {
    node.style.transform = "";
    node.style.opacity = "";
    ["like", "nope", "skip"].forEach((kind) => setStampOpacity(node, kind, 0));
  }

  function setStampOpacity(node, kind, value) {
    const element = node.querySelector(`[data-stamp="${kind}"]`);
    if (element) element.style.opacity = String(value);
  }

  function flyOut(node, action) {
    if (!node) return;
    node.classList.remove("dragging");
    const width = Math.max(window.innerWidth, 480);
    const height = Math.max(window.innerHeight, 540);
    const stampKind = action === "dislike" ? "nope" : action;
    setStampOpacity(node, stampKind, 1);
    if (action === "skip") {
      node.style.transform = `translate3d(0, -${height * 0.9}px, 0) rotate(-4deg)`;
    } else {
      const direction = action === "like" ? 1 : -1;
      node.style.transform = `translate3d(${direction * width * 1.15}px, 40px, 0) rotate(${direction * 24}deg)`;
    }
    node.style.opacity = "0";
  }

  // --------------------------------------------------------------- Actions

  async function act(action) {
    if (state.busy || state.saving || !state.cards.length) return;
    if (!["like", "dislike", "skip"].includes(action)) return;

    state.busy = true;
    setActionsDisabled(true);
    hideError(els.quizError);

    const card = state.cards[0];
    const node = cardNodes.get(keyOf(card));
    flyOut(node, action);
    await sleep(230);

    state.cards.shift();
    if (node) {
      node.remove();
      cardNodes.delete(keyOf(card));
    }
    renderDeck();
    state.swipes += 1;
    updateProgress();

    try {
      await apiPost("/api/v1/quiz/swipe", {
        session_id: state.sessionId,
        tmdb_id: card.tmdb_id,
        media_type: card.media_type || "movie",
        action,
      });
    } catch (error) {
      if (error.status === 404) {
        showError(els.quizError, "Deine Quiz-Session ist abgelaufen. Bitte starte neu.");
        state.serverDone = true;
      } else {
        showError(els.quizError, `Swipe konnte nicht gespeichert werden: ${error.message}`);
      }
    }

    if (!state.serverDone) await maybeLoadMore();
    state.busy = false;
    setActionsDisabled(false);
    updateProgress();
    renderDeck();
  }

  async function maybeLoadMore() {
    if (state.cards.length > PREFETCH_THRESHOLD) return;
    await loadMore(false);
  }

  async function loadMore(force) {
    if (!state.sessionId || state.loading) return;
    if (!force && state.cards.length > PREFETCH_THRESHOLD) return;
    state.loading = true;
    els.deckLoading.hidden = false;
    els.loadMoreBtn.disabled = true;

    try {
      const data = await apiPost("/api/v1/quiz/next", {
        session_id: state.sessionId,
        limit: 3,
      });
      mergeCards(data.cards);
      if (data.done) state.serverDone = true;
      if (Number(data.max_answers)) state.maxAnswers = Number(data.max_answers);
    } catch (error) {
      showError(
        els.quizError,
        `Nächste Karten konnten nicht geladen werden: ${error.message}`
      );
    } finally {
      state.loading = false;
      els.deckLoading.hidden = true;
      els.loadMoreBtn.disabled = false;
      renderDeck();
      updateProgress();
      updateFinishState();
    }
  }

  function updateProgress() {
    const goal = Math.max(1, Math.min(10, state.maxAnswers));
    const ratio = clamp01(state.swipes / goal);
    els.progressBar.style.width = `${(ratio * 100).toFixed(1)}%`;
    els.progressTrack.setAttribute("aria-valuenow", String(Math.round(ratio * 100)));
    els.progressLabel.textContent = `${state.swipes} ${state.swipes === 1 ? "Swipe" : "Swipes"}`;
    if (state.loading) {
      els.queueLabel.textContent = "Neue Karten werden geladen …";
    } else if (state.serverDone) {
      els.queueLabel.textContent = "Quiz abgeschlossen";
    } else if (state.cards.length) {
      els.queueLabel.textContent = `${state.cards.length} ${state.cards.length === 1 ? "Karte" : "Karten"} bereit`;
    } else {
      els.queueLabel.textContent = "Keine Karten";
    }
    updateFinishState();
  }

  function updateFinishState() {
    const ready = state.swipes >= MIN_SWIPES;
    els.finishBtn.disabled = !ready || state.saving || state.busy;
    els.skipToSave.hidden = !(ready && state.swipes < 10);
    if (state.saving) {
      els.finishBtn.textContent = "Profil wird gespeichert …";
    } else if (!ready) {
      const missing = MIN_SWIPES - state.swipes;
      els.finishBtn.textContent = `Noch ${missing} ${missing === 1 ? "Karte" : "Karten"} bewerten`;
    } else {
      els.finishBtn.textContent = "Profil speichern";
    }
  }

  function setActionsDisabled(disabled) {
    els.actions.forEach((button) => {
      button.disabled = disabled;
    });
  }

  function onKeyDown(event) {
    if (state.view !== "quiz" || state.busy) return;
    const target = event.target;
    if (target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) {
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      act("dislike");
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      act("like");
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      act("skip");
    }
  }

  // ----------------------------------------------------------------- Done

  async function finish() {
    if (state.saving || state.busy) return;
    if (state.swipes < MIN_SWIPES) {
      showError(
        els.quizError,
        `Bitte bewerte mindestens ${MIN_SWIPES} Karten, damit dein Profil aussagekräftig ist.`
      );
      return;
    }
    state.saving = true;
    updateFinishState();
    hideError(els.quizError);

    try {
      const data = await apiPost("/api/v1/quiz/complete", {
        session_id: state.sessionId,
      });
      renderDone(data);
      showView("done");
    } catch (error) {
      if (error.status === 404) {
        showError(els.quizError, "Deine Session ist abgelaufen. Bitte starte das Quiz neu.");
      } else {
        showError(els.quizError, `Profil konnte nicht gespeichert werden: ${error.message}`);
      }
    } finally {
      state.saving = false;
      updateFinishState();
    }
  }

  function renderDone(data) {
    const user = data.user || {};
    const taste = user.taste || {};
    const counts = user.counts || {};

    els.doneTitle.textContent = `Danke, ${user.name || state.name}!`;
    els.doneSub.textContent =
      "Dein Geschmacksprofil ist gespeichert. CineMatch findet jetzt gemeinsame Filme, die niemanden im Haushalt ärgern.";

    els.doneAvatar.replaceChildren();
    const initial = document.createElement("span");
    initial.textContent = (user.name || state.name || "?").trim().charAt(0).toUpperCase();
    els.doneAvatar.append(initial);
    if (user.avatar_url) {
      const image = document.createElement("img");
      image.src = user.avatar_url;
      image.alt = "";
      image.addEventListener("error", () => image.remove());
      els.doneAvatar.append(image);
    }

    els.doneGenres.replaceChildren();
    (taste.top || []).slice(0, 5).forEach((genre) => {
      const badge = document.createElement("span");
      badge.className = "badge badge-strong";
      badge.textContent = genre.name;
      els.doneGenres.append(badge);
    });
    if (!(taste.top || []).length) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "Noch keine Genre-Präferenz";
      els.doneGenres.append(badge);
    }

    els.doneStats.replaceChildren();
    const stats = [
      ["Likes", counts.liked ?? 0],
      ["Dislikes", counts.disliked ?? 0],
      ["Egal", counts.skipped ?? 0],
      ["Swipes", user.swipes ?? state.swipes],
    ];
    stats.forEach(([label, value]) => {
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = String(value);
      els.doneStats.append(term, detail);
    });

    const presence = data.presence || {};
    els.donePresence.hidden = false;
    els.donePresence.className = "presence-status";
    if (presence.reachable) {
      els.donePresence.classList.add("ok");
      els.donePresence.textContent = "✓ Dein Gerät antwortet auf Ping. CineMatch kann es automatisch als anwesend erkennen.";
    } else {
      els.donePresence.classList.add("muted");
      els.donePresence.textContent = "Dein Gerät antwortet gerade nicht auf Ping. Die Anwesenheit wird beim nächsten Abruf erneut geprüft.";
    }

    els.doneIp.textContent = data.client_ip || "deiner IP";
  }

  function restartQuiz() {
    if (state.view === "quiz" && state.swipes > 0) {
      const confirmed = window.confirm(
        "Quiz neu starten? Deine bisherigen Antworten werden verworfen."
      );
      if (!confirmed) return;
    }
    state.sessionId = null;
    state.cards = [];
    state.swipes = 0;
    state.serverDone = false;
    state.loading = false;
    state.busy = false;
    state.saving = false;
    cardNodes.clear();
    els.deck.replaceChildren();
    hideError(els.quizError);
    els.name.value = state.name || els.name.value;
    Array.from(document.querySelectorAll('input[name="providers"]')).forEach((input) => {
      input.checked = state.providers.includes(input.value);
      input.closest(".chip")?.classList.toggle("selected", input.checked);
    });
    showView("basics");
    updateProgress();
  }

  // ---------------------------------------------------------------- Views

  function showView(view) {
    state.view = view;
    els.basics.hidden = view !== "basics";
    els.quiz.hidden = view !== "quiz";
    els.done.hidden = view !== "done";
    document.body.classList.toggle("quiz-active", view === "quiz");
    window.scrollTo(0, 0);
  }

  function showEmptyDeck(force) {
    els.deckEmpty.hidden = !force || state.cards.length > 0 || state.loading;
  }

  function showError(element, message) {
    element.textContent = message;
    element.hidden = false;
  }

  function hideError(element) {
    element.hidden = true;
    element.textContent = "";
  }
})();
