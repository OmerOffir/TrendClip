(() => {
  "use strict";

  const GAMES_VISIBLE = 15;
  const NO_GAME = "__none__";

  const $ = (id) => document.getElementById(id);
  const els = {
    region: $("region"),
    category: $("category"),
    maxResults: $("maxResults"),
    top: $("top"),
    refresh: $("refresh"),
    status: $("status"),
    error: $("error"),
    warnings: $("warnings"),
    loading: $("loading"),
    loadingText: $("loadingText"),
    games: $("games"),
    gamesMore: $("gamesMore"),
    gameSort: $("gameSort"),
    topics: $("topics"),
    outliers: $("outliers"),
    videoRows: $("videoRows"),
    search: $("search"),
    gameFilter: $("gameFilter"),
    statTopGame: $("statTopGame"),
    statTopGameSub: $("statTopGameSub"),
    statGames: $("statGames"),
    statGamesSub: $("statGamesSub"),
    statVideos: $("statVideos"),
    statVideosSub: $("statVideosSub"),
    statOutliers: $("statOutliers"),
    srcDialog: $("srcDialog"),
    srcForm: $("srcForm"),
    srcGame: $("srcGame"),
    srcOptions: $("srcOptions"),
    srcSeconds: $("srcSeconds"),
    srcOrientation: $("srcOrientation"),
    srcCancel: $("srcCancel"),
    dlSeconds: $("dlSeconds"),
    dlOrientation: $("dlOrientation"),
    dlLinkForm: $("dlLinkForm"),
    dlUrl: $("dlUrl"),
    dlGame: $("dlGame"),
    dlLinkGo: $("dlLinkGo"),
    dlLinkHint: $("dlLinkHint"),
    downloads: $("downloads"),
    libraryInfo: $("libraryInfo"),
  };

  const state = {
    data: null,
    categoryNames: {},
    sort: { key: "velocity_score", dir: "desc" },
    gameSort: "score",
    showAllGames: false,
    requestSeq: 0,
    library: null, // { counts: {game: n}, total, channels }
    jobs: [],
    clips: null, // null until /api/backgrounds has answered once
    pollTimer: null,
  };

  const DOWNLOAD_ICON =
    '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M5 20h14v-2H5v2zm7-18v10.17l-3.59-3.58L7 10l5 5 5-5-1.41-1.41L13 12.17V2h-2z"/></svg>';

  const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
  const whole = new Intl.NumberFormat("en", { maximumFractionDigits: 0 });
  const pct = new Intl.NumberFormat("en", { style: "percent", maximumFractionDigits: 1 });

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  const plural = (n, word) => `${whole.format(n)} ${word}${n === 1 ? "" : "s"}`;
  const fmtAge = (hours) => (hours < 48 ? `${Math.round(hours)}h` : `${Math.round(hours / 24)}d`);
  const fmtRatio = (r) => (r == null ? "hidden" : r >= 10 ? `${whole.format(r)}x` : `${r.toFixed(1)}x`);
  const fmtDuration = (sec) => {
    if (sec == null || sec === 0) return "";
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = String(sec % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
  };

  function badges(v) {
    const out = [];
    if (v.is_outlier) out.push('<span class="pill hot">OUTLIER</span>');
    if (v.live_status === "live") out.push('<span class="pill live">LIVE</span>');
    if (v.duration_seconds && v.duration_seconds <= 180) out.push('<span class="pill">SHORT</span>');
    return out.join("");
  }

  const miniVideo = (v) => `
    <a class="mini-video" href="${esc(v.url)}" target="_blank" rel="noopener">
      <img src="${esc(v.thumbnail_url)}" alt="" loading="lazy" />
      <div>
        <div class="t">${esc(v.title)}</div>
        <div class="m">${esc(v.channel_title)} · ${compact.format(v.views_per_hour)} views/h · ${compact.format(v.views)} views · ${fmtAge(v.age_hours)} ago</div>
      </div>
    </a>`;

  async function api(path, options) {
    const resp = await fetch(path, options);
    if (!resp.ok) {
      let detail = `${resp.status} ${resp.statusText}`;
      try {
        const body = await resp.json();
        if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      } catch (_) { /* non-JSON error */ }
      throw new Error(detail);
    }
    return { body: await resp.json(), cache: resp.headers.get("X-Cache") };
  }

  function setLoading(on) {
    const n = els.region.value.split(",").length;
    els.loadingText.textContent = n > 1 ? `Fetching trending charts from ${n} regions…` : "Fetching trends from YouTube…";
    els.loading.hidden = !on;
    els.refresh.disabled = on;
  }

  function showError(message) {
    els.error.hidden = !message;
    els.error.textContent = message || "";
  }

  function currentParams() {
    return new URLSearchParams({
      region: els.region.value,
      category: els.category.value,
      max_results: els.maxResults.value,
      top: els.top.value,
    });
  }

  function ensureOption(select, value, label) {
    if (![...select.options].some((o) => o.value === value)) {
      select.add(new Option(label ?? value, value));
    }
    select.value = value;
  }

  async function loadCategories(preferred) {
    const firstRegion = els.region.value.split(",")[0];
    try {
      const { body } = await api(`/api/categories?region=${encodeURIComponent(firstRegion)}`);
      state.categoryNames = Object.fromEntries(body.categories.map((c) => [c.id, c.title]));
      els.category.innerHTML = '<option value="all">All categories</option>';
      for (const c of body.categories) els.category.add(new Option(`${c.title}  ·  ${c.id}`, c.id));
      els.category.value = state.categoryNames[preferred] ? preferred : "all";
    } catch (err) {
      showError(`Could not load categories: ${err.message}`);
      ensureOption(els.category, preferred, `Category ${preferred}`);
    }
  }

  async function loadTrends(refresh = false) {
    const params = currentParams();
    history.replaceState(null, "", `?${params}${location.hash}`);
    if (refresh) params.set("refresh", "true");
    showError("");
    setLoading(true);
    const seq = ++state.requestSeq;
    try {
      const { body, cache } = await api(`/api/trends?${params}`);
      if (seq !== state.requestSeq) return;
      state.data = body;
      state.showAllGames = false;
      render(cache);
      document.dispatchEvent(new CustomEvent("trendclip:trends"));
    } catch (err) {
      if (seq !== state.requestSeq) return;
      showError(err.message);
    } finally {
      if (seq === state.requestSeq) setLoading(false);
    }
  }

  function render(cache) {
    const d = state.data;
    const catLabel = d.category_id ? state.categoryNames[d.category_id] || `Category ${d.category_id}` : "All categories";
    const regionLabel = d.regions.length > 3 ? `${d.regions.length} regions` : d.regions.join(", ");
    const time = new Date(d.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    els.status.innerHTML = `
      <span>${esc(regionLabel)} · ${esc(catLabel)}</span>
      <span class="pill ${cache === "HIT" ? "" : "good"}">${cache === "HIT" ? "cached" : "fresh"} · ${esc(time)}</span>`;

    if (d.warnings.length) {
      els.warnings.hidden = false;
      els.warnings.innerHTML = `<strong>Heads up</strong><ul>${d.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`;
    } else {
      els.warnings.hidden = true;
    }

    const s = d.stats;
    const top = d.games[0];
    els.statTopGame.textContent = top ? top.name : "–";
    els.statTopGame.title = top ? top.name : "";
    els.statTopGameSub.textContent = top ? `${plural(top.video_count, "video")} · ${compact.format(top.views_per_hour)} views/h` : "";
    els.statGames.textContent = whole.format(s.games_detected);
    els.statGamesSub.textContent = s.videos_fetched
      ? `${pct.format(s.videos_with_game / s.videos_fetched)} of videos matched`
      : "";
    els.statVideos.textContent = whole.format(s.videos_fetched);
    els.statVideosSub.textContent = plural(d.regions.length, "region");
    els.statOutliers.textContent = whole.format(s.outlier_videos);

    renderGames();
    renderTopics(d.candidates);
    renderOutliers(d.outlier_videos);
    renderGameFilter();
    renderTable();
  }

  function renderGames() {
    const all = [...state.data.games].sort((a, b) => b[state.gameSort] - a[state.gameSort]);
    if (!all.length) {
      els.games.innerHTML = '<li class="empty">No known games detected. Try the Gaming category or another region.</li>';
      els.gamesMore.hidden = true;
      return;
    }
    const visible = state.showAllGames ? all : all.slice(0, GAMES_VISIBLE);
    const maxShare = Math.max(...all.map((g) => g.view_share), 0.0001);

    els.games.innerHTML = visible
      .map((g, i) => {
        const franchise = g.franchise ? `<span class="chip">${esc(g.franchise)}</span>` : "";
        const regions = g.regions.length > 1 ? ` · ${g.regions.length} regions` : "";
        const outliers = g.outlier_count ? ` · ${plural(g.outlier_count, "outlier")}` : "";
        const thumbs = g.videos.slice(0, 3).map((v) => `<img src="${esc(v.thumbnail_url)}" alt="" loading="lazy" />`).join("");
        return `
          <li class="game">
            <div class="game-head" role="button" tabindex="0" aria-expanded="false">
              <span class="game-rank">${i + 1}</span>
              <div>
                <div class="game-name">${esc(g.name)} ${franchise}</div>
                <div class="game-sub">${plural(g.video_count, "video")}${outliers}${regions} · ${esc(g.channels.slice(0, 3).join(", "))}</div>
              </div>
              <div class="share">
                <div class="share-bar"><i style="width:${Math.max(3, (g.view_share / maxShare) * 100)}%"></i></div>
                <span class="share-label">${pct.format(g.view_share)} of trending watch speed</span>
              </div>
              <div class="metric"><b>${compact.format(g.views_per_hour)}</b><span>views / hour</span></div>
              <div class="metric optional"><b>${compact.format(g.total_views)}</b><span>total views</span></div>
              <div class="thumbs">${thumbs}</div>
              <div>${downloadButton(g.name)}</div>
            </div>
            <div class="game-body">
              <div>
                <h4>Top videos</h4>
                <div class="vlist">${g.videos.map(miniVideo).join("")}</div>
              </div>
              <div>
                <h4>Top channels</h4>
                <ul>${g.channels.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
                <h4 style="margin-top:14px">Momentum score</h4>
                <div>${g.score.toFixed(1)}</div>
                <button class="btn-ghost" type="button" data-show-game="${esc(g.name)}">Show all ${plural(g.video_count, "video")} in table</button>
              </div>
            </div>
          </li>`;
      })
      .join("");

    const hidden = all.length - visible.length;
    els.gamesMore.hidden = all.length <= GAMES_VISIBLE;
    els.gamesMore.textContent = hidden > 0 ? `Show all ${all.length} games (+${hidden})` : "Show top 15 only";
  }

  function downloadButton(game) {
    let hint = "";
    if (state.library) {
      const n = state.library.counts[game] || 0;
      hint = n ? `${whole.format(n)} no-copyright clips` : "via YouTube search";
    }
    const busy = state.jobs.some((j) => j.game === game && (j.status === "queued" || j.status === "running"));
    return `<button class="dl-btn" type="button" data-download="${esc(game)}" ${busy ? "disabled" : ""}>
      <span>${DOWNLOAD_ICON} ${busy ? "Downloading…" : "Get gameplay"}</span>${hint ? `<small>${esc(hint)}</small>` : ""}
    </button>`;
  }

  // ---- background gameplay downloads -------------------------------------------------

  async function loadLibrary() {
    try {
      state.library = (await api("/api/backgrounds/library")).body;
      const names = state.library.channels.map((c) => c.title).join(", ");
      els.libraryInfo.textContent =
        `${whole.format(state.library.total)} no-copyright videos from ${names || "no channels"} · ` +
        "click Get gameplay on any game above for a random clip of it";
      if (state.data) renderGames();
    } catch (err) {
      els.libraryInfo.textContent = `No-Copyright channel list unavailable: ${err.message}`;
    }
  }

  async function loadClips() {
    try {
      state.clips = (await api("/api/backgrounds")).body;
    } catch (_) {
      state.clips = [];
    }
    renderDownloads();
    document.dispatchEvent(new CustomEvent("trendclip:clips"));
  }

  // ---- "Get gameplay" asks where from: random, one of your channels, YouTube search or Pexels ----

  const SOURCE_KEY = "trendclip.gameplaySource";
  let srcGame = null;

  function sourceOption(value, title, sub, { disabled = false, checked = false } = {}) {
    return `<label class="source-option${disabled ? " disabled" : ""}">
      <input type="radio" name="src" value="${esc(value)}" ${disabled ? "disabled" : ""} ${checked ? "checked" : ""} />
      <span><b>${title}</b><small>${sub}</small></span>
    </label>`;
  }

  async function openSourceDialog(game) {
    srcGame = game;
    els.srcGame.textContent = game;
    els.srcSeconds.innerHTML = els.dlSeconds.innerHTML;
    els.srcSeconds.value = els.dlSeconds.value;
    els.srcOrientation.innerHTML = els.dlOrientation.innerHTML;
    els.srcOrientation.value = els.dlOrientation.value;
    els.srcOptions.innerHTML = '<div class="hint">Loading sources…</div>';
    els.srcDialog.showModal();
    let opts;
    try {
      opts = (await api(`/api/backgrounds/options?game=${encodeURIComponent(game)}`)).body;
    } catch (err) {
      opts = { channels: [], total: 0, pexels: false, error: err.message };
    }
    if (srcGame !== game) return;
    const last = localStorage.getItem(SOURCE_KEY) || "random";
    const has = (v) => v === "random" || v === "search" || (v === "pexels" && opts.pexels) ||
      opts.channels.some((c) => c.id === v && c.videos);
    const pick = has(last) ? last : "random";
    const withGame = opts.channels.filter((c) => c.videos);
    const rows = [
      sourceOption("random", "🎲 Random",
        opts.total
          ? `Any of your ${withGame.length} channel${withGame.length === 1 ? "" : "s"} with ${game} · ${whole.format(opts.total)} videos`
          : "None of your channels has it, so YouTube is searched for no-copyright videos",
        { checked: pick === "random" }),
      ...opts.channels.map((c) => sourceOption(c.id, esc(c.title),
        c.videos
          ? `${whole.format(c.videos)} ${esc(game)} video${c.videos === 1 ? "" : "s"}${c.vertical ? ` · ${c.vertical} vertical` : ""}${c.added ? " · added by you" : ""}`
          : `No ${esc(game)} videos on this channel`,
        { disabled: !c.videos, checked: pick === c.id })),
      sourceOption("search", "🔎 Search YouTube…", "Pick a video yourself from results that say no copyright",
        { checked: pick === "search" }),
      sourceOption("pexels", "Pexels stock footage",
        opts.pexels ? "Free stock videos (mostly generic, few game-specific)" : "Add PEXELS_API_KEY to .env to use it",
        { disabled: !opts.pexels, checked: pick === "pexels" }),
    ];
    els.srcOptions.innerHTML = (opts.error ? `<div class="banner banner-warn">Channel list unavailable: ${esc(opts.error)}</div>` : "") + rows.join("");
  }

  async function submitSourceDialog(e) {
    e.preventDefault();
    const choice = (els.srcForm.querySelector('input[name="src"]:checked') || {}).value;
    if (!choice || !srcGame) return;
    localStorage.setItem(SOURCE_KEY, choice);
    els.dlSeconds.value = els.srcSeconds.value;
    els.dlOrientation.value = els.srcOrientation.value;
    els.srcDialog.close();
    const game = srcGame;
    srcGame = null;
    if (choice === "search") {
      document.dispatchEvent(new CustomEvent("trendclip:find", { detail: { query: game } }));
      return;
    }
    const channel = choice.startsWith("UC") ? choice : null;
    await startDownload(game, channel ? "youtube" : choice, channel);
  }

  async function startDownload(game, source = "random", channelId = null) {
    try {
      const { body } = await api("/api/backgrounds/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          game,
          source,
          channel_id: channelId,
          seconds: Number(els.dlSeconds.value),
          orientation: els.dlOrientation.value,
        }),
      });
      state.jobs.unshift(body);
      renderDownloads();
      renderGames();
      ensurePolling();
      els.downloads.closest(".card").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      showError(`Download could not start: ${err.message}`);
    }
  }

  const LINK_HINT = els.dlLinkHint.innerHTML;
  const YT_LINK_RE = /^(https?:\/\/)?(www\.|m\.)?(youtube\.com\/(watch\?|shorts\/|live\/|embed\/|@|channel\/|c\/|user\/)|youtu\.be\/)/i;

  async function startLinkDownload(e) {
    e.preventDefault();
    const url = els.dlUrl.value.trim();
    const bad = !YT_LINK_RE.test(url);
    els.dlUrl.classList.toggle("invalid", bad);
    els.dlLinkHint.classList.toggle("over", bad);
    els.dlLinkHint.innerHTML = bad ? "That isn't a YouTube video or channel link (e.g. https://www.youtube.com/@NoCopyrightGameplays)." : LINK_HINT;
    if (bad) return;
    els.dlLinkGo.disabled = true;
    try {
      await submitLink(url, els.dlGame.value.trim());
    } catch (err) {
      showError(`Download could not start: ${err.message}`);
    } finally {
      els.dlLinkGo.disabled = false;
    }
  }

  // Shared with find.js: a clip from one video (or a channel), with the length/orientation chosen above.
  async function submitLink(url, game) {
    const { body } = await api("/api/backgrounds/download-link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        game: game || "",
        seconds: Number(els.dlSeconds.value),
        orientation: els.dlOrientation.value,
      }),
    });
    state.jobs.unshift(body);
    renderDownloads();
    ensurePolling();
    return body;
  }

  const isActive = (j) => j.status === "queued" || j.status === "running";

  function ensurePolling() {
    if (state.pollTimer) return;
    state.pollTimer = setInterval(async () => {
      try {
        const before = new Set(state.jobs.filter(isActive).map((j) => j.id));
        state.jobs = (await api("/api/backgrounds/jobs")).body;
        const finished = state.jobs.some((j) => before.has(j.id) && !isActive(j));
        if (finished) await loadClips();
        else renderDownloads();
        if (finished && state.data) renderGames();
      } catch (_) { /* transient; keep polling */ }
      if (!state.jobs.some(isActive)) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
    }, 1500);
  }

  function renderDownloads() {
    const pending = state.jobs.filter((j) => j.status !== "done");
    const jobCards = pending.map((j) => {
      const failed = j.status === "error";
      const pctDone = j.progress == null ? null : Math.round(j.progress * 100);
      return `
        <div class="dl-item">
          <div class="dl-body">
            <div class="dl-title">${esc(j.game || "From link")}</div>
            <div class="dl-meta">${esc(j.clip_seconds ? `${j.clip_seconds}s` : "full video")} · ${esc(j.orientation)} · ${j.url ? esc(j.url) : j.channel_title ? esc(j.channel_title) : esc(j.sources.join(" → "))}</div>
            ${failed
              ? `<div class="dl-error">${esc(j.error || "Failed")}</div>
                 <div class="dl-actions"><button class="dl-delete" type="button" data-dismiss="${esc(j.id)}">Dismiss</button></div>`
              : `<div class="progress ${pctDone == null ? "indeterminate" : ""}"><i style="width:${pctDone ?? 0}%"></i></div>
                 <div class="dl-meta">${esc(j.message)}${pctDone != null ? ` · ${pctDone}%` : ""}</div>`}
          </div>
        </div>`;
    });

    const clipCards = (state.clips || []).map((c) => {
      const src = `/media/backgrounds/${encodeURIComponent(c.filename)}`;
      const portrait = c.height && c.width && c.height > c.width;
      const start = c.start_seconds != null ? ` · from ${Math.floor(c.start_seconds / 60)}:${String(Math.floor(c.start_seconds % 60)).padStart(2, "0")}` : "";
      const res = c.width && c.height ? `${c.width}×${c.height}` : "";
      const when = new Date(c.downloaded_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
      const author = c.author_url ? `<a href="${esc(c.author_url)}" target="_blank" rel="noopener">${esc(c.author)}</a>` : esc(c.author);
      return `
        <div class="dl-item ${portrait ? "portrait" : ""}">
          <video src="${src}" controls muted preload="metadata"></video>
          <div class="dl-body">
            <div class="dl-title">${esc(c.game)} <span class="pill">${esc(c.source)}</span>${c.duration_seconds ? ` <span class="pill">${esc(fmtLength(c.duration_seconds))}</span>` : ""}</div>
            <div class="dl-meta">${esc(c.title)}</div>
            <div class="dl-meta">${author} · ${esc(res)} · ${c.duration_seconds ? `${Math.round(c.duration_seconds)}s` : ""}${esc(start)} · ${esc(when)}</div>
            <div class="dl-meta">${esc(c.license_note)}</div>
            <div class="dl-actions">
              <a href="${src}" download>Download file</a>
              <a href="${esc(c.source_url)}" target="_blank" rel="noopener">Source</a>
              <button class="dl-create" type="button" data-create="${esc(c.filename)}">Create Short</button>
              <button class="dl-delete" type="button" data-delete="${esc(c.filename)}">Delete</button>
            </div>
          </div>
        </div>`;
    });

    els.downloads.innerHTML =
      jobCards.join("") + clipCards.join("") ||
      '<div class="empty">No clips yet. Click <b>Get gameplay</b> on a game above to download a random no-copyright clip.</div>';
  }

  function fmtLength(seconds) {
    const s = Math.round(seconds);
    return s < 60 ? `${s}s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} min`;
  }

  async function deleteClip(filename, button) {
    if (!confirm("Delete this clip from your computer?")) return;
    button.disabled = true;
    try {
      await api(`/api/backgrounds/${encodeURIComponent(filename)}`, { method: "DELETE" });
      state.clips = (state.clips || []).filter((c) => c.filename !== filename);
      renderDownloads();
      document.dispatchEvent(new CustomEvent("trendclip:clips"));
    } catch (err) {
      button.disabled = false;
      showError(`Could not delete clip: ${err.message}`);
    }
  }

  async function dismissJob(id, button) {
    button.disabled = true;
    try {
      await api(`/api/backgrounds/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
    } catch (_) { /* already gone after a restart */ }
    state.jobs = state.jobs.filter((j) => j.id !== id);
    renderDownloads();
  }

  function setupDownloadOptions(cfg) {
    const bg = cfg.backgrounds || {};
    ensureOption(els.dlSeconds, String(bg.clip_seconds ?? 60), `${bg.clip_seconds}s`);
    els.dlOrientation.value = bg.orientation || "landscape";
  }

  function renderTopics(topics) {
    if (!topics.length) {
      els.topics.innerHTML = '<li class="empty">Every trending video matched a known game.</li>';
      return;
    }
    const maxScore = Math.max(...topics.map((t) => t.score));
    els.topics.innerHTML = topics
      .map((t, i) => {
        const extra = t.keywords.slice(1).map((k) => `<span class="chip">${esc(k)}</span>`).join("");
        return `
          <li class="topic">
            <div class="topic-head" role="button" tabindex="0" aria-expanded="false">
              <span class="rank">${i + 1}</span>
              <div>
                <div class="topic-name">${esc(t.topic)}</div>
                <div class="topic-meta">${plural(t.video_count, "video")} · ${compact.format(t.total_views)} views · peak ${compact.format(t.max_views_per_hour)}/h</div>
                ${extra ? `<div class="chips">${extra}</div>` : ""}
              </div>
              <div class="score">
                <strong>${t.score.toFixed(1)}</strong>
                <div class="bar"><i style="width:${Math.max(4, (t.score / maxScore) * 100)}%"></i></div>
              </div>
            </div>
            <div class="topic-videos">${t.videos.map(miniVideo).join("")}</div>
          </li>`;
      })
      .join("");
  }

  function renderOutliers(videos) {
    if (!videos.length) {
      els.outliers.innerHTML = '<div class="empty">No outliers in this batch.</div>';
      return;
    }
    els.outliers.innerHTML = videos
      .slice(0, 12)
      .map(
        (v) => `
        <a class="vcard" href="${esc(v.url)}" target="_blank" rel="noopener">
          <div class="thumb">
            <img src="${esc(v.thumbnail_url)}" alt="" loading="lazy" />
            <div class="badges">${v.live_status === "live" ? '<span class="pill live">LIVE</span>' : ""}</div>
            ${fmtDuration(v.duration_seconds) ? `<span class="dur">${fmtDuration(v.duration_seconds)}</span>` : ""}
          </div>
          <div class="vbody">
            ${v.game ? `<span class="game-tag">${esc(v.game)}</span>` : ""}
            <div class="vtitle">${esc(v.title)}</div>
            <div class="vchan">${esc(v.channel_title)} · ${fmtAge(v.age_hours)} ago</div>
            <div class="vstats">
              <span><b>${compact.format(v.views_per_hour)}</b> views/h</span>
              <span><b>${fmtRatio(v.outlier_ratio)}</b> subs</span>
            </div>
          </div>
        </a>`
      )
      .join("");
  }

  function renderGameFilter() {
    const keep = els.gameFilter.value;
    const names = state.data.games.map((g) => g.name).sort((a, b) => a.localeCompare(b));
    els.gameFilter.innerHTML = '<option value="">All games</option>';
    for (const n of names) els.gameFilter.add(new Option(n, n));
    els.gameFilter.add(new Option("No game detected", NO_GAME));
    els.gameFilter.value = [...els.gameFilter.options].some((o) => o.value === keep) ? keep : "";
  }

  function renderTable() {
    if (!state.data) return;
    const q = els.search.value.trim().toLowerCase();
    const game = els.gameFilter.value;
    const { key, dir } = state.sort;
    const rows = state.data.videos
      .filter((v) => !game || (game === NO_GAME ? !v.game : v.game === game))
      .filter((v) => !q || v.title.toLowerCase().includes(q) || v.channel_title.toLowerCase().includes(q))
      .sort((a, b) => {
        const av = a[key] ?? -Infinity;
        const bv = b[key] ?? -Infinity;
        return dir === "asc" ? av - bv : bv - av;
      });

    document.querySelectorAll(".videos th.sortable").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.sort === key);
      th.classList.toggle("asc", th.dataset.sort === key && dir === "asc");
    });

    if (!rows.length) {
      els.videoRows.innerHTML = '<tr><td colspan="7" class="empty">No videos match.</td></tr>';
      return;
    }
    els.videoRows.innerHTML = rows
      .map(
        (v) => `
        <tr>
          <td>
            <a class="vcell" href="${esc(v.url)}" target="_blank" rel="noopener">
              <img src="${esc(v.thumbnail_url)}" alt="" loading="lazy" />
              <div>
                <div class="t">${esc(v.title)}</div>
                <div class="m">${esc(v.channel_title)}${v.channel_subscribers != null ? ` · ${compact.format(v.channel_subscribers)} subs` : ""} ${badges(v)}</div>
              </div>
            </a>
          </td>
          <td>${v.game ? `<span class="game-tag">${esc(v.game)}</span>` : '<span class="game-tag none">—</span>'}</td>
          <td class="num"><strong>${v.velocity_score.toFixed(2)}</strong></td>
          <td class="num">${compact.format(v.views_per_hour)}</td>
          <td class="num">${compact.format(v.views)}</td>
          <td class="num ${v.outlier_ratio == null ? "dim" : ""}">${fmtRatio(v.outlier_ratio)}</td>
          <td class="num">${fmtAge(v.age_hours)}</td>
        </tr>`
      )
      .join("");
  }

  function bindExpandable(list, itemSelector, headSelector) {
    const toggle = (head) => {
      const open = head.closest(itemSelector).classList.toggle("open");
      head.setAttribute("aria-expanded", String(open));
    };
    list.addEventListener("click", (e) => {
      if (e.target.closest("button, a")) return;
      const head = e.target.closest(headSelector);
      if (head) toggle(head);
    });
    list.addEventListener("keydown", (e) => {
      const head = e.target.closest(headSelector);
      if (head && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        toggle(head);
      }
    });
  }

  function bindEvents() {
    els.dlLinkForm.addEventListener("submit", startLinkDownload);
    els.srcForm.addEventListener("submit", submitSourceDialog);
    els.srcCancel.addEventListener("click", () => {
      srcGame = null;
      els.srcDialog.close();
    });
    els.region.addEventListener("change", async () => {
      await loadCategories(els.category.value);
      loadTrends();
    });
    els.category.addEventListener("change", () => loadTrends());
    els.maxResults.addEventListener("change", () => loadTrends());
    els.top.addEventListener("change", () => loadTrends());
    els.refresh.addEventListener("click", () => loadTrends(true));
    els.search.addEventListener("input", renderTable);
    els.gameFilter.addEventListener("change", renderTable);

    els.gameSort.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-sort]");
      if (!btn || !state.data) return;
      state.gameSort = btn.dataset.sort;
      els.gameSort.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      renderGames();
    });
    els.gamesMore.addEventListener("click", () => {
      state.showAllGames = !state.showAllGames;
      renderGames();
    });
    els.downloads.addEventListener("click", (e) => {
      const del = e.target.closest("[data-delete]");
      if (del) deleteClip(del.dataset.delete, del);
      const dismiss = e.target.closest("[data-dismiss]");
      if (dismiss) dismissJob(dismiss.dataset.dismiss, dismiss);
      const create = e.target.closest("[data-create]");
      if (create) window.TrendClip.showView("create", create.dataset.create);
    });

    els.games.addEventListener("click", (e) => {
      const dl = e.target.closest("[data-download]");
      if (dl) {
        e.stopPropagation();
        openSourceDialog(dl.dataset.download);
        return;
      }
      const btn = e.target.closest("[data-show-game]");
      if (!btn) return;
      e.stopPropagation();
      els.gameFilter.value = btn.dataset.showGame;
      els.search.value = "";
      renderTable();
      els.videoRows.closest(".card").scrollIntoView({ behavior: "smooth", block: "start" });
    });

    document.querySelectorAll(".videos th.sortable").forEach((th) =>
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        state.sort = { key, dir: state.sort.key === key && state.sort.dir === "desc" ? "asc" : "desc" };
        renderTable();
      })
    );

    bindExpandable(els.games, ".game", ".game-head");
    bindExpandable(els.topics, ".topic", ".topic-head");
  }

  function populateRegions(cfg) {
    const groups = document.createElement("optgroup");
    groups.label = "Multi-region";
    for (const [name, codes] of Object.entries(cfg.region_groups || {})) groups.append(new Option(name, codes));
    const countries = document.createElement("optgroup");
    countries.label = "Countries";
    for (const [code, name] of Object.entries(cfg.regions)) countries.append(new Option(`${name} (${code})`, code));
    els.region.append(groups, countries);
  }

  async function init() {
    bindEvents();
    let cfg;
    try {
      cfg = (await api("/api/config")).body;
    } catch (err) {
      showError(err.message);
      return;
    }
    const params = new URLSearchParams(location.search);
    const d = cfg.defaults;

    populateRegions(cfg);
    setupDownloadOptions(cfg);
    loadLibrary();
    loadClips();
    api("/api/backgrounds/jobs")
      .then(({ body }) => {
        state.jobs = body;
        renderDownloads();
        if (state.jobs.some(isActive)) ensurePolling();
      })
      .catch(() => {});
    ensureOption(els.region, (params.get("region") || d.region).toUpperCase());
    ensureOption(els.maxResults, params.get("max_results") || String(d.max_results));
    ensureOption(els.top, params.get("top") || String(d.top));

    await loadCategories(params.get("category") || d.category);
    loadTrends();
  }

  // Shared with create.js (Create tab).
  const views = { trends: $("viewTrends"), create: $("viewCreate"), upload: $("viewUpload"), plan: $("viewPlan") };
  const nav = $("nav");

  function showView(name, clip) {
    if (!views[name]) name = "trends";
    for (const [key, el] of Object.entries(views)) el.hidden = key !== name;
    nav.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    if (location.hash !== `#${name}`) history.replaceState(null, "", `${location.pathname}${location.search}#${name}`);
    document.dispatchEvent(new CustomEvent("trendclip:view", { detail: { view: name, clip } }));
    window.scrollTo({ top: 0 });
  }

  nav.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-view]");
    if (btn) showView(btn.dataset.view);
  });

  window.TrendClip = {
    api,
    esc,
    fmtLength,
    showView,
    getData: () => state.data,
    getClips: () => state.clips,
    reloadClips: loadClips,
    submitLink,
    reloadLibrary: () => loadLibrary(),
    getLibrary: () => state.library,
    getTopGames: () => (state.data ? [...state.data.games].sort((a, b) => b.score - a.score).map((g) => g.name) : []),
    fmtDuration,
    compact: (n) => compact.format(n),
  };

  init();
  const startView = location.hash.slice(1);
  if (startView !== "trends" && views[startView]) showView(startView);
})();
