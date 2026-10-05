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
    mode: $("cMode"),
    music: $("cMusic"),
    musicVol: $("cMusicVol"),
    track: $("cTrack"),
    trackTitle: $("cTrackTitle"),
    trackAudio: $("cTrackAudio"),
    trackMeta: $("cTrackMeta"),
    shuffle: $("cShuffle"),
    titleCard: $("cTitleCard"),
    popups: $("cPopups"),
    popWord: $("cPopWord"),
    popEmoji: $("cPopEmoji"),
    popAdd: $("cPopAdd"),
    format: $("cFormat"),
    handle: $("cHandle"),
    handleField: $("cHandleField"),
    series: $("cSeries"),
    story: $("cStory"),
    partTabs: $("cPartTabs"),
    partHint: $("cPartHint"),
    endCard: $("cEndCard"),
    pinned: $("cPinned"),
    pinnedField: $("cPinnedField"),
    reactions: $("cReactions"),
    stickers: $("cStickers"),
    ctaSticker: $("cCtaSticker"),
    stickerSummary: $("cStickerSummary"),
    stickerGrid: $("cStickerGrid"),
    stickerRescan: $("cStickerRescan"),
    stickerRetag: $("cStickerRetag"),
    stickerHint: $("cStickerHint"),
  };
  const MOOD_EMOJI = { funny: "😂", awkward: "😬", shocked: "😱", approve: "👍", reject: "🙅", proud: "🥂", pain: "🙂",
    nope: "🚪", innocent: "🙋", crazy: "🤪", embarrassed: "🤦", suspicious: "🤨" };

  const DRAFT_KEY = "trendclip.createDraft";
  const DRAFT_FIELDS = ["mode", "game", "notes", "script", "title", "desc", "tags", "titleCard", "endCard", "voice", "rate",
    "highlight", "words", "fit", "music", "musicVol", "handle", "story", "pinned"];
  const PART_FIELDS = ["script", "title", "desc", "tags", "titleCard", "endCard"];
  const LENGTHS = {
    short: { def: 30, opts: [[15, "15 seconds"], [20, "20 seconds"], [30, "30 seconds"], [45, "45 seconds"], [60, "60 seconds"]] },
    long: { def: 90, opts: [[60, "~60 s · ~200 words"], [75, "~75 s · ~250 words"], [90, "~90 s · ~300 words"]] },
    multi: { def: 45, opts: [[40, "40 s per part"], [45, "45 s per part"], [50, "50 s per part"]] },
  };
  const HANDLE_RE = /^@[\w.-]{3,30}$/;
  const ASYNC_FIELDS = ["voice", "music"]; // options arrive from the server; applied after loading
  const HIGHLIGHT_CSS = { yellow: "#ffff00", cyan: "#00ffff", green: "#00ff00", orange: "#ffa500" };

  const state = {
    status: null,
    clip: null, // selected clip filename
    shorts: [],
    scriptJob: null,
    renderJob: null,
    started: false,
    track: null, // previewed music track (DownloadedTrack)
    trackHistory: [],
    picking: false,
    popups: [], // [{word, emoji, query}]
    reactions: [], // [{word, mood}] reaction-sticker beats from Gemini
    stickerLib: null,
    format: "short", // short | long | multi
    parts: null, // multi: [{script, title, desc, tags, titleCard, endCard, popups}] x2; the form shows parts[part]
    part: 0,
    seriesId: null,
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
    draft.popups = state.popups;
    draft.reactions = state.reactions;
    draft.stickers = els.stickers.checked;
    draft.ctaSticker = els.ctaSticker.checked;
    draft.length = els.length.value;
    draft.format = state.format;
    if (state.parts) state.parts[state.part] = capturePart();
    draft.parts = state.parts;
    draft.part = state.part;
    draft.seriesId = state.seriesId;
    localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  }

  // ---- formats & parts -------------------------------------------------------------------

  const emptyPart = () => ({ script: "", title: "", desc: "", tags: "", titleCard: "", endCard: "", popups: [], reactions: [] });

  function capturePart() {
    const p = { popups: state.popups, reactions: state.reactions };
    for (const key of PART_FIELDS) p[key] = els[key].value;
    return p;
  }

  function applyPart(p) {
    for (const key of PART_FIELDS) els[key].value = p[key] || "";
    state.popups = Array.isArray(p.popups) ? p.popups : [];
    state.reactions = Array.isArray(p.reactions) ? p.reactions : [];
    renderPopups();
    renderReactions();
  }

  function partFromResult(r) {
    return { script: r.script, title: r.title, desc: r.description, tags: r.hashtags.join(" "),
      titleCard: r.title_card || "", endCard: r.end_card || "", popups: r.popups || [], reactions: r.reactions || [] };
  }

  function renderParts() {
    const multi = state.format === "multi";
    els.series.hidden = !multi;
    els.pinnedField.hidden = !multi || state.part !== 0;
    els.handleField.hidden = !multi;
    if (!multi) return;
    for (const b of els.partTabs.querySelectorAll("[data-part]")) {
      const i = Number(b.dataset.part);
      b.classList.toggle("active", i === state.part);
      const words = (state.parts[i].script || "").trim().split(/\s+/).filter(Boolean).length;
      b.dataset.empty = words < 3 ? "1" : "";
    }
    els.partHint.textContent = state.part === 0
      ? "Part 1 ends on the cliffhanger, then the call to action \"Sub to … for Part 2 dropping tomorrow!\". Title gets (Part 1), hashtags #part1."
      : "Part 2 picks up the cliffhanger and ends with \"Sub for daily side quest stories and drop your crazy stories in the comments!\". It continues the gameplay where Part 1 stopped.";
  }

  function switchPart(i) {
    if (!state.parts || i === state.part) return;
    state.parts[state.part] = capturePart();
    state.part = i;
    applyPart(state.parts[i]);
    renderParts();
    scriptStats();
    saveDraft();
  }

  function buildLengths(wanted) {
    const spec = LENGTHS[state.format];
    els.length.innerHTML = spec.opts.map(([v, label]) => `<option value="${v}">${label}</option>`).join("");
    const ok = spec.opts.some(([v]) => String(v) === String(wanted));
    els.length.value = ok ? String(wanted) : String(spec.def);
  }

  function setFormat(format, wantedLength) {
    if (!LENGTHS[format]) format = "short";
    const changed = format !== state.format;
    if (changed && state.format === "multi" && state.parts) {
      state.parts[state.part] = capturePart();
      state.parts = null; // keep the part on screen as the single script
    }
    state.format = format;
    if (format === "multi" && !state.parts) {
      state.parts = [capturePart(), emptyPart()];
      state.part = 0;
    }
    for (const b of els.format.querySelectorAll("[data-format]")) {
      const on = b.dataset.format === format;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    }
    buildLengths(changed ? wantedLength : wantedLength ?? els.length.value);
    renderParts();
    updateButtons();
    scriptStats();
  }

  function loadDraft() {
    try {
      const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "{}");
      for (const key of DRAFT_FIELDS) {
        if (draft[key] == null || ASYNC_FIELDS.includes(key)) continue;
        const el = els[key];
        el.value = draft[key];
        if (el.tagName === "SELECT" && el.value === "") {
          el.selectedIndex = Math.max(0, [...el.options].findIndex((o) => o.defaultSelected));
        }
      }
      if (draft.watch != null) els.watch.checked = draft.watch;
      if (Array.isArray(draft.popups)) state.popups = draft.popups;
      if (Array.isArray(draft.reactions)) state.reactions = draft.reactions;
      if (draft.stickers != null) els.stickers.checked = draft.stickers;
      if (draft.ctaSticker != null) els.ctaSticker.checked = draft.ctaSticker;
      state.clip = draft.clip || null;
      if (draft.format === "multi" && Array.isArray(draft.parts) && draft.parts.length === 2) {
        state.parts = draft.parts;
        state.part = draft.part === 1 ? 1 : 0;
        state.seriesId = draft.seriesId || null;
      }
      state.format = "";
      setFormat(draft.format || "short", draft.length);
      return draft;
    } catch (_) {
      setFormat("short");
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

  const wordCount = (text) => (text || "").trim().split(/\s+/).filter(Boolean).length;

  function speechSeconds(words) {
    const wps = (state.status && state.status.words_per_second) || 3.3;
    const rate = 1 + parseInt(els.rate.value, 10) / 100 - 0.05;
    return words / (wps * rate);
  }

  function scriptStats() {
    const words = wordCount(els.script.value);
    const secs = speechSeconds(words);
    const target = Number(els.length.value);
    let text = words ? `· ${words} words ≈ ${Math.round(secs)}s` : "";
    // The background loops when it is shorter than the voice (parts play back to back through the clip).
    const clip = selectedClip();
    const total = state.parts
      ? state.parts.reduce((sum, p, i) => sum + speechSeconds(wordCount(i === state.part ? els.script.value : p.script)), 0)
      : secs;
    if (words && clip && clip.duration_seconds && total > clip.duration_seconds + 1) {
      text += ` · clip is ${Math.round(clip.duration_seconds)}s, gameplay will loop`;
    }
    els.scriptStats.textContent = text;
    els.scriptStats.classList.toggle("over", words > 0 && secs > target + 8);
    if (state.parts) renderParts();
    updateButtons();
  }

  function updateButtons() {
    const busyScript = state.scriptJob != null;
    const busyRender = state.renderJob != null;
    const hasClip = !!selectedClip();
    const gemini = state.status && state.status.gemini_enabled;
    const multi = state.format === "multi";
    const story = multi || els.mode.value === "story";
    const long = state.format === "long";
    const badHandle = multi && els.handle.value.trim() && !HANDLE_RE.test(els.handle.value.trim());
    els.mode.disabled = multi;
    els.mode.title = multi ? "Multi-part is always a story" : "";
    els.handle.classList.toggle("invalid", !!badHandle);
    els.write.disabled = busyScript || !hasClip || !els.game.value.trim() || !gemini || badHandle;
    els.write.title = !gemini ? "Add GEMINI_API_KEY to .env first" : !hasClip ? "Pick a clip first"
      : badHandle ? "Channel must look like @YourChannel" : "";
    els.write.textContent = busyScript ? "Writing…"
      : multi ? "Write Part 1 + Part 2 with Gemini"
      : story ? `Write a ${long ? "long " : ""}random story with Gemini`
      : `Write ${long ? "a long " : ""}script with Gemini`;
    els.watch.disabled = story;
    els.watch.closest(".check").classList.toggle("disabled", story);
    els.watch.closest(".check").title = story ? "Not needed: the story is not about the clip" : "";
    const scripts = multi && state.parts
      ? state.parts.map((p, i) => (i === state.part ? els.script.value : p.script))
      : [els.script.value];
    els.render.disabled = busyRender || state.picking || !hasClip || scripts.some((s) => wordCount(s) < 3);
    els.render.title = multi && scripts.some((s) => wordCount(s) < 3) ? "Both parts need a script" : "";
    els.render.textContent = busyRender ? "Creating…" : multi ? "Create Part 1 + Part 2" : long ? "Create long Short" : "Create Short";
  }

  // ---- pop-up images -------------------------------------------------------------------

  const norm = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, "");

  function inScript(word, script = els.script.value) {
    const first = norm(word.split(/\s+/)[0] || "");
    if (!first) return false;
    return script.split(/\s+/).some((t) => {
      const n = norm(t);
      const [a, b] = n.length > first.length ? [n, first] : [first, n];
      return n === first || (b.length >= 3 && a.startsWith(b) && a.length - b.length <= 2);
    });
  }

  function renderPopups() {
    els.popups.innerHTML = state.popups.length
      ? state.popups.map((p, i) => {
          const ok = inScript(p.word);
          return `<span class="chip${ok ? "" : " missing"}" title="${ok ? esc(p.query || p.word) : "This word is not in the script, so it will not pop up"}">
            ${esc(p.emoji || "🖼️")} ${esc(p.word)}<button type="button" data-pop-remove="${i}" aria-label="Remove">×</button></span>`;
        }).join("")
      : `<span class="hint">None. Gemini suggests some with the script, or add your own.</span>`;
  }

  function renderReactions() {
    const on = els.stickers.checked;
    els.reactions.classList.toggle("disabled", !on);
    els.reactions.innerHTML = state.reactions.length
      ? state.reactions.map((r, i) => {
          const ok = inScript(r.word);
          return `<span class="chip${ok ? "" : " missing"}" title="${ok ? esc(r.mood) : "This word is not in the script"}">
            ${MOOD_EMOJI[r.mood] || "✨"} ${esc(r.mood)} · ${esc(r.word)}<button type="button" data-reaction-remove="${i}" aria-label="Remove">×</button></span>`;
        }).join("")
      : `<span class="hint">${on ? "No beats from Gemini: cues in the voiceover (\"oh no\", \"awkward\", \"silence\"…) are used." : "Off."}</span>`;
  }

  function renderStickerLib() {
    const lib = state.stickerLib;
    if (!lib) return;
    const list = lib.stickers.filter((s) => s.category !== "off");
    const counts = Object.entries(lib.counts).filter(([c]) => c !== "off").map(([c, n]) => `${n} ${c}`).join(", ");
    els.stickerSummary.textContent = list.length ? `Sticker library · ${counts}` : "Sticker library · empty";
    els.stickers.closest(".check").title = list.length ? "" : `Add PNG / GIF stickers to ${lib.folder}`;
    els.stickerGrid.innerHTML = list.length
      ? list.map((s) => `
          <figure class="sticker" title="${esc(s.description || s.filename)}">
            <img src="/media/stickers/${encodeURIComponent(s.filename)}" alt="" loading="lazy" />
            <figcaption><b>${esc(s.category === "cta" ? `cta · ${s.cta || "subscribe"}` : s.category)}</b>
              ${esc((s.moods || []).map((m) => `${MOOD_EMOJI[m] || ""}${m}`).join(" "))}
              ${s.animated ? '<span class="pill">GIF</span>' : ""}</figcaption>
          </figure>`).join("")
      : `<div class="empty">No stickers. Put PNG / GIF files in <code>${esc(lib.folder)}</code> and press Rescan.</div>`;
    const untagged = list.filter((s) => s.source === "filename").length;
    els.stickerHint.textContent = untagged
      ? `${untagged} tagged from file names only (Gemini was unavailable); press Re-tag to try again.`
      : "Tags: Gemini looked at each image. Fix any in stickers/stickers.json.";
  }

  async function loadStickers(path = "/api/stickers", method = "GET", button = null) {
    if (button) { button.disabled = true; els.stickerHint.textContent = "Scanning… (Gemini looks at new images)"; }
    try {
      state.stickerLib = (await api(path, { method })).body;
      renderStickerLib();
    } catch (err) {
      els.stickerHint.textContent = `Could not load stickers: ${err.message}`;
    } finally {
      if (button) button.disabled = false;
    }
  }

  function addPopup() {
    const word = els.popWord.value.trim();
    if (!word) return;
    state.popups.push({ word, emoji: els.popEmoji.value.trim(), query: word });
    els.popWord.value = "";
    els.popEmoji.value = "";
    renderPopups();
    saveDraft();
  }

  // ---- background music ----------------------------------------------------------------

  async function loadMusic(preferred) {
    try {
      const { body } = await api("/api/create/music");
      for (const s of body.sources) {
        const label = `${s.title}${s.mood ? ` · ${s.mood}` : ""} (${s.tracks} tracks)`;
        els.music.append(new Option(label, s.id));
      }
    } catch (err) {
      els.music.append(new Option(`Music list unavailable (${err.message})`, "none"));
    }
    if (preferred && [...els.music.options].some((o) => o.value === preferred)) els.music.value = preferred;
    if (els.music.value !== "none") pickTrack();
  }

  function renderTrack() {
    const t = state.track;
    els.track.hidden = els.music.value === "none" || (!t && !state.picking);
    if (state.picking) {
      els.trackTitle.textContent = "Picking a random track…";
      els.trackMeta.textContent = "";
      els.trackAudio.hidden = true;
      return;
    }
    if (!t) return;
    els.trackTitle.textContent = t.title;
    els.trackTitle.title = t.title;
    els.trackAudio.hidden = false;
    const src = `/media/music/${encodeURIComponent(t.filename)}`;
    if (els.trackAudio.getAttribute("src") !== src) els.trackAudio.src = src;
    els.trackAudio.volume = 0.6;
    const len = t.duration_seconds ? ` · ${T.fmtLength(t.duration_seconds)}` : "";
    els.trackMeta.innerHTML = `${esc(t.channel_title)}${esc(len)} · <a href="${esc(t.url)}" target="_blank" rel="noopener">source</a> · credit is added to the description`;
  }

  async function pickTrack() {
    if (els.music.value === "none") {
      state.track = null;
      els.trackAudio.pause();
      renderTrack();
      return;
    }
    state.picking = true;
    renderTrack();
    updateButtons();
    try {
      const { body } = await api("/api/create/music/pick", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: els.music.value, exclude: state.trackHistory.slice(-30) }),
      });
      state.track = body;
      state.trackHistory.push(body.video_id);
    } catch (err) {
      state.track = null;
      showError(`Could not get music: ${err.message}`);
    } finally {
      state.picking = false;
      renderTrack();
      updateButtons();
    }
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
          mode: els.mode.value,
          format: state.format,
          channel_handle: HANDLE_RE.test(els.handle.value.trim()) ? els.handle.value.trim() : null,
        }),
      });
      state.scriptJob = body.id;
      renderJob(els.scriptJob, body);
      updateButtons();
      poll(body.id, els.scriptJob, (r) => {
        if (r.format === "multi") return applySeries(r);
        els.script.value = r.script;
        els.title.value = r.title;
        els.desc.value = r.description;
        els.tags.value = r.hashtags.join(" ");
        els.titleCard.value = r.title_card || "";
        state.popups = r.popups || [];
        state.reactions = r.reactions || [];
        renderPopups();
        renderReactions();
        els.onScreen.hidden = !r.on_screen;
        const label = r.mode === "story" ? "Story" : r.watched_clip ? "Gemini saw" : "Gemini assumed";
        els.onScreen.innerHTML = `<b>${label}:</b> ${esc(r.on_screen)}` +
          (titles.length && r.mode !== "story" ? ` <span class="hint">· used ${titles.length} trending titles</span>` : "");
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

  function applySeries(r) {
    state.parts = r.parts.map(partFromResult);
    state.part = 0;
    state.seriesId = r.series_id;
    applyPart(state.parts[0]);
    els.story.value = r.story_name;
    els.pinned.value = r.pinned_comment;
    if (!els.handle.value.trim()) els.handle.value = r.handle;
    const p1 = r.parts[0];
    els.onScreen.hidden = !p1.on_screen;
    els.onScreen.innerHTML = `<b>Story:</b> ${esc(p1.on_screen)}`;
    const stat = (p) => `Part ${p.part}: ${p.word_count} words ≈ ${Math.round(p.estimated_seconds)}s`;
    els.scriptJob.innerHTML = `<div class="dl-meta">Written by ${esc(r.model)} · “${esc(r.story_name)}” · ${r.parts.map(stat).join(" · ")} · switch parts with the tabs</div>`;
    renderParts();
    scriptStats();
    saveDraft();
  }

  const hashtags = (text) => text.split(/[\s,]+/).filter(Boolean).map((t) => (t.startsWith("#") ? t : `#${t}`));

  function styleBody() {
    return {
      clip: state.clip,
      game: els.game.value.trim() || (selectedClip() || {}).game || "Gameplay",
      voice: els.voice.value || null,
      rate: els.rate.value,
      highlight: els.highlight.value,
      fit: els.fit.value,
      max_words: Number(els.words.value),
      music_source: els.music.value,
      music_track: state.track ? state.track.video_id : null,
      music_volume: Number(els.musicVol.value),
      stickers: els.stickers.checked,
      cta_sticker: els.ctaSticker.checked,
    };
  }

  function seriesBody() {
    state.parts[state.part] = capturePart();
    const handle = els.handle.value.trim();
    return {
      ...styleBody(),
      story_name: els.story.value.trim() || "Story",
      series_id: state.seriesId,
      pinned_comment: els.pinned.value.trim(),
      channel_handle: HANDLE_RE.test(handle) ? handle : "",
      parts: state.parts.map((p) => ({
        script: p.script.trim(),
        title: p.title,
        description: p.desc,
        hashtags: hashtags(p.tags),
        title_card: p.titleCard.trim(),
        end_card: p.endCard.trim(),
        popups: (p.popups || []).filter((x) => inScript(x.word, p.script)),
        reactions: (p.reactions || []).filter((x) => inScript(x.word, p.script)),
      })),
    };
  }

  async function renderShort() {
    showError("");
    const multi = state.format === "multi" && state.parts;
    try {
      const { body } = await api(multi ? "/api/create/render-series" : "/api/create/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(multi ? seriesBody() : {
          ...styleBody(),
          script: els.script.value.trim(),
          title: els.title.value,
          description: els.desc.value,
          hashtags: hashtags(els.tags.value),
          title_card: els.titleCard.value.trim(),
          end_card: els.endCard.value.trim(),
          popups: state.popups.filter((p) => inScript(p.word)),
          reactions: state.reactions.filter((r) => inScript(r.word)),
        }),
      });
      state.renderJob = body.id;
      renderJob(els.renderJob, body);
      updateButtons();
      poll(body.id, els.renderJob, async (result) => {
        const made = result.shorts || [result];
        els.renderJob.innerHTML = `<div class="dl-meta">Done: ${made.map((s) =>
          `<b>${esc(s.filename)}</b> (${Math.round(s.duration_seconds)}s)`).join(" + ")} · see Your Shorts below</div>`;
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
        <div class="short-card${s.ready ? " is-ready" : ""}">
          <video src="${src}" controls preload="metadata" playsinline></video>
          <div class="dl-body">
            <div class="dl-title">${s.ready ? '<span class="pill ready">READY</span> ' : ""}${s.part ? `<span class="pill part">PART ${s.part}/${s.parts_total || 2}</span> ` : ""}${esc(s.title)}</div>
            ${s.part ? `<div class="dl-meta">📁 ${esc(s.filename)}</div>` : ""}
            <div class="dl-meta">${esc(s.game)} · ${s.duration_seconds ? `${Math.round(s.duration_seconds)}s` : ""} · ${esc(when)}</div>
            <div class="dl-meta tags">${esc(s.hashtags.join(" "))}</div>
            ${s.music_title ? `<div class="dl-meta">♪ <a href="${esc(s.music_url)}" target="_blank" rel="noopener">${esc(s.music_title)}</a></div>` : ""}
            ${s.popups && s.popups.length ? `<div class="dl-meta">Pop-ups: ${esc(s.popups.join(", "))}</div>` : ""}
            ${s.stickers && s.stickers.length ? `<div class="dl-meta">Stickers: ${esc(s.stickers.join(", "))}</div>` : ""}
            <div class="dl-actions">
              <button type="button" class="${s.ready ? "dl-copy" : "dl-ready"}" data-ready-short="${esc(s.filename)}">${s.ready ? "Not ready" : "Ready to upload"}</button>
              ${s.ready ? `<button type="button" class="dl-copy" data-goto="upload">Open Upload tab</button>` : ""}
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

  async function toggleReady(short, button) {
    button.disabled = true;
    try {
      const { body } = await api(`/api/shorts/${encodeURIComponent(short.filename)}/ready`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ready: !short.ready }),
      });
      Object.assign(short, body);
      renderShorts();
      document.dispatchEvent(new CustomEvent("trendclip:ready"));
    } catch (err) {
      button.disabled = false;
      showError(`Could not update: ${err.message}`);
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
    els.handle.placeholder = s.channel_handle || "@YourChannel";
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
        if (key === "script") { renderPopups(); renderReactions(); }
        if (key === "game") updateButtons();
      });
      els[key].addEventListener("change", () => {
        saveDraft();
        if (["fit", "words", "highlight"].includes(key)) updatePreview();
        if (key === "mode") updateButtons();
        if (key === "music") pickTrack();
      });
    }
    els.length.addEventListener("change", () => { saveDraft(); scriptStats(); });
    els.handle.addEventListener("input", updateButtons);
    els.format.addEventListener("click", (e) => {
      const b = e.target.closest("[data-format]");
      if (!b) return;
      setFormat(b.dataset.format);
      saveDraft();
    });
    els.partTabs.addEventListener("click", (e) => {
      const b = e.target.closest("[data-part]");
      if (b) switchPart(Number(b.dataset.part));
    });
    els.shuffle.addEventListener("click", pickTrack);
    els.popAdd.addEventListener("click", addPopup);
    for (const input of [els.popWord, els.popEmoji]) {
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); addPopup(); }
      });
    }
    els.popups.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-pop-remove]");
      if (!btn) return;
      state.popups.splice(Number(btn.dataset.popRemove), 1);
      renderPopups();
      saveDraft();
    });
    renderPopups();
    els.reactions.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-reaction-remove]");
      if (!btn) return;
      state.reactions.splice(Number(btn.dataset.reactionRemove), 1);
      renderReactions();
      saveDraft();
    });
    for (const box of [els.stickers, els.ctaSticker]) {
      box.addEventListener("change", () => { renderReactions(); saveDraft(); });
    }
    els.stickerRescan.addEventListener("click", () => loadStickers("/api/stickers/rescan", "POST", els.stickerRescan));
    els.stickerRetag.addEventListener("click", () => {
      if (confirm("Ask Gemini to look at every sticker again? Your stickers.json fixes are kept.")) {
        loadStickers("/api/stickers/rescan?retag=true", "POST", els.stickerRetag);
      }
    });
    els.watch.addEventListener("change", saveDraft);

    els.shorts.addEventListener("click", (e) => {
      const goto = e.target.closest("[data-goto]");
      if (goto) return T.showView(goto.dataset.goto);
      const s = (attr) => {
        const btn = e.target.closest(`[${attr}]`);
        return btn ? [btn, state.shorts.find((x) => x.filename === btn.getAttribute(attr))] : [null, null];
      };
      let [btn, short] = s("data-copy-title");
      if (short) return copy(short.title, btn);
      [btn, short] = s("data-copy-desc");
      if (short) return copy(`${short.description}\n\n${short.hashtags.join(" ")}`.trim(), btn);
      [btn, short] = s("data-ready-short");
      if (short) return toggleReady(short, btn);
      [btn, short] = s("data-delete-short");
      if (short) deleteShort(short.filename, btn);
    });

    document.addEventListener("trendclip:shorts-changed", loadShorts);
    document.addEventListener("trendclip:clips", () => {
      if (state.started && !state.clip && clips().length) selectClip(clips()[0].filename);
      else renderPicker();
    });
    document.addEventListener("trendclip:view", (e) => {
      if (e.detail.view !== "create") {
        els.preview.pause();
        els.trackAudio.pause();
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
    renderPopups();
    renderReactions();
    renderPicker();
    loadStickers();
    scriptStats();
    await loadStatus();
    loadVoices(draft.voice);
    loadMusic(draft.music);
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
