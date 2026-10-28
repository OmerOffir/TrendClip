// Dark Outliers tab: the story bank (Reddit / Wikipedia / Gemini), reading, status, fetching, scripts and videos.
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const els = {
    error: $("daError"), sources: $("daSources"), list: $("daList"), detail: $("daDetail"), shown: $("daShown"),
    source: $("daSource"), status: $("daStatus"), pillar: $("daPillar"), query: $("daQuery"), fetch: $("daFetch"),
    fetchStatus: $("daFetchStatus"), rank: $("daRank"), navCount: $("daNavCount"), mix: $("daMix"),
    n: { reddit: $("daNReddit"), wikipedia: $("daNWiki"), gemini: $("daNGemini"), rss: $("daNRss") },
  };
  const SOURCES = { reddit: "Reddit", wikipedia: "Wikipedia", gemini: "Gemini", rss: "RSS feeds" };
  const PILLARS = { mystery: "🕵️ Mystery", history: "🏚️ Creepy history", outrage: "⚠️ Disturbing" };
  const pillarPill = (p) => (p ? `<span class="pill da-pillar-${p}">${PILLARS[p] || p}</span>` : "");
  const STATUSES = ["NEW", "PROCESSING", "SCRIPT_READY", "USED", "REJECTED"];
  const LABELS = { NEW: "Back to new", PROCESSING: "Processing", SCRIPT_READY: "Script ready", USED: "Used", REJECTED: "Rejected" };
  const state = { selected: null, nextId: null, videos: [], loaded: false, polling: null, searchTimer: null };

  const family = (s) => s.source_id.split("_")[0];
  const showError = (msg) => { els.error.textContent = msg; els.error.hidden = !msg; };

  function renderSources(sources, counts) {
    els.sources.innerHTML = Object.entries(SOURCES).map(([key, label]) => {
      const c = sources[key] || {};
      const total = Object.values(c).reduce((a, b) => a + b, 0);
      const rest = STATUSES.slice(1).filter((s) => c[s]).map((s) => `${c[s]} ${s.toLowerCase()}`).join(" · ");
      return `<div class="da-source da-${key}"><b>${label}</b><span class="da-big">${total}</span>
        <small>${c.NEW || 0} new${rest ? ` · ${rest}` : ""}</small></div>`;
    }).join("");
    const fresh = counts.NEW || 0;
    els.navCount.textContent = fresh;
    els.navCount.hidden = !fresh;
  }

  function renderMix(mix, labels, nextPillar) {
    const made = Object.values(mix).reduce((a, m) => a + m.produced, 0);
    els.mix.innerHTML = `<b>Video mix</b> <span class="hint">(scripted + used stories vs. the target)</span>` +
      Object.entries(mix).map(([p, m]) => {
        const share = made ? Math.round((100 * m.produced) / made) : 0;
        return `<div class="da-mix-row${p === nextPillar ? " next" : ""}" title="${esc(labels[p] || p)}">
          <span>${PILLARS[p] || p}</span>
          <span class="da-mix-bar"><i style="width:${share}%"></i><s style="left:${Math.round(m.target * 100)}%"></s></span>
          <small>${m.produced} made (${share}% / target ${Math.round(m.target * 100)}%) · ${m.new} new${p === nextPillar ? " · <b>next</b>" : ""}</small>
        </div>`;
      }).join("");
  }

  function renderList(stories) {
    els.shown.textContent = `· ${stories.length} shown`;
    if (!stories.length) {
      els.list.innerHTML = '<div class="empty">No stories here. Press <b>Fetch new stories</b>.</div>';
      return;
    }
    els.list.innerHTML = stories.map((s) => `
      <button type="button" class="da-item${s.id === state.selected ? " active" : ""}" data-id="${s.id}">
        <span class="da-score" title="Score 0-100">${s.score}</span>
        <span class="da-item-body">
          <b>${esc(s.title || "(no title)")}</b>
          <small>${s.id === state.nextId ? '<span class="pill good">NEXT</span> ' : ""}${pillarPill(s.pillar)} <span class="pill da-pill-${family(s)}">${esc(s.source_name)}</span>
            ${s.status !== "NEW" ? `<span class="pill">${s.status}</span>` : ""}${state.videos.includes(s.id) ? ' <span class="pill good">🎬 video</span>' : ""} ${s.chars.toLocaleString()} chars</small>
          ${s.note ? `<span class="da-note">${esc(s.note)}</span>` : ""}
          <span class="da-preview">${esc(s.preview)}</span>
        </span>
      </button>`).join("");
  }

  async function load() {
    const params = new URLSearchParams();
    if (els.source.value) params.set("source", els.source.value);
    if (els.status.value) params.set("status", els.status.value);
    if (els.pillar.value) params.set("pillar", els.pillar.value);
    if (els.query.value.trim()) params.set("q", els.query.value.trim());
    try {
      const { body } = await api(`/api/darkarchives/stories?${params}`);
      state.nextId = body.next_id;
      state.videos = body.videos || [];
      renderSources(body.sources, body.counts);
      if (body.mix) renderMix(body.mix, body.pillar_labels || {}, body.next_pillar);
      renderList(body.stories);
      showError("");
      state.loaded = true;
    } catch (err) {
      showError(err.message === "Not Found"
        ? "The dashboard server is older than this page: restart the dashboard to load the Dark Outliers story bank."
        : `Couldn't load the story bank: ${err.message}`);
    }
  }

  async function open(id) {
    state.selected = id;
    els.list.querySelectorAll(".da-item").forEach((b) => b.classList.toggle("active", Number(b.dataset.id) === id));
    els.detail.innerHTML = '<div class="empty">Loading…</div>';
    try {
      const { body: s } = await api(`/api/darkarchives/stories/${id}`);
      const words = s.raw_text.split(/\s+/).filter(Boolean).length;
      els.detail.innerHTML = `
        <div class="da-detail-head">
          <h3>${esc(s.title)}</h3>
          <div class="da-meta">
            ${pillarPill(s.pillar)}
            <span class="pill da-pill-${family(s)}">${esc(s.source_name)}</span>
            <span class="pill">score ${s.score}</span>
            <span class="pill">${s.status}</span>
            <span class="hint">${words.toLocaleString()} words · ~${Math.round(words / 150)} min read aloud · ${esc(s.source_id)} · saved ${esc(s.created_at)} UTC</span>
          </div>
          ${s.note ? `<div class="da-note">${s.status === "REJECTED" ? "Rejected: " : ""}${esc(s.note)}</div>` : ""}
          <div class="da-actions">
            ${s.url ? `<a class="btn-secondary" href="${esc(s.url)}" target="_blank" rel="noopener">Open source ↗</a>` : '<span class="hint">Gemini: no source link, fact-check before using</span>'}
            ${STATUSES.map((st) => `<button type="button" class="btn-secondary" data-status="${st}" ${st === s.status ? "disabled" : ""}>${LABELS[st]}</button>`).join("")}
          </div>
          ${s.status === "USED" ? "" : `<div class="da-actions"><button type="button" class="btn-primary" id="daWrite">✍️ ${s.status === "SCRIPT_READY" ? "Rewrite" : "Write"} the Shorts script</button><span class="hint" id="daWriteStatus"></span></div>`}
        </div>
        <div id="daScript"></div>
        <div id="daVideo"></div>
        <details class="da-raw" ${s.status === "SCRIPT_READY" || s.status === "USED" ? "" : "open"}><summary>Raw story</summary>
          <div class="da-text">${esc(s.raw_text).replace(/\n/g, "<br>")}</div></details>`;
      loadScript(s.id);
      api("/api/darkarchives/script-job").then(({ body }) => {
        showScriptJob(body);
        if (body.running && body.story_id === s.id) setTimeout(pollScript, 2000);
      }).catch(() => {});
      api("/api/darkarchives/video-job").then(({ body }) => {
        showVideoJob(body);
        if (body.running && body.story_id === s.id) setTimeout(pollVideo, 2500);
      }).catch(() => {});
    } catch (err) {
      els.detail.innerHTML = `<div class="empty error">${esc(err.message)}</div>`;
    }
  }

  async function loadScript(id) {
    const box = $("daScript");
    try {
      const { body: sc } = await api(`/api/darkarchives/stories/${id}/script`);
      if (state.selected !== id) return;
      box.innerHTML = `
        <div class="da-script">
          <div class="da-script-head"><b>${esc(sc.title)}</b>
            <span class="pill good">${sc.total_word_count} words</span><span class="pill">~${sc.estimated_duration_seconds}s</span>
            <span class="pill">${sc.scenes.length} scenes</span>
            <a class="hint" href="/api/darkarchives/stories/${id}/script" target="_blank" rel="noopener">story_${id}.json ↗</a></div>
          <div class="da-hook"><small>HOOK 0-3s</small>${esc(sc.hook_text)}</div>
          ${sc.scenes.map((c) => `
            <div class="da-scene">
              <span class="da-scene-n">${c.scene_id}</span>
              <div>
                <div class="da-overlay">${esc(c.overlay_text)}</div>
                <div>${esc(c.narration)}</div>
                <div class="da-kw">${c.visual_keywords.map((k) => `<span class="pill">🎞️ ${esc(k)}</span>`).join(" ")}</div>
                <div class="hint">🖼️ ${esc(c.image_prompt)}</div>
              </div>
            </div>`).join("")}
        </div>
        <div class="da-actions da-make">
          <button type="button" class="btn-primary" id="daMake">🎬 ${state.videos.includes(id) ? "Remake" : "Make"} the video</button>
          <span class="hint" id="daMakeStatus"></span>
        </div>`;
      loadVideo(id);
    } catch (_) {
      box.innerHTML = "";
      $("daVideo").innerHTML = "";
    }
  }

  async function loadVideo(id) {
    const box = $("daVideo");
    try {
      const { body: v } = await api(`/api/darkarchives/stories/${id}/video/info`);
      if (state.selected !== id) return;
      const src = `/api/darkarchives/stories/${id}/video?t=${encodeURIComponent(v.created_at || "")}`;
      const pics = (v.beats || []).map((b) => `<figure class="da-beat" title="${esc(b.label)}: ${esc(b.text)}">
          <img src="/api/darkarchives/stories/${id}/video/pictures/${esc(b.picture)}" alt="" loading="lazy" />
          <figcaption>${esc(b.label)} · ${b.duration ? b.duration.toFixed(1) : (b.end - b.start).toFixed(1)}s${b.picture_source === "wikipedia" ? " · 📷 photo" : ""}</figcaption>
        </figure>`).join("");
      box.innerHTML = `
        <div class="da-video">
          <div class="da-video-head"><b>🎬 ${esc(v.filename)}</b>
            <span class="pill good">${v.duration_seconds}s</span>
            <span class="pill">pictures: ${esc(v.pictures)}</span>
            ${v.photos && v.photos.length ? `<span class="pill">${v.photos.length} photo${v.photos.length > 1 ? "s" : ""}</span>` : ""}
            ${v.music ? `<span class="pill">🎵 ${esc(v.music.title)}</span>` : ""}
            <span class="hint">made in ${v.seconds_to_make}s · ${esc(v.created_at || "")}</span>
            <a class="btn-secondary" href="${src}" download="${esc(v.filename)}">Download</a></div>
          <div class="da-video-body">
            <video controls preload="metadata" src="${src}"></video>
            <div>
              <div class="da-beats">${pics}</div>
              <div class="da-copy"><b>Description</b><pre class="da-desc">${esc(v.description || "")}</pre></div>
              <div class="da-youtube" id="daYouTube"></div>
            </div>
          </div>
        </div>`;
      loadYouTube(v);
    } catch (_) {
      box.innerHTML = "";
    }
  }

  function uploadedLine(up) {
    if (!up) return "";
    return `<div class="da-uploaded">✅ On YouTube (${esc(up.privacy)}): <a href="${esc(up.url)}" target="_blank" rel="noopener">${esc(up.url)}</a>
      · <a href="${esc(up.studio_url)}" target="_blank" rel="noopener">YouTube Studio ↗</a></div>`;
  }

  async function loadYouTube(info) {
    const box = $("daYouTube");
    if (!box) return;
    const up = info && info.uploads && info.uploads.youtube;
    try {
      const { body: yt } = await api("/api/darkarchives/youtube/status");
      const who = yt.channel ? `${esc(yt.channel.title)}${yt.handle ? ` (${esc(yt.handle)})` : ""}` : esc(yt.handle || "Dark Outliers");
      box.innerHTML = `
        <b>YouTube</b>
        ${!yt.folder ? '<span class="da-note">channels/darkarchives/channel.env is missing on the dashboard machine</span>' : ""}
        ${!yt.client_secret ? '<span class="da-note">client_secret.json is missing (Upload tab → Connect YouTube explains)</span>' : ""}
        <div class="da-actions">
          ${yt.connected
            ? `<span class="pill good">Connected: ${who}</span><button type="button" class="btn-secondary" id="daYtLogout">Disconnect</button>`
            : `<button type="button" class="btn-secondary" id="daYtConnect" ${yt.client_secret ? "" : "disabled"}>Connect YouTube</button>
               <span class="hint">Google opens in a new window: pick the <b>Dark Outliers</b> channel there</span>`}
          ${yt.error ? `<span class="da-note">${esc(yt.error)}</span>` : ""}
        </div>
        ${uploadedLine(up)}
        <div class="da-actions">
          <select id="daPrivacy"><option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option></select>
          <button type="button" class="btn-primary" id="daUpload" ${yt.connected ? "" : "disabled"}>⬆️ ${up ? "Upload again" : "Upload to YouTube"}</button>
          <span class="hint" id="daUploadStatus"></span>
        </div>`;
      api("/api/darkarchives/upload-job").then(({ body }) => {
        showUploadJob(body);
        if (body.running && body.story_id === state.selected) setTimeout(pollUpload, 2000);
      }).catch(() => {});
    } catch (err) {
      box.innerHTML = `<span class="da-note">YouTube: ${esc(err.message)}</span>`;
    }
  }

  async function connectYouTube() {
    let popup = window.open("", "trendclip-youtube", "width=520,height=720");
    try {
      const { body } = await api("/api/darkarchives/youtube/connect", { method: "POST" });
      if (popup) popup.location.href = body.auth_url;
      else popup = window.open(body.auth_url, "_blank");
    } catch (err) {
      if (popup) popup.close();
      return showError(`Could not start the Google login: ${err.message}`);
    }
    const timer = setInterval(() => {
      if (!popup || popup.closed) {
        clearInterval(timer);
        refreshYouTube();
      }
    }, 1000);
  }

  async function reloadVideo() {
    if (state.selected) await loadVideo(state.selected);
  }

  async function refreshYouTube() {
    await reloadVideo();
    if (sections.current === "videos") await renderYouTubeBar();
  }

  function showUploadJob(job) {
    const btn = $("daUpload");
    const status = $("daUploadStatus");
    if (!btn || !status) return;
    const mine = job.story_id === state.selected;
    btn.disabled = Boolean(job.running) || btn.dataset.off === "1";
    if (job.running) status.textContent = mine ? `${job.message || "Uploading"}…` : `Busy with story ${job.story_id}`;
    else if (mine && job.error) status.textContent = `❌ ${job.error}`;
    else status.textContent = "";
  }

  async function pollUpload() {
    try {
      const { body } = await api("/api/darkarchives/upload-job");
      showUploadJob(body);
      if (body.running) return setTimeout(pollUpload, 2000);
      if (body.finished_at && state.selected === body.story_id) await reloadVideo();
    } catch (err) {
      showError(err.message);
    }
  }

  async function uploadVideo() {
    const privacy = ($("daPrivacy") || {}).value || "private";
    try {
      const { body } = await api(`/api/darkarchives/stories/${state.selected}/upload`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ privacy }),
      });
      showUploadJob(body);
      setTimeout(pollUpload, 2000);
    } catch (err) {
      showError(err.message);
    }
  }

  function showVideoJob(job) {
    const btn = $("daMake");
    const status = $("daMakeStatus");
    if (!btn || !status) return;
    const mine = job.story_id === state.selected;
    btn.disabled = Boolean(job.running);
    if (job.running) {
      const pct = job.progress != null ? ` ${Math.round(job.progress * 100)}%` : "";
      status.textContent = mine ? `${job.message || "Working"}…${pct} (${Math.round(Date.now() / 1000 - job.started_at)}s; a video takes a few minutes)`
        : `Busy with story ${job.story_id}: ${job.message || ""}${pct}`;
    } else if (mine && job.error) status.textContent = `❌ ${job.error}`;
    else status.textContent = "";
  }

  async function pollVideo() {
    try {
      const { body } = await api("/api/darkarchives/video-job");
      showVideoJob(body);
      if (body.running) return setTimeout(pollVideo, 2500);
      if (body.finished_at) {
        await load();
        if (state.selected === body.story_id) {
          await open(body.story_id);
          showVideoJob(body);
        }
        onShow();
      }
    } catch (err) {
      showError(err.message);
    }
  }

  async function makeVideo() {
    try {
      const { body } = await api(`/api/darkarchives/stories/${state.selected}/video`, { method: "POST" });
      showVideoJob(body);
      setTimeout(pollVideo, 2500);
    } catch (err) {
      showError(err.message);
    }
  }

  function showScriptJob(job) {
    const btn = $("daWrite");
    const status = $("daWriteStatus");
    if (!btn || job.story_id !== state.selected) return;
    btn.disabled = Boolean(job.running);
    if (job.running) status.textContent = `Gemini is writing… ${Math.round(Date.now() / 1000 - job.started_at)}s (it can take a few minutes)`;
    else if (job.error) status.textContent = `❌ ${job.error}`;
    else if (job.status === "REJECTED") status.textContent = `Gemini rejected it: ${job.reason || "not a usable story"}`;
    else status.textContent = "";
  }

  async function pollScript() {
    try {
      const { body } = await api("/api/darkarchives/script-job");
      showScriptJob(body);
      if (body.running) return setTimeout(pollScript, 2000);
      if (body.finished_at) {
        if (body.ranking) {
          els.rank.disabled = false;
          els.fetchStatus.textContent = body.error ? `No ranking: ${body.error}` : `Ranking ready: ${body.title || ""} (story ${body.story_id})`;
          if (body.story_id) state.selected = body.story_id;
        }
        await load();
        if (state.selected === body.story_id) {
          await open(body.story_id);
          showScriptJob(body);
        }
      }
    } catch (err) {
      showError(err.message);
    }
  }

  async function rankStories() {
    try {
      els.rank.disabled = true;
      const { body } = await api("/api/darkarchives/ranking", { method: "POST" });
      els.fetchStatus.textContent = "Gemini is ranking the best stories…";
      showScriptJob(body);
      setTimeout(pollScript, 2000);
    } catch (err) {
      showError(err.message);
      els.rank.disabled = false;
    }
  }

  async function writeScript() {
    try {
      const { body } = await api(`/api/darkarchives/stories/${state.selected}/script`, { method: "POST" });
      showScriptJob(body);
      setTimeout(pollScript, 2000);
    } catch (err) {
      showError(err.message);
    }
  }

  async function setStatus(status) {
    try {
      await api(`/api/darkarchives/stories/${state.selected}/status`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }),
      });
      await load();
      await open(state.selected);
    } catch (err) {
      showError(err.message);
    }
  }

  function showJob(job) {
    const busy = Boolean(job.running);
    els.fetch.disabled = busy;
    els.fetch.textContent = busy ? "Fetching…" : "Fetch new stories";
    const parts = Object.entries(job.report || {}).map(([name, r]) => r.error
      ? `${SOURCES[name]}: ❌ ${r.error}`
      : `${SOURCES[name]}: ${r.saved} new, ${r.duplicates} already had (${r.fetched} fetched)`);
    if (busy && job.current) parts.push(`${SOURCES[job.current]}: working…`);
    els.fetchStatus.textContent = parts.join(" · ");
  }

  async function poll() {
    try {
      const { body } = await api("/api/darkarchives/fetch");
      showJob(body);
      if (body.running) {
        load();
        state.polling = setTimeout(poll, 2500);
      } else {
        state.polling = null;
        if (body.finished_at) load();
      }
    } catch (err) {
      state.polling = null;
      showError(err.message);
    }
  }

  async function startFetch() {
    const sources = [...document.querySelectorAll(".da-check input[type=checkbox]:checked")].map((c) => c.value);
    if (!sources.length) return showError("Pick at least one source");
    const n = (k) => Number(els.n[k].value) || undefined;
    try {
      const { body } = await api("/api/darkarchives/fetch", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sources, reddit: n("reddit"), wikipedia: n("wikipedia"), gemini: n("gemini"), rss: n("rss") }),
      });
      showError("");
      showJob(body);
      if (!state.polling) state.polling = setTimeout(poll, 1500);
    } catch (err) {
      showError(err.message);
    }
  }

  // ---- sections: Stories / Videos / Plan
  const sections = { current: "stories" };
  function showSection(name) {
    sections.current = name;
    document.querySelectorAll("#daNav [data-da-view]").forEach((b) => {
      b.classList.toggle("active", b.dataset.daView === name);
      b.setAttribute("aria-selected", b.dataset.daView === name ? "true" : "false");
    });
    document.querySelectorAll("[data-da-panel]").forEach((p) => { p.hidden = p.dataset.daPanel !== name; });
    if (name === "videos") loadVideos();
    if (name === "plan") { loadPlan(); linux.refresh(); }
  }
  $("daNav").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-da-view]");
    if (btn) showSection(btn.dataset.daView);
  });
  window.TrendClip.showDarkSection = showSection;

  // ---- Videos
  const vids = { error: $("daVideosError"), list: $("daVideos"), count: $("daVideosCount"), nav: $("daNavVideos") };
  const fmtWhen = (iso) => (iso || "").replace("T", " ").slice(0, 16);
  const todayIso = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const slotOptions = (slots, chosen) => slots.map((s) => `<option value="${s}" ${s === chosen ? "selected" : ""}>${s}</option>`).join("");

  vids.yt = null;
  async function renderYouTubeBar() {
    const bar = $("daVideosYouTube");
    try {
      const { body: yt } = await api("/api/darkarchives/youtube/status");
      vids.yt = yt;
      const who = yt.channel ? `${esc(yt.channel.title)}${yt.handle ? ` (${esc(yt.handle)})` : ""}` : esc(yt.handle || "Dark Outliers");
      bar.innerHTML = `<b>YouTube</b>
        ${yt.connected
          ? `<span class="pill good">Connected: ${who}</span><button type="button" class="btn-secondary" id="daVidYtLogout">Disconnect</button>
             <span class="hint">Planned videos go out by themselves; <b>Upload now</b> on a card sends it right away</span>`
          : `<span class="pill">Not connected</span><button type="button" class="btn-primary" id="daVidYtConnect" ${yt.client_secret ? "" : "disabled"}>Connect YouTube</button>
             <span class="hint">Google opens in a new window: pick the <b>Dark Outliers</b> channel there. Until then nothing can be uploaded, planned slots included</span>`}
        ${!yt.folder ? '<span class="da-note">channels/darkarchives/channel.env is missing on the dashboard machine</span>' : ""}
        ${!yt.client_secret ? '<span class="da-note">client_secret.json is missing (Upload tab → Connect YouTube explains)</span>' : ""}
        ${yt.error ? `<span class="da-note">${esc(yt.error)}</span>` : ""}`;
    } catch (err) {
      bar.innerHTML = `<span class="da-note">YouTube: ${esc(err.message)}</span>`;
    }
    vids.list.querySelectorAll("button[data-upload]").forEach((b) => { b.disabled = !(vids.yt && vids.yt.connected); });
  }
  $("daVideosYouTube").addEventListener("click", (e) => {
    if (e.target.closest("#daVidYtConnect")) connectYouTube();
    if (e.target.closest("#daVidYtLogout")) api("/api/darkarchives/youtube/logout", { method: "POST" }).then(refreshYouTube).catch((err) => showError(err.message));
  });

  async function uploadFromCard(card, storyId) {
    const privacy = card.querySelector("select[data-privacy]").value;
    const status = card.querySelector(".da-upload-status");
    try {
      const { body } = await api(`/api/darkarchives/stories/${storyId}/upload`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ privacy }),
      });
      vids.error.hidden = true;
      status.textContent = "Uploading…";
      const tick = async () => {
        const { body: job } = await api("/api/darkarchives/upload-job");
        if (job.running) {
          status.textContent = `${job.message || "Uploading"}…`;
          return setTimeout(tick, 2000);
        }
        status.textContent = job.error ? `❌ ${job.error}` : "";
        await loadVideos();
        onShow();
      };
      setTimeout(tick, 1500);
      return body;
    } catch (err) {
      vids.error.textContent = err.message;
      vids.error.hidden = false;
    }
  }

  async function loadVideos() {
    try {
      const { body } = await api("/api/darkarchives/videos");
      vids.data = body.videos;
      vids.error.hidden = true;
      vids.count.textContent = `· ${body.videos.length}`;
      vids.nav.textContent = body.videos.length;
      vids.nav.hidden = !body.videos.length;
      if (!body.videos.length) {
        vids.list.innerHTML = '<div class="empty">No videos yet. In <b>Stories</b>: pick a story → Write the Shorts script → Make the video.</div>';
        return;
      }
      vids.list.innerHTML = body.videos.map((v) => {
        const up = v.uploads && v.uploads.youtube;
        const first = (v.beats || [])[0];
        const pic = first ? `/api/darkarchives/stories/${v.story_id}/video/pictures/${esc(first.picture)}` : "";
        const src = `/api/darkarchives/stories/${v.story_id}/video?t=${encodeURIComponent(v.created_at || "")}`;
        const yt = v.youtube || {};
        return `<article class="da-vcard" data-story="${v.story_id}">
          <div class="da-vcard-player">
            <video controls preload="metadata" ${pic ? `poster="${pic}"` : ""} src="${src}"></video>
            <a class="hint" href="${src}" download="${esc(v.filename)}">Download ${esc(v.filename)}</a>
          </div>
          <div class="da-vcard-body">
            <b>${esc(v.title)}</b>
            <div class="pills"><span class="pill">${v.duration_seconds}s</span><span class="pill">story ${v.story_id}</span>
              ${up ? `<span class="pill good">▶️ YouTube · ${esc(up.privacy)}</span>` : '<span class="pill">not on YouTube</span>'}
              ${v.sent && v.sent.hub ? `<span class="pill good">✅ On Linux plan · ${esc(v.sent.hub.date)} ${esc(v.sent.hub.slot)}</span>`
                : v.planned.length ? v.planned.map((p) => `<span class="pill">📅 ${esc(p)}</span>`).join("") : '<span class="pill">not planned</span>'}</div>
            <span class="hint">made ${esc(fmtWhen(v.created_at))} · pictures: ${esc(v.pictures || "")}${v.photos && v.photos.length ? ` · ${v.photos.length} photo${v.photos.length > 1 ? "s" : ""}` : ""}${v.music ? ` · 🎵 ${esc(v.music.title)}` : ""}</span>
            ${up ? `<span class="hint"><a href="${esc(up.url)}" target="_blank" rel="noopener">${esc(up.url)}</a> · <a href="${esc(up.studio_url)}" target="_blank" rel="noopener">YouTube Studio ↗</a></span>` : ""}
            <div class="da-yt-text">
              <div class="da-yt-row"><span class="da-yt-label">YouTube title</span><span class="da-yt-title">${esc(yt.title || "")}</span></div>
              <div class="da-yt-row"><span class="da-yt-label">Description</span><pre class="da-desc">${esc(yt.description || "")}</pre></div>
              <div class="da-yt-row"><span class="da-yt-label">Tags</span><span class="pills">${(yt.tags || []).map((t) => `<span class="pill">${esc(t)}</span>`).join("")}</span></div>
              <div class="da-yt-row"><span class="da-yt-label">Goes out as</span><span class="hint">${up ? "already uploaded" : `${esc(body.privacy)} · Short · Entertainment (DA_UPLOAD_PRIVACY)`}</span></div>
            </div>
            <div class="da-vcard-actions">
              <input type="date" value="${todayIso()}" min="${todayIso()}" aria-label="Day" />
              <select aria-label="Time">${slotOptions(body.slots, body.slots[0])}</select>
              <button type="button" class="btn-secondary" data-plan="${v.story_id}">📅 Plan</button>
              <button type="button" class="btn-secondary" data-open="${v.story_id}">Story · pictures</button>
              <span class="da-sep"></span>
              <select data-privacy aria-label="Privacy"><option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public" ${body.privacy === "public" ? "selected" : ""}>Public</option></select>
              <button type="button" class="btn-primary" data-upload="${v.story_id}" disabled>⬆️ ${up ? "Upload again" : "Upload now"}</button>
              <span class="hint da-upload-status"></span>
              <button type="button" class="btn-ghost da-delete" data-delete="${v.story_id}" title="Delete the video and its pictures (the story can be made again)">🗑 Delete</button>
            </div>
          </div>
        </article>`;
      }).join("");
      renderYouTubeBar();
    } catch (err) {
      vids.error.textContent = `Couldn't load the videos: ${err.message}`;
      vids.error.hidden = false;
    }
  }

  async function planVideo(card, storyId) {
    const date = card.querySelector("input[type=date]").value;
    const slot = card.querySelector("select").value;
    try {
      await api("/api/darkarchives/plan/items", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ story_id: storyId, date, slot }) });
      vids.error.hidden = true;
      await loadVideos();
    } catch (err) {
      vids.error.textContent = err.message;
      vids.error.hidden = false;
    }
  }

  async function deleteVideo(storyId, title) {
    if (!window.confirm(`Delete the video "${title}"?\n\nThe file, its pictures and its plan entries are removed; the story goes back to "script ready" so it can be made again.${up(storyId) ? "\n\nIt stays on YouTube: remove it there yourself if you want." : ""}`)) return;
    try {
      await api(`/api/darkarchives/stories/${storyId}/video`, { method: "DELETE" });
      vids.error.hidden = true;
      await loadVideos();
      onShow();
      if (state.selected === storyId) open(storyId);
    } catch (err) {
      vids.error.textContent = err.message;
      vids.error.hidden = false;
    }
  }
  const up = (storyId) => Boolean((vids.data || []).find((v) => v.story_id === storyId && v.uploads && v.uploads.youtube));

  vids.list.addEventListener("click", (e) => {
    const upl = e.target.closest("button[data-upload]");
    if (upl) return uploadFromCard(upl.closest(".da-vcard"), Number(upl.dataset.upload));
    const del = e.target.closest("button[data-delete]");
    if (del) return deleteVideo(Number(del.dataset.delete), del.closest(".da-vcard").querySelector("b").textContent);
    const plan = e.target.closest("button[data-plan]");
    if (plan) return planVideo(plan.closest(".da-vcard"), Number(plan.dataset.plan));
    const openBtn = e.target.closest("button[data-open]");
    if (openBtn) {
      showSection("stories");
      els.status.value = "";
      load().then(() => open(Number(openBtn.dataset.open)));
    }
  });
  $("daVideosRefresh").addEventListener("click", loadVideos);

  // ---- Plan
  const plan = { error: $("daPlanError"), box: $("daPlan"), nav: $("daNavPlan"), hint: $("daPlanHint"), offset: 0, data: null };
  const DAYS_SHOWN = 14;
  const DAY_MS = 86400000;
  const dayIso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const dayLabel = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

  function renderPlan() {
    const d = plan.data;
    if (!d) return;
    const now = new Date();
    const today = dayIso(now);
    const pending = d.items.filter((i) => !i.youtube.done);
    plan.nav.textContent = pending.length;
    plan.nav.hidden = !pending.length;
    plan.hint.textContent = d.auto_post
      ? `The Discord bot uploads each slot's video to the Dark Outliers channel at its time (${d.slots.join(", ")}, as ${d.privacy}) and reports in its Discord channel`
      : `Automatic posting is off (DA_AUTO_POST): upload each planned video yourself from Videos`;
    const unplanned = d.videos.filter((v) => !(v.uploads && v.uploads.youtube) && !(v.sent && v.sent.hub));
    const byKey = {};
    d.items.forEach((i) => { byKey[`${i.date} ${i.slot}`] = i; });
    const start = new Date(now.getTime() + plan.offset * DAY_MS);
    const days = [];
    for (let n = 0; n < DAYS_SHOWN; n++) days.push(dayIso(new Date(start.getTime() + n * DAY_MS)));
    const late = d.items.filter((i) => !i.youtube.done && (i.date < days[0] || i.date < today));
    plan.box.innerHTML = (late.length && plan.offset >= 0 ? `<div class="da-note">${late.length} planned video${late.length > 1 ? "s" : ""} in the past not on YouTube: ${late.map((i) => `${esc(i.date)} ${esc(i.slot)} ${esc(i.title)}`).join(" · ")}</div>` : "") +
      days.map((iso) => `
      <div class="da-plan-day${iso === today ? " today" : ""}${iso < today ? " past" : ""}">
        <div class="da-plan-date"><b>${esc(dayLabel(iso))}</b><small>${esc(iso)}</small></div>
        <div class="da-plan-slots">${d.slots.map((slot) => {
          const item = byKey[`${iso} ${slot}`];
          const passed = `${iso} ${slot}` < `${today} ${now.toTimeString().slice(0, 5)}`;
          if (item) {
            const yt = item.youtube;
            const cls = yt.done ? " done" : passed ? " late" : "";
            return `<div class="da-plan-slot${cls}" data-item="${item.id}"><span class="time">${slot}</span>
              <span class="title">🎬 ${esc(item.title)}${item.video_missing ? ' <span class="da-note">(video missing)</span>' : ""}</span>
              ${yt.done ? `<span class="pill good">▶️ on YouTube${yt.privacy ? ` · ${esc(yt.privacy)}` : ""}</span>${yt.url ? ` <a class="hint" href="${esc(yt.url)}" target="_blank" rel="noopener">open ↗</a>` : ""}`
                : passed ? '<span class="pill">⚠️ not posted</span>' : '<span class="pill">⏳ waits for its time</span>'}
              <span class="spacer"></span>
              ${item.video_missing ? "" : `<button type="button" class="btn-ghost" data-watch="${item.story_id}" title="Play it here">▶ Watch</button>`}
              ${plan.sender && !yt.done && !passed && !item.video_missing ? `<button type="button" class="btn-secondary" data-hub="${item.story_id}" title="The Linux machine posts it instead">📤 Send to Linux</button>` : ""}
              ${yt.done ? "" : `<button type="button" class="btn-ghost" data-posted="${item.id}" title="I uploaded it myself">Mark posted</button>`}
              <button type="button" class="btn-ghost" data-remove="${item.id}" title="Take it off the plan">✕</button></div>`;
          }
          const options = unplanned.map((v) => `<option value="${v.story_id}">${esc(v.title)} (${v.duration_seconds}s${v.planned.length ? ", planned " + esc(v.planned[0]) : ""})</option>`).join("");
          return `<div class="da-plan-slot"><span class="time">${slot}</span>
            ${passed ? '<span class="hint">passed</span>' : options
              ? `<select aria-label="Video for ${iso} ${slot}"><option value="">— pick a video —</option>${options}</select>
                 <button type="button" class="btn-secondary" data-add="${iso}" data-slot="${slot}">Add</button>`
              : '<span class="hint">free · make a video first</span>'}</div>`;
        }).join("")}</div>
      </div>`).join("");
  }

  async function renderProduce() {
    const box = $("daProduce");
    try {
      const [{ body: t }, { body: job }] = await Promise.all([api("/api/darkarchives/produce/targets"), api("/api/darkarchives/produce-job")]);
      const slots = t.targets.length ? t.targets.join(", ") : "none (every slot of today and tomorrow is planned)";
      const done = job.result ? job.result.slots.map((r) => r.status === "planned" ? `✅ ${r.day} ${r.slot}: ${r.title}` : `❌ ${r.day} ${r.slot}: ${r.status} ${r.error || ""}`).join(" · ") : "";
      box.innerHTML = `<b>Automation</b>
        <span class="hint">${t.auto_produce ? `Every day at <b>${esc(t.time)}</b> the bot ${t.daily_fetch ? "refreshes the bank and " : ""}makes a video for each empty slot of tomorrow (and today's still 3 h away), checks it and plans it; the Discord post has a "Don't post this" button` : "Off (DA_AUTO_PRODUCE=0)"} · empty slots now: ${esc(slots)}</span>
        <button type="button" class="btn-primary" id="daProduceNow" ${job.running || !t.targets.length ? "disabled" : ""}>🏭 Run it now</button>
        <span class="hint" id="daProduceStatus">${job.running ? `${esc(job.message || "Working")}…${job.progress != null ? ` ${Math.round(job.progress * 100)}%` : ""}` : job.error ? `❌ ${esc(job.error)}` : esc(done)}</span>`;
      if (job.running) setTimeout(() => { renderProduce(); if (sections.current === "plan") loadPlan(); }, 3000);
    } catch (err) {
      box.innerHTML = `<span class="da-note">${esc(err.message)}</span>`;
    }
  }
  $("daProduce").addEventListener("click", async (e) => {
    if (!e.target.closest("#daProduceNow")) return;
    try {
      await api("/api/darkarchives/produce", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fetch: true }) });
      renderProduce();
    } catch (err) {
      plan.error.textContent = err.message;
      plan.error.hidden = false;
    }
  });

  // ---- Insights: the channel's own numbers and what the script prompts learn from them
  const fmtN = (n) => (n == null ? "–" : n >= 10000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
  const pct = (v) => (v == null ? "" : ` · ${Math.round(v)}% watched`);

  async function renderInsights(refresh = false) {
    const box = $("daInsights");
    if (refresh) box.innerHTML = `<div class="da-insights-head"><b>Insights</b><span class="hint">Reading YouTube…</span></div>`;
    try {
      const { body: r } = await api(`/api/darkarchives/insights${refresh ? "?refresh=true" : ""}`);
      const when = r.fetched_at ? new Date(r.fetched_at).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "never";
      const head = `<div class="da-insights-head"><b>Insights</b>
        <span class="hint">${r.videos ? `${r.measured} of ${r.videos} videos on YouTube measured${r.with_retention ? `, ${r.with_retention} with retention` : ""} · ${fmtN(r.totals.views)} views in all${r.avg_view_pct != null ? `, ${Math.round(r.avg_view_pct)}% watched on average` : ""}` : "No video on YouTube yet: the channel learns from its first uploads"} · read ${esc(when)}</span>
        <button type="button" class="btn-secondary" id="daInsightsRefresh" title="Read YouTube now">↻ Refresh</button></div>`;
      if (r.error) { box.innerHTML = head + `<span class="da-note">${esc(r.error)}</span>`; return; }
      if (!r.videos) { box.innerHTML = head; return; }
      const video = (v) => `<li><span>${v.url ? `<a href="${esc(v.url)}" target="_blank" rel="noopener">${esc(v.title)}</a>` : esc(v.title)}${v.pillar ? ` ${pillarPill(v.pillar)}` : ""}</span><span>${fmtN(v.views)} views${pct(v.avg_view_pct)}${v.views_day1 != null ? ` · ${fmtN(v.views_day1)} day 1` : ""}</span></li>`;
      const groups = Object.values(r.dimensions).flatMap((d) => d.groups.filter((g) => g.n >= 2).slice(0, 1).map((g) => `<span class="pill" title="${esc(d.label)}: ${g.n} videos">${esc(g.label)} · ${fmtN(g.views_median)} median${g.avg_view_pct != null ? ` · ${Math.round(g.avg_view_pct)}%` : ""}</span>`));
      box.innerHTML = head + `<div class="da-insights-grid">
        <div><div class="hint"><b>Top videos</b></div><ul class="da-insights-list">${r.top.map(video).join("")}</ul>
          ${r.retention_best.length ? `<div class="hint" style="margin-top:8px"><b>Held viewers best</b></div><ul class="da-insights-list">${r.retention_best.slice(0, 3).map(video).join("")}</ul>` : ""}</div>
        <div>${r.lessons ? `<div class="hint"><b>What the script prompts learn</b>${Object.keys(r.pillar_bias || {}).length ? ` · mix bias ${esc(Object.entries(r.pillar_bias).map(([p, b]) => `${p} ${b > 0 ? "+" : ""}${Math.round(b * 100)}%`).join(", "))}` : ""}</div><pre>${esc(r.lessons)}</pre>`
            : `<div class="hint">${esc(r.best_lesson || "")}${r.best_lesson ? "<br>" : ""}The lessons go into the script prompts once ${r.min_videos} videos are measured (${r.measured} so far)</div>`}
          ${groups.length ? `<div class="da-insights-groups" style="margin-top:8px">${groups.join("")}</div>` : ""}</div></div>
        ${r.warnings.length ? `<span class="da-note">${r.warnings.map(esc).join(" · ")}</span>` : ""}`;
    } catch (err) {
      box.innerHTML = `<div class="da-insights-head"><b>Insights</b><span class="da-note">${esc(err.message)}</span></div>`;
    }
  }
  $("daInsights").addEventListener("click", (e) => {
    if (e.target.closest("#daInsightsRefresh")) renderInsights(true);
  });

  const { channelHub } = window.TrendClip;
  const linux = channelHub.mount($("daHub"), "darkarchives", () => loadPlan());
  async function loadPlan() {
    try {
      const [{ body }, sender] = await Promise.all([api("/api/darkarchives/plan"), channelHub.sender()]);
      plan.data = body;
      plan.sender = sender;
      plan.error.hidden = true;
      renderPlan();
      linux.counts();
      renderProduce();
      renderInsights();
    } catch (err) {
      plan.error.textContent = `Couldn't load the plan: ${err.message}`;
      plan.error.hidden = false;
    }
  }

  async function planCall(path, options) {
    try {
      await api(path, options);
      plan.error.hidden = true;
      await loadPlan();
    } catch (err) {
      plan.error.textContent = err.message;
      plan.error.hidden = false;
    }
  }

  plan.box.addEventListener("click", (e) => {
    const json = (data) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    const watchBtn = e.target.closest("button[data-watch]");
    if (watchBtn) return channelHub.watch(watchBtn.closest(".da-plan-slot"), `/api/darkarchives/stories/${watchBtn.dataset.watch}/video`);
    const hubBtn = e.target.closest("button[data-hub]");
    if (hubBtn) return channelHub.picker(hubBtn.closest(".da-plan-slot"), "darkarchives", hubBtn.dataset.hub, () => { setTimeout(loadPlan, 1500); linux.refresh(); });
    const add = e.target.closest("button[data-add]");
    if (add) {
      const sel = add.parentElement.querySelector("select");
      if (!sel || !sel.value) return;
      return planCall("/api/darkarchives/plan/items", json({ story_id: Number(sel.value), date: add.dataset.add, slot: add.dataset.slot }));
    }
    const rm = e.target.closest("button[data-remove]");
    if (rm) return planCall(`/api/darkarchives/plan/items/${rm.dataset.remove}`, { method: "DELETE" });
    const posted = e.target.closest("button[data-posted]");
    if (posted) return planCall(`/api/darkarchives/plan/items/${posted.dataset.posted}/posted`, json({ posted: true }));
  });
  $("daPlanPrev").addEventListener("click", () => { plan.offset -= 7; renderPlan(); });
  $("daPlanNext").addEventListener("click", () => { plan.offset += 7; renderPlan(); });
  $("daPlanToday").addEventListener("click", () => { plan.offset = 0; renderPlan(); });

  els.list.addEventListener("click", (e) => {
    const item = e.target.closest(".da-item");
    if (item) open(Number(item.dataset.id));
  });
  els.detail.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-status]");
    if (btn) setStatus(btn.dataset.status);
    if (e.target.closest("#daWrite")) writeScript();
    if (e.target.closest("#daMake")) makeVideo();
    if (e.target.closest("#daYtConnect")) connectYouTube();
    if (e.target.closest("#daYtLogout")) api("/api/darkarchives/youtube/logout", { method: "POST" }).then(reloadVideo).catch((err) => showError(err.message));
    if (e.target.closest("#daUpload")) uploadVideo();
  });
  window.addEventListener("message", (e) => { if (e.data === "trendclip:youtube") refreshYouTube(); });
  els.source.addEventListener("change", load);
  els.status.addEventListener("change", load);
  els.pillar.addEventListener("change", load);
  els.query.addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(load, 250);
  });
  els.fetch.addEventListener("click", startFetch);
  els.rank.addEventListener("click", rankStories);

  const onShow = () => {
    if (document.body.dataset.workspace !== "darkarchives") return;
    showSection(sections.current);
    if (!state.loaded) load();
    if (!state.polling) poll();
    api("/api/darkarchives/plan").then(({ body }) => {
      plan.data = body;
      const pending = body.items.filter((i) => !i.youtube.done).length;
      plan.nav.textContent = pending;
      plan.nav.hidden = !pending;
      vids.nav.textContent = body.videos.length;
      vids.nav.hidden = !body.videos.length;
    }).catch(() => {});
  };
  new MutationObserver(onShow).observe(document.body, { attributes: true, attributeFilter: ["data-workspace"] });
  onShow();
})();
