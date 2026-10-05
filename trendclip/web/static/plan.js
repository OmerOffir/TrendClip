(() => {
  "use strict";

  const T = window.TrendClip;
  const { api, esc } = T;
  const $ = (id) => document.getElementById(id);

  const els = {
    view: $("viewPlan"),
    error: $("planError"),
    navCount: $("navPlanCount"),
    prev: $("pPrev"),
    today: $("pToday"),
    next: $("pNext"),
    goals: $("pGoals"),
    gReels: $("gReels"),
    gLong: $("gLong"),
    stats: $("pStats"),
    backlog: $("pBacklog"),
    showPosted: $("pShowPosted"),
    days: $("pDays"),
    dialog: $("pDialog"),
    form: $("pForm"),
    formTitle: $("pFormTitle"),
    kind: $("pKind"),
    short: $("pShort"),
    titleField: $("pTitleField"),
    title: $("pTitle"),
    date: $("pDate"),
    platforms: $("pPlatforms"),
    notes: $("pNotes"),
    del: $("pDelete"),
    cancel: $("pCancel"),
    save: $("pSave"),
  };

  const PLATFORMS = [
    { id: "youtube", label: "YouTube", short: "YT" },
    { id: "instagram", label: "Instagram", short: "IG" },
    { id: "tiktok", label: "TikTok", short: "TT" },
  ];
  const DAYS_SHOWN = 7;
  const DAY_MS = 86400000;

  const state = {
    started: false,
    data: null,
    offset: 0, // days from today to the first day shown
    showPosted: false,
    form: null, // { id|null, kind, platforms:Set }
  };

  function showError(message) {
    els.error.textContent = message;
    els.error.hidden = !message;
  }

  // ---- dates (local calendar days as YYYY-MM-DD) ------------------------------------------

  const pad = (n) => String(n).padStart(2, "0");
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const parse = (s) => {
    const [y, m, d] = s.split("-").map(Number);
    return new Date(y, m - 1, d);
  };
  const todayDate = () => {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  };
  const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  const todayIso = () => iso(todayDate());
  const diffDays = (s) => Math.round((parse(s) - todayDate()) / DAY_MS);
  const weekStart = (d) => addDays(d, -((d.getDay() + 6) % 7)); // Monday

  function dayName(s) {
    const n = diffDays(s);
    if (n === 0) return "Today";
    if (n === 1) return "Tomorrow";
    if (n === 2) return "Day after tomorrow";
    if (n === -1) return "Yesterday";
    return parse(s).toLocaleDateString(undefined, { weekday: "long" });
  }
  const shortDate = (s) => parse(s).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

  // ---- data helpers -------------------------------------------------------------------

  const goals = () => state.data.goals;
  const itemsOn = (day, kind) => state.data.items.filter((i) => i.date === day && (!kind || i.kind === kind));
  const shortByName = (name) => state.data.shorts.find((s) => s.filename === name);

  function weekItems(start, kind) {
    const from = iso(start);
    const to = iso(addDays(start, 6));
    return state.data.items.filter((i) => i.kind === kind && i.date >= from && i.date <= to);
  }

  const allPosted = (s) => PLATFORMS.every((p) => s.status[p.id] && s.status[p.id].done);

  function leftToday() {
    const day = todayIso();
    const reels = itemsOn(day, "reel");
    const open = itemsOn(day).filter((i) => !i.done).length;
    return open + Math.max(0, goals().reels_per_day - reels.length);
  }

  // ---- loading ------------------------------------------------------------------------

  async function load() {
    try {
      state.data = (await api("/api/plan")).body;
      showError("");
    } catch (err) {
      showError(`Could not load the plan: ${err.message}`);
      return;
    }
    renderNav();
    if (!els.view.hidden) render();
  }

  function renderNav() {
    const left = leftToday();
    els.navCount.textContent = String(left);
    els.navCount.hidden = !left;
  }

  async function send(path, method, body) {
    const { body: out } = await api(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return out;
  }

  async function mutate(fn) {
    try {
      await fn();
      showError("");
    } catch (err) {
      showError(err.message);
    }
    await load();
    document.dispatchEvent(new CustomEvent("trendclip:plan-changed"));
  }

  // ---- rendering ----------------------------------------------------------------------

  function render() {
    if (!state.data) return;
    renderGoals();
    renderStats();
    renderBacklog();
    renderDays();
  }

  function platformChips(selected, attr) {
    return PLATFORMS.map((p) => `<button type="button" class="pf-chip pf-${p.id}${selected.includes(p.id) ? " on" : ""}"
      ${attr}="${p.id}" aria-pressed="${selected.includes(p.id)}">${p.label}</button>`).join("");
  }

  function renderGoals() {
    const g = goals();
    if (document.activeElement !== els.gReels) els.gReels.value = g.reels_per_day;
    if (document.activeElement !== els.gLong) els.gLong.value = g.long_per_week;
    els.goals.querySelectorAll("[data-goal]").forEach((span) => {
      span.innerHTML = platformChips(g[span.dataset.goal], "data-goal-platform");
    });
  }

  function stat(label, value, sub, cls) {
    return `<div class="plan-stat ${cls || ""}"><span class="stat-label">${esc(label)}</span>
      <span class="plan-stat-value">${value}</span><span class="stat-sub">${sub}</span></div>`;
  }

  function renderStats() {
    const g = goals();
    const out = [];
    for (let n = 0; n < 3; n++) {
      const day = iso(addDays(todayDate(), n));
      const reels = itemsOn(day, "reel");
      const done = reels.filter((i) => i.done).length;
      const label = dayName(day);
      if (n === 0) {
        const cls = reels.length && done >= g.reels_per_day ? "good" : "";
        out.push(stat(`${label} · reels posted`, `${done}/${g.reels_per_day}`,
          `${reels.length} planned`, cls));
      } else {
        const cls = reels.length >= g.reels_per_day ? "good" : "warn";
        out.push(stat(`${label} · reels planned`, `${reels.length}/${g.reels_per_day}`,
          reels.length >= g.reels_per_day ? "all set" : `${g.reels_per_day - reels.length} to pick`, cls));
      }
    }
    for (const [label, start] of [["This week", weekStart(todayDate())], ["Next week", addDays(weekStart(todayDate()), 7)]]) {
      const longs = weekItems(start, "long");
      const done = longs.filter((i) => i.done).length;
      const cls = done >= g.long_per_week ? "good" : longs.length >= g.long_per_week ? "" : "warn";
      out.push(stat(`${label} · long videos`, `${done}/${g.long_per_week}`,
        `${longs.length} planned · ${shortDate(iso(start))} – ${shortDate(iso(addDays(start, 6)))}`, cls));
    }
    const late = state.data.items.filter((i) => i.date < todayIso() && !i.done).length;
    if (late) out.push(stat("Late", String(late), "past days not posted everywhere", "bad"));
    els.stats.innerHTML = out.join("");
  }

  function thumb(filename) {
    return filename
      ? `<video src="/media/shorts/${encodeURIComponent(filename)}#t=1" muted preload="metadata" playsinline></video>`
      : '<span class="thumb-ph" aria-hidden="true">▶</span>';
  }

  function shortMeta(s) {
    return [s.part ? `Part ${s.part}/${s.parts_total || 2}` : "", s.game,
      s.duration_seconds ? `${Math.round(s.duration_seconds)}s` : ""].filter(Boolean).map(esc).join(" · ");
  }

  function renderBacklog() {
    const list = state.data.shorts
      .filter((s) => !s.planned.length && (state.showPosted || !allPosted(s)))
      .sort((a, b) => (b.ready - a.ready) || b.created_at.localeCompare(a.created_at));
    if (!list.length) {
      els.backlog.innerHTML = state.data.shorts.length
        ? '<div class="empty">Every Short is on the plan. Make more in <a href="#create" data-goto="create"><b>Create</b></a>.</div>'
        : '<div class="empty">No Shorts yet. Make one in <a href="#create" data-goto="create"><b>Create</b></a>.</div>';
      return;
    }
    const tomorrow = iso(addDays(todayDate(), 1));
    const after = iso(addDays(todayDate(), 2));
    els.backlog.innerHTML = list.map((s) => `
      <div class="backlog-card" draggable="true" data-drag-short="${esc(s.filename)}">
        ${thumb(s.filename)}
        <div class="backlog-body">
          <div class="dl-title">${esc(s.title)}</div>
          <div class="dl-meta">${shortMeta(s)}${s.ready ? ' · <span class="pill ready">ready</span>' : ""}</div>
          <div class="backlog-actions">
            <button type="button" class="dl-copy" data-plan-short="${esc(s.filename)}" data-day="${tomorrow}">Tomorrow</button>
            <button type="button" class="dl-copy" data-plan-short="${esc(s.filename)}" data-day="${after}">Day after</button>
            <button type="button" class="dl-copy" data-pick-short="${esc(s.filename)}">Pick a day…</button>
          </div>
        </div>
      </div>`).join("");
  }

  function statusChips(item) {
    return item.platforms.map((pid) => {
      const p = PLATFORMS.find((x) => x.id === pid);
      const st = item.status[pid] || {};
      const title = st.auto
        ? (st.scheduled_for ? `Scheduled on YouTube from the Upload tab` : `Uploaded from the Upload tab`)
        : st.done ? `Marked as posted on ${p.label}; click to undo` : `Click once it's posted on ${p.label}`;
      const link = st.url ? ` data-url="${esc(st.url)}"` : "";
      return `<button type="button" class="pf-chip pf-${pid}${st.done ? " on" : ""}${st.auto ? " auto" : ""}"
        data-toggle="${pid}" data-item="${esc(item.id)}"${link} title="${esc(title)}" aria-pressed="${!!st.done}"
        >${st.done ? "✓ " : ""}${p.label}</button>`;
    }).join("");
  }

  function itemRow(item) {
    const s = item.short_info;
    const meta = [];
    if (item.kind === "long") meta.push('<span class="pill long">LONG</span>');
    if (s) meta.push(shortMeta(s));
    else if (item.short_missing) meta.push('<span class="dim">Short was deleted</span>');
    if (item.notes) meta.push(`<span class="item-notes">${esc(item.notes)}</span>`);
    return `
      <div class="plan-item${item.done ? " done" : ""}" draggable="true" data-drag-item="${esc(item.id)}">
        ${thumb(s ? item.short : null)}
        <div class="plan-item-body">
          <div class="dl-title">${item.done ? '<span class="done-mark">✓</span> ' : ""}${esc(item.title)}</div>
          <div class="dl-meta">${meta.join(" · ")}</div>
          <div class="pf-row">${statusChips(item)}</div>
        </div>
        <div class="plan-item-actions">
          ${s ? `<button type="button" class="dl-copy" data-open-upload="${esc(item.short)}" title="Texts, captions and YouTube upload">Upload tab</button>` : ""}
          <button type="button" class="dl-copy" data-edit="${esc(item.id)}">Edit</button>
        </div>
      </div>`;
  }

  function dayCard(day, late) {
    const g = goals();
    const reels = late ? itemsOn(day).filter((i) => !i.done && i.kind === "reel") : itemsOn(day, "reel");
    const longs = late ? itemsOn(day).filter((i) => !i.done && i.kind === "long") : itemsOn(day, "long");
    const past = day < todayIso();
    const reelsDone = reels.filter((i) => i.done).length;
    const empty = past ? 0 : Math.max(0, g.reels_per_day - reels.length);
    let pill;
    if (past) pill = reels.length + longs.length ? `<span class="pill ${reels.concat(longs).every((i) => i.done) ? "done" : "warn"}">${reelsDone}/${reels.length} reels posted</span>` : "";
    else pill = `<span class="pill ${reelsDone >= g.reels_per_day && g.reels_per_day ? "done" : reels.length >= g.reels_per_day ? "" : "warn"}">${reelsDone}/${g.reels_per_day} reels posted</span>`;
    const slots = Array.from({ length: empty }, (_, i) => `
      <button type="button" class="plan-slot" data-add="reel" data-day="${day}">+ Reel ${reels.length + i + 1}</button>`).join("");
    const cls = ["plan-day"];
    if (day === todayIso()) cls.push("today");
    if (past) cls.push("past");
    return `
      <section class="card ${cls.join(" ")}" data-drop-day="${day}">
        <div class="plan-day-head">
          <div><h3>${esc(dayName(day))}</h3><span class="hint">${esc(shortDate(day))}</span></div>
          ${pill}
        </div>
        <div class="plan-group">
          ${reels.map(itemRow).join("")}${slots}
          ${!reels.length && !empty ? '<div class="hint">No reels.</div>' : ""}
        </div>
        ${longs.length ? `<div class="plan-group long">${longs.map(itemRow).join("")}</div>` : ""}
        ${past ? "" : `<button type="button" class="plan-add-long" data-add="long" data-day="${day}">+ Long video</button>`}
      </section>`;
  }

  function renderDays() {
    const start = addDays(todayDate(), state.offset);
    const out = [];
    if (state.offset === 0) {
      const lateDays = [...new Set(state.data.items.filter((i) => i.date < todayIso() && !i.done).map((i) => i.date))];
      if (lateDays.length) {
        out.push(`<div class="plan-late-head"><h2>Late</h2><span class="hint">Planned for an earlier day and not posted everywhere yet. Tick them, or drag them to a new day.</span></div>`);
        out.push(...lateDays.map((d) => dayCard(d, true)));
        out.push('<div class="plan-late-head"><h2>Coming up</h2></div>');
      }
    }
    for (let n = 0; n < DAYS_SHOWN; n++) out.push(dayCard(iso(addDays(start, n)), false));
    els.days.innerHTML = out.join("");
    els.today.disabled = state.offset === 0;
  }

  // ---- actions ------------------------------------------------------------------------

  function planShort(filename, day) {
    return mutate(() => send("/api/plan/items", "POST", { kind: "reel", date: day, short: filename }));
  }

  function toggle(itemId, platform) {
    const item = state.data.items.find((i) => i.id === itemId);
    const st = item && item.status[platform];
    if (!st) return;
    if (st.auto) {
      if (st.url) window.open(st.url, "_blank", "noopener");
      return;
    }
    return mutate(() => send(`/api/plan/items/${encodeURIComponent(itemId)}/posted/${platform}`, "POST", { posted: !st.done }));
  }

  function moveItem(itemId, day) {
    const item = state.data.items.find((i) => i.id === itemId);
    if (!item || item.date === day) return;
    return mutate(() => send(`/api/plan/items/${encodeURIComponent(itemId)}`, "PATCH", { date: day }));
  }

  let goalsTimer = null;
  function saveGoals(patch) {
    Object.assign(state.data.goals, patch);
    renderGoals();
    renderNav();
    clearTimeout(goalsTimer);
    goalsTimer = setTimeout(() => mutate(() => send("/api/plan/goals", "PUT", state.data.goals)), 400);
    renderStats();
    renderDays();
  }

  function toggleGoalPlatform(key, platform) {
    const list = new Set(goals()[key]);
    if (list.has(platform)) list.delete(platform);
    else list.add(platform);
    if (!list.size) return;
    saveGoals({ [key]: PLATFORMS.map((p) => p.id).filter((p) => list.has(p)) });
  }

  // ---- dialog -------------------------------------------------------------------------

  function shortOptions(selected) {
    const free = state.data.shorts.filter((s) => !s.planned.length || s.filename === selected);
    const used = state.data.shorts.filter((s) => s.planned.length && s.filename !== selected);
    const opt = (s, extra) => `<option value="${esc(s.filename)}"${s.filename === selected ? " selected" : ""}>${esc(s.title)}${extra || ""}</option>`;
    return [
      `<option value=""${selected ? "" : " selected"}>Something else (type a title below)</option>`,
      free.length ? `<optgroup label="Your Shorts">${free.map((s) => opt(s)).join("")}</optgroup>` : "",
      used.length ? `<optgroup label="Already planned">${used.map((s) => opt(s, ` (${s.planned.map(shortDate).join(", ")})`)).join("")}</optgroup>` : "",
    ].join("");
  }

  function renderForm() {
    const f = state.form;
    els.kind.querySelectorAll("[data-kind]").forEach((b) => b.classList.toggle("active", b.dataset.kind === f.kind));
    els.titleField.hidden = !!els.short.value;
    els.platforms.innerHTML = platformChips([...f.platforms], "data-form-platform");
  }

  function openDialog({ id = null, kind = "reel", day = iso(addDays(todayDate(), 1)), short = "" } = {}) {
    const item = id ? state.data.items.find((i) => i.id === id) : null;
    if (item) {
      kind = item.kind;
      day = item.date;
      short = item.short_info ? item.short : "";
    }
    state.form = {
      id,
      kind,
      platforms: new Set(item ? item.platforms : goals()[kind === "reel" ? "reel_platforms" : "long_platforms"]),
    };
    els.formTitle.textContent = item ? "Edit plan item" : kind === "long" ? "Add a long video" : "Add a reel";
    els.short.innerHTML = shortOptions(short);
    els.title.value = item && !item.short_info ? item.title : "";
    els.date.value = day;
    els.notes.value = item ? item.notes : "";
    els.del.hidden = !item;
    renderForm();
    els.dialog.showModal();
    (short ? els.date : els.short).focus();
  }

  function setFormKind(kind) {
    const f = state.form;
    if (f.kind === kind) return;
    f.kind = kind;
    if (!f.id) f.platforms = new Set(goals()[kind === "reel" ? "reel_platforms" : "long_platforms"]);
    renderForm();
  }

  async function submitForm(e) {
    e.preventDefault();
    const f = state.form;
    const short = els.short.value;
    const body = {
      kind: f.kind,
      date: els.date.value,
      notes: els.notes.value,
      platforms: PLATFORMS.map((p) => p.id).filter((p) => f.platforms.has(p)),
    };
    if (!body.date) return els.date.focus();
    if (!body.platforms.length) return showError("Pick at least one platform");
    if (short) body.short = short;
    else {
      body.title = els.title.value.trim();
      if (!body.title) return els.title.focus();
      if (f.id) body.unlink = true;
    }
    els.dialog.close();
    await mutate(() => (f.id
      ? send(`/api/plan/items/${encodeURIComponent(f.id)}`, "PATCH", body)
      : send("/api/plan/items", "POST", body)));
  }

  async function deleteFromForm() {
    const id = state.form && state.form.id;
    if (!id) return;
    els.dialog.close();
    await mutate(() => send(`/api/plan/items/${encodeURIComponent(id)}`, "DELETE"));
  }

  // ---- events -------------------------------------------------------------------------

  function onClick(e) {
    const goto = e.target.closest("[data-goto]");
    if (goto) {
      e.preventDefault();
      return T.showView(goto.dataset.goto);
    }
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.toggle) return toggle(t.dataset.item, t.dataset.toggle);
    if (t.dataset.planShort) return planShort(t.dataset.planShort, t.dataset.day);
    if (t.dataset.pickShort) return openDialog({ short: t.dataset.pickShort });
    if (t.dataset.add) return openDialog({ kind: t.dataset.add, day: t.dataset.day });
    if (t.dataset.edit) return openDialog({ id: t.dataset.edit });
    if (t.dataset.openUpload) return T.showView("upload", t.dataset.openUpload);
    if (t.dataset.goalPlatform) return toggleGoalPlatform(t.closest("[data-goal]").dataset.goal, t.dataset.goalPlatform);
  }

  function bindDrag() {
    els.view.addEventListener("dragstart", (e) => {
      const el = e.target.closest("[data-drag-item],[data-drag-short]");
      if (!el) return;
      const payload = el.dataset.dragItem ? `item:${el.dataset.dragItem}` : `short:${el.dataset.dragShort}`;
      e.dataTransfer.setData("text/plain", payload);
      e.dataTransfer.effectAllowed = "move";
      el.classList.add("dragging");
    });
    els.view.addEventListener("dragend", (e) => {
      const el = e.target.closest(".dragging");
      if (el) el.classList.remove("dragging");
      els.days.querySelectorAll(".drop-over").forEach((d) => d.classList.remove("drop-over"));
    });
    els.days.addEventListener("dragover", (e) => {
      const day = e.target.closest("[data-drop-day]");
      if (!day) return;
      e.preventDefault();
      els.days.querySelectorAll(".drop-over").forEach((d) => d !== day && d.classList.remove("drop-over"));
      day.classList.add("drop-over");
    });
    els.days.addEventListener("drop", (e) => {
      const day = e.target.closest("[data-drop-day]");
      if (!day) return;
      e.preventDefault();
      day.classList.remove("drop-over");
      const [kind, ...rest] = e.dataTransfer.getData("text/plain").split(":");
      const value = rest.join(":");
      if (kind === "item") moveItem(value, day.dataset.dropDay);
      if (kind === "short") planShort(value, day.dataset.dropDay);
    });
  }

  function bind() {
    els.view.addEventListener("click", onClick);
    els.prev.addEventListener("click", () => { state.offset -= DAYS_SHOWN; renderDays(); });
    els.next.addEventListener("click", () => { state.offset += DAYS_SHOWN; renderDays(); });
    els.today.addEventListener("click", () => { state.offset = 0; renderDays(); });
    els.gReels.addEventListener("change", () => {
      const n = Math.min(10, Math.max(0, parseInt(els.gReels.value, 10) || 0));
      saveGoals({ reels_per_day: n });
    });
    els.gLong.addEventListener("change", () => {
      const n = Math.min(14, Math.max(0, parseInt(els.gLong.value, 10) || 0));
      saveGoals({ long_per_week: n });
    });
    els.showPosted.addEventListener("change", () => {
      state.showPosted = els.showPosted.checked;
      renderBacklog();
    });
    els.kind.addEventListener("click", (e) => {
      const b = e.target.closest("[data-kind]");
      if (b) setFormKind(b.dataset.kind);
    });
    els.short.addEventListener("change", renderForm);
    els.platforms.addEventListener("click", (e) => {
      const b = e.target.closest("[data-form-platform]");
      if (!b) return;
      const set = state.form.platforms;
      if (set.has(b.dataset.formPlatform)) set.delete(b.dataset.formPlatform);
      else set.add(b.dataset.formPlatform);
      renderForm();
    });
    els.form.addEventListener("submit", submitForm);
    els.cancel.addEventListener("click", () => els.dialog.close());
    els.del.addEventListener("click", deleteFromForm);
    bindDrag();
    for (const ev of ["trendclip:shorts-changed", "trendclip:ready", "trendclip:posted"]) document.addEventListener(ev, load);
    document.addEventListener("trendclip:view", (e) => {
      if (e.detail.view === "plan") start();
    });
    // The day labels ("Today", "Tomorrow") move at midnight.
    let day = todayIso();
    setInterval(() => {
      if (todayIso() !== day) {
        day = todayIso();
        load();
      }
    }, 60000);
  }

  function start() {
    state.started = true;
    load();
  }

  bind();
  if (!els.view.hidden) start();
  else load(); // fills the nav counter
})();
