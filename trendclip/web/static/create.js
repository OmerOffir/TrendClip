(() => {
  "use strict";

  const T = window.TrendClip;
  const { api, esc } = T;
  const $ = (id) => document.getElementById(id);
  const els = {
    banner: $("createBanner"),
    error: $("createError"),
    picker: $("clipPicker"),
    geminiInfo: $("geminiInfo"),
    game: $("cGame"),
    length: $("cLength"),
    notes: $("cNotes"),
    watch: $("cWatch"),
    write: $("cWrite"),
    scriptJob: $("cScriptJob"),
    onScreen: $("cOnScreen"),
    script: $("cScript"),
    scriptStats: $("cScriptStats"),
    title: $("cTitle"),
    desc: $("cDesc"),
    tags: $("cTags"),
    voice: $("cVoice"),
    rate: $("cRate"),
    highlight: $("cHighlight"),
    words: $("cWords"),
    fit: $("cFit"),
    phone: $("cPhone"),
    preview: $("cPreview"),
    phoneSub: $("cPhoneSub"),
    phoneEmpty: $("cPhoneEmpty"),
    render: $("cRender"),
    renderJob: $("cRenderJob"),
    shorts: $("shortsList"),
  };

  const DRAFT_KEY = "trendclip.createDraft";
  const DRAFT_FIELDS = ["game", "length", "notes", "script", "title", "desc", "tags", "voice", "rate", "highlight", "words", "fit"];
  const HIGHLIGHT_CSS = { yellow: "#ffff00", cyan: "#00ffff", green: "#00ff00", orange: "#ffa500" };

  const state = {
    status: null,
    clip: null, // selected clip filename
    shorts: [],
    scriptJob: null,
    renderJob: null,
    started: false,
  };

  const clips = () => T.getClips() || [];
  const selectedClip = () => clips().find((c) => c.filename === state.clip) || null;
  const clipUrl = (c) => `/media/backgrounds/${encodeURIComponent(c.filename)}`;

  function showError(message) {
    els.error.hidden = !message;
    els.error.textContent = message || "";
  }

  // ---- draft persistence ---------------------------------------------------------------

  function saveDraft() {
    const draft = { clip: state.clip };
    for (const key of DRAFT_FIELDS) draft[key] = els[key].value;
    draft.watch = els.watch.checked;
    localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  }

  function loadDraft() {
    try {
      const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "{}");
      for (const key of DRAFT_FIELDS) if (draft[key] != null && key !== "voice") els[key].value = draft[key];
      if (draft.watch != null) els.watch.checked = draft.watch;
      state.clip = draft.clip || null;
      return draft;
    } catch (_) {
      return {};
    }
  }

  // ---- clip picker ---------------------------------------------------------------------

  function renderPicker() {
    const list = clips();
    if (state.clip && !list.some((c) => c.filename === state.clip)) state.clip = null;
    if (!list.length) {
      els.picker.innerHTML =
        '<div class="empty">No clips yet. Go to <a href="#trends" data-goto="trends"><b>Trends</b></a> and press <b>Get gameplay</b> on a game.</div>';
      updatePreview();
      return;
    }
    els.picker.innerHTML = list.map((c) => {
      const portrait = c.height > c.width;
      const lowRes = Math.min(c.width || 0, c.height || 0) < 720;
      return `
        <button type="button" class="clip-card ${c.filename === state.clip ? "selected" : ""}" data-clip="${esc(c.filename)}">
          <video src="${clipUrl(c)}#t=2" muted preload="metadata" playsinline></video>
          <div class="clip-info">
            <div class="clip-game">${esc(c.game)}</div>
            <div class="clip-meta">
              <span class="pill">${portrait ? "9:16" : "16:9"}</span>
              ${c.duration_seconds ? `<span class="pill">${esc(T.fmtLength(c.duration_seconds))}</span>` : ""}
              ${lowRes ? `<span class="pill warn" title="Below 720p: will look soft at 1080x1920">${c.width}×${c.height}</span>` : ""}
            </div>
          </div>
        </button>`;
    }).join("");
    updatePreview();
  }

  function selectClip(filename) {
    const previous = selectedClip();
    state.clip = filename;
    const clip = selectedClip();
    if (clip && (!els.game.value.trim() || (previous && els.game.value === previous.game))) els.game.value = clip.game;
    renderPicker();
    saveDraft();
    updateButtons();
  }

  // ---- preview ---------------------------------------------------------------------------

  function updatePreview() {
    const clip = selectedClip();
    els.phoneEmpty.hidden = !!clip;
    els.preview.hidden = !clip;
    els.phone.classList.toggle("blur", els.fit.value === "blur");
    if (clip) {
      const src = clipUrl(clip);
      if (els.preview.getAttribute("src") !== src) {
        els.preview.src = src;
        els.preview.play().catch(() => {});
      }
      els.phone.style.setProperty("--clip", `url("${src}")`);
    } else {
      els.preview.removeAttribute("src");
    }
    const n = Number(els.words.value);
    const sample = ["THIS", "IS", "YOUR", "SHORT"].slice(0, n);
    const active = Math.min(1, n - 1);
    els.phoneSub.innerHTML = sample.map((w, i) => (i === active ? `<b>${w}</b>` : w)).join(" ");
    els.phoneSub.style.setProperty("--hl", HIGHLIGHT_CSS[els.highlight.value] || "#ffff00");
  }

  function scriptStats() {
    const words = els.script.value.trim().split(/\s+/).filter(Boolean).length;
    const wps = (state.status && state.status.words_per_second) || 3.6;
    const rate = 1 + parseInt(els.rate.value, 10) / 100 - 0.05;
    const secs = words / (wps * rate);
    const target = Number(els.length.value);
    els.scriptStats.textContent = words ? `· ${words} words ≈ ${Math.round(secs)}s` : "";
    els.scriptStats.classList.toggle("over", words > 0 && secs > target + 8);
    updateButtons();
  }

  function updateButtons() {
    const busyScript = state.scriptJob != null;
    const busyRender = state.renderJob != null;
    const hasClip = !!selectedClip();
    const gemini = state.status && state.status.gemini_enabled;
    els.write.disabled = busyScript || !hasClip || !els.game.value.trim() || !gemini;
    els.write.title = !gemini ? "Add GEMINI_API_KEY to .env first" : !hasClip ? "Pick a clip first" : "";
    els.render.disabled = busyRender || !hasClip || els.script.value.trim().split(/\s+/).length < 3;
    els.render.textContent = busyRender ? "Creating…" : "Create Short";
  }

  // ---- jobs ------------------------------------------------------------------------------

  function renderJob(box, job) {
    box.hidden = false;
    if (job.status === "error") {
      box.innerHTML = `<div class="dl-error">${esc(job.error || "Failed")}</div>`;
      return;
    }
    const pctDone = job.progress == null ? null : Math.round(job.progress * 100);
    box.innerHTML = `
      <div class="progress ${pctDone == null ? "indeterminate" : ""}"><i style="width:${pctDone ?? 0}%"></i></div>
      <div class="dl-meta">${esc(job.message)}${pctDone != null && job.status !== "done" ? ` · ${pctDone}%` : ""}</div>`;
  }

  function poll(jobId, box, onDone, onEnd) {
    const timer = setInterval(async () => {
      let job;
      try {
        job = (await api(`/api/create/jobs/${jobId}`)).body;
      } catch (err) {
        return; // transient; try again
      }
      renderJob(box, job);
      if (job.status === "done" || job.status === "error") {
        clearInterval(timer);
        onEnd();
        if (job.status === "done") onDone(job.result);
      }
    }, 1200);
  }

  function trendTitles(game) {
    const data = T.getData();
    const match = data && data.games.find((g) => g.name.toLowerCase() === game.toLowerCase());
    if (!match) return [];
    return [...match.videos].sort((a, b) => b.views_per_hour - a.views_per_hour).slice(0, 8).map((v) => v.title);
  }

  async function writeScript() {
    showError("");
    const game = els.game.value.trim();
    const titles = trendTitles(game);
    try {
      const { body } = await api("/api/create/script", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clip: state.clip,
          game,
          trend_titles: titles,
          notes: els.notes.value,
          target_seconds: Number(els.length.value),
          watch_clip: els.watch.checked,
        }),
      });
      state.scriptJob = body.id;
      renderJob(els.scriptJob, body);
      updateButtons();
      poll(body.id, els.scriptJob, (r) => {
        els.script.value = r.script;
        els.title.value = r.title;
        els.desc.value = r.description;
        els.tags.value = r.hashtags.join(" ");
        els.onScreen.hidden = !r.on_screen;
        els.onScreen.innerHTML = `<b>${r.watched_clip ? "Gemini saw" : "Gemini assumed"}:</b> ${esc(r.on_screen)}` +
          (titles.length ? ` <span class="hint">· used ${titles.length} trending titles</span>` : "");
        els.scriptJob.innerHTML = `<div class="dl-meta">Written by ${esc(r.model)} · ${r.word_count} words ≈ ${Math.round(r.estimated_seconds)}s · edit anything below</div>`;
        scriptStats();
        saveDraft();
      }, () => {
        state.scriptJob = null;
        updateButtons();
      });
    } catch (err) {
      showError(`Could not start Gemini: ${err.message}`);
    }
  }

  async function renderShort() {
    showError("");
    const tags = els.tags.value.split(/[\s,]+/).filter(Boolean).map((t) => (t.startsWith("#") ? t : `#${t}`));
    try {
      const { body } = await api("/api/create/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clip: state.clip,
          game: els.game.value.trim() || (selectedClip() || {}).game || "Gameplay",
          script: els.script.value.trim(),
          title: els.title.value,
          description: els.desc.value,
          hashtags: tags,
          voice: els.voice.value || null,
          rate: els.rate.value,
          highlight: els.highlight.value,
          fit: els.fit.value,
          max_words: Number(els.words.value),
        }),
      });
      state.renderJob = body.id;
      renderJob(els.renderJob, body);
      updateButtons();
      poll(body.id, els.renderJob, async (short) => {
        els.renderJob.innerHTML = `<div class="dl-meta">Done: <b>${esc(short.title)}</b> (${Math.round(short.duration_seconds)}s) · see Your Shorts below</div>`;
        await loadShorts();
        els.shorts.closest(".card").scrollIntoView({ behavior: "smooth", block: "start" });
      }, () => {
        state.renderJob = null;
        updateButtons();
      });
    } catch (err) {
      showError(`Could not start rendering: ${err.message}`);
    }
  }

  // ---- shorts list ---------------------------------------------------------------------

  async function loadShorts() {
    try {
      state.shorts = (await api("/api/shorts")).body;
    } catch (_) {
      state.shorts = [];
    }
    renderShorts();
  }

  function renderShorts() {
    if (!state.shorts.length) {
      els.shorts.innerHTML = '<div class="empty">No Shorts yet. Pick a clip, write a script and press <b>Create Short</b>.</div>';
      return;
    }
    els.shorts.innerHTML = state.shorts.map((s) => {
      const src = `/media/shorts/${encodeURIComponent(s.filename)}`;
      const when = new Date(s.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
      return `
        <div class="short-card">
          <video src="${src}" controls preload="metadata" playsinline></video>
          <div class="dl-body">
            <div class="dl-title">${esc(s.title)}</div>
            <div class="dl-meta">${esc(s.game)} · ${s.duration_seconds ? `${Math.round(s.duration_seconds)}s` : ""} · ${esc(when)}</div>
            <div class="dl-meta tags">${esc(s.hashtags.join(" "))}</div>
            <div class="dl-actions">
              <a href="${src}" download>Download</a>
              <button type="button" class="dl-copy" data-copy-title="${esc(s.filename)}">Copy title</button>
              <button type="button" class="dl-copy" data-copy-desc="${esc(s.filename)}">Copy description</button>
              <button type="button" class="dl-delete" data-delete-short="${esc(s.filename)}">Delete</button>
            </div>
          </div>
        </div>`;
    }).join("");
  }

  async function copy(text, button) {
    try {
      await navigator.clipboard.writeText(text);
      const label = button.textContent;
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = label), 1200);
    } catch (_) {
      window.prompt("Copy:", text);
    }
  }

  async function deleteShort(filename, button) {
    if (!confirm("Delete this Short from your computer?")) return;
    button.disabled = true;
    try {
      await api(`/api/shorts/${encodeURIComponent(filename)}`, { method: "DELETE" });
      state.shorts = state.shorts.filter((s) => s.filename !== filename);
      renderShorts();
    } catch (err) {
      button.disabled = false;
      showError(`Could not delete: ${err.message}`);
    }
  }

  // ---- setup -----------------------------------------------------------------------------

  async function loadStatus() {
    try {
      state.status = (await api("/api/create/status")).body;
    } catch (err) {
      showError(err.message);
      return;
    }
    const s = state.status;
    els.banner.hidden = s.gemini_enabled;
    els.banner.innerHTML = s.gemini_enabled ? "" :
      'Gemini is not connected yet. Create a free key at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener"><b>Google AI Studio</b></a>, ' +
      'add <code>GEMINI_API_KEY=your-key</code> to <code>.env</code>, then <button type="button" class="btn-link" id="cRecheck">check again</button>. ' +
      "You can still type a script yourself and create a Short.";
    els.geminiInfo.textContent = s.gemini_enabled
      ? `Gemini (${s.gemini_model}) watches the clip and writes the voiceover, title and hashtags`
      : "Gemini needs GEMINI_API_KEY in .env";
    updateButtons();
  }

  async function loadVoices(preferred) {
    try {
      const voices = (await api("/api/create/voices")).body;
      const groups = {};
      for (const v of voices) (groups[v.locale] = groups[v.locale] || []).push(v);
      els.voice.innerHTML = "";
      for (const [locale, list] of Object.entries(groups)) {
        const og = document.createElement("optgroup");
        og.label = locale;
        for (const v of list) og.append(new Option(v.label, v.name));
        els.voice.append(og);
      }
      const want = preferred || (state.status && state.status.voice);
      if (want && voices.some((v) => v.name === want)) els.voice.value = want;
    } catch (err) {
      els.voice.innerHTML = `<option value="">Default voice (list unavailable)</option>`;
    }
  }

  function bind() {
    els.picker.addEventListener("click", (e) => {
      const goto = e.target.closest("[data-goto]");
      if (goto) {
        e.preventDefault();
        T.showView(goto.dataset.goto);
        return;
      }
      const card = e.target.closest("[data-clip]");
      if (card) selectClip(card.dataset.clip);
    });
    els.picker.addEventListener("mouseover", (e) => {
      const v = e.target.closest(".clip-card")?.querySelector("video");
      if (v) v.play().catch(() => {});
    });
    els.picker.addEventListener("mouseout", (e) => {
      const v = e.target.closest(".clip-card")?.querySelector("video");
      if (v && !e.relatedTarget?.closest?.(".clip-card")) v.pause();
    });

    els.write.addEventListener("click", writeScript);
    els.render.addEventListener("click", renderShort);
    els.banner.addEventListener("click", (e) => {
      if (e.target.id === "cRecheck") loadStatus();
    });
    for (const key of DRAFT_FIELDS) {
      els[key].addEventListener("input", () => {
        saveDraft();
        if (key === "script" || key === "length" || key === "rate") scriptStats();
        if (key === "game") updateButtons();
      });
      els[key].addEventListener("change", () => {
        saveDraft();
        if (["fit", "words", "highlight"].includes(key)) updatePreview();
      });
    }
    els.watch.addEventListener("change", saveDraft);

    els.shorts.addEventListener("click", (e) => {
      const s = (attr) => {
        const btn = e.target.closest(`[${attr}]`);
        return btn ? [btn, state.shorts.find((x) => x.filename === btn.getAttribute(attr))] : [null, null];
      };
      let [btn, short] = s("data-copy-title");
      if (short) return copy(short.title, btn);
      [btn, short] = s("data-copy-desc");
      if (short) return copy(`${short.description}\n\n${short.hashtags.join(" ")}`.trim(), btn);
      [btn, short] = s("data-delete-short");
      if (short) deleteShort(short.filename, btn);
    });

    document.addEventListener("trendclip:clips", () => {
      if (state.started && !state.clip && clips().length) selectClip(clips()[0].filename);
      else renderPicker();
    });
    document.addEventListener("trendclip:view", (e) => {
      if (e.detail.view !== "create") {
        els.preview.pause();
        return;
      }
      start();
      if (e.detail.clip) selectClip(e.detail.clip);
      else if (!state.clip && clips().length) selectClip(clips()[0].filename);
      if (!els.preview.hidden) els.preview.play().catch(() => {});
    });
  }

  async function start() {
    if (state.started) return;
    state.started = true;
    const draft = loadDraft();
    renderPicker();
    scriptStats();
    await loadStatus();
    loadVoices(draft.voice);
    loadShorts();
    scriptStats();
  }

  bind();
  if (!$("viewCreate").hidden) {
    // app.js may have opened the Create view (#create) before this script was listening.
    start().then(() => {
      if (!state.clip && clips().length) selectClip(clips()[0].filename);
    });
  }
})();
