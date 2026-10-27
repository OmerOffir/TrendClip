// Borderline Why tab: the YouTube login, "make the next video now", the videos with Approve / Don't post / Upload,
// and the topic list (with questions added by hand).
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const els = {
    error: $("bwError"), hint: $("bwAutoHint"), yt: $("bwYouTube"), make: $("bwMake"), makeStatus: $("bwMakeStatus"),
    list: $("bwVideos"), count: $("bwVideosCount"), navWaiting: $("bwNavWaiting"), navTopics: $("bwNavTopics"),
    topics: $("bwTopics"), topicsCount: $("bwTopicsCount"), topicsError: $("bwTopicsError"),
  };
  const state = { section: "videos", status: null, polling: { make: null, upload: null } };
  const showError = (msg) => { els.error.textContent = msg || ""; els.error.hidden = !msg; };
  const fmtWhen = (iso) => (iso || "").replace("T", " ").slice(0, 16);
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const STATES = {
    posted: ["good", "▶️ On YouTube"], approved: ["good", "✅ Approved"], waiting: ["warn", "⏸ Waiting for Approve"],
    vetoed: ["", "🚫 Won't be posted"], blocked: ["warn", "❌ Failed its check"],
  };

  // ---- status and YouTube
  async function loadStatus() {
    try {
      const { body } = await api("/api/borderlinewhy/status");
      state.status = body;
      const nxt = body.next_job;
      els.hint.innerHTML = `One video a day at <b>${esc(body.slots.join(", "))}</b> (${esc(body.timezone)}), made ${body.lead_hours} h before`
        + ` and posted in Discord · ${body.require_approval ? "goes out only after <b>Approve</b>" : "goes out unless vetoed"}`
        + ` · as ${esc(body.privacy)}`
        + (nxt ? ` · next: ${esc(fmtWhen(nxt.post_at))} (made at ${esc(nxt.start.slice(11, 16))})` : "")
        + ` · ${body.topics.left} topics left${body.queued.length ? `, ${body.queued.length} hand-checked script(s) queued` : ""}`
        + (body.auto_make ? "" : " · <b>automatic making is off</b> (BW_AUTO_MAKE)")
        + (body.discord ? "" : " · <b>no Discord channel</b> (channels/borderlinewhy/channel.env)");
      els.navTopics.textContent = body.topics.left;
      els.navTopics.hidden = !body.topics.left;
      renderYouTube(body.youtube);
      showMake(body.make);
      if (body.make.running) pollMake();
      if (body.upload.running) pollUpload();
      showError("");
    } catch (err) {
      showError(err.message);
    }
  }

  function renderYouTube(yt) {
    const who = yt.channel ? `${esc(yt.channel.title)}${yt.handle ? ` (${esc(yt.handle)})` : ""}` : esc(yt.handle || "Borderline Why");
    els.yt.innerHTML = `<b>YouTube</b>
      ${yt.connected
        ? `<span class="pill good">Connected: ${who}</span><button type="button" class="btn-secondary" id="bwYtLogout">Disconnect</button>
           <span class="hint">Approved videos go out at their slot; <b>Upload now</b> on a card sends it right away</span>`
        : `<span class="pill">Not connected</span><button type="button" class="btn-primary" id="bwYtConnect" ${yt.client_secret ? "" : "disabled"}>Connect YouTube</button>
           <span class="hint">Google opens in a new window: pick the <b>Borderline Why</b> channel there. Until then nothing can be uploaded</span>`}
      ${!yt.folder ? '<span class="da-note">channels/borderlinewhy/ is missing on the dashboard machine</span>' : ""}
      ${!yt.client_secret ? '<span class="da-note">client_secret.json is missing (Upload tab → Connect YouTube explains)</span>' : ""}
      ${yt.error ? `<span class="da-note">${esc(yt.error)}</span>` : ""}`;
    els.list.querySelectorAll("button[data-upload]").forEach((b) => { b.disabled = !yt.connected; });
  }

  async function connectYouTube() {
    let popup = window.open("", "trendclip-youtube", "width=520,height=720");
    try {
      const { body } = await post("/api/borderlinewhy/youtube/connect");
      if (popup) popup.location.href = body.auth_url;
      else popup = window.open(body.auth_url, "_blank");
    } catch (err) {
      if (popup) popup.close();
      return showError(`Could not start the Google login: ${err.message}`);
    }
    const timer = setInterval(() => {
      if (!popup || popup.closed) {
        clearInterval(timer);
        loadStatus();
      }
    }, 1000);
  }

  els.yt.addEventListener("click", (e) => {
    if (e.target.closest("#bwYtConnect")) connectYouTube();
    if (e.target.closest("#bwYtLogout")) post("/api/borderlinewhy/youtube/logout").then(loadStatus).catch((err) => showError(err.message));
  });
  window.addEventListener("message", (e) => { if (e.data === "trendclip:youtube" && document.body.dataset.workspace === "borderlinewhy") loadStatus(); });

  // ---- make now
  function showMake(job) {
    els.make.disabled = !!job.running;
    els.makeStatus.textContent = job.running
      ? `${job.message || "Working"}${job.fraction != null ? ` (${Math.round(job.fraction * 100)}%)` : ""}…`
      : job.error ? `❌ ${job.error}` : job.result ? `✅ Made ${job.result}: it is below and in Discord` : "";
  }

  async function pollMake() {
    clearTimeout(state.polling.make);
    try {
      const { body } = await api("/api/borderlinewhy/make-job");
      showMake(body);
      if (body.running) state.polling.make = setTimeout(pollMake, 2000);
      else loadVideos();
    } catch (err) {
      showError(err.message);
    }
  }

  els.make.addEventListener("click", async () => {
    if (!confirm("Make the next Borderline Why video now? A video already made for that slot (not uploaded) is set aside.")) return;
    try {
      const { body } = await post("/api/borderlinewhy/make");
      showMake(body);
      pollMake();
    } catch (err) {
      showError(err.message);
    }
  });

  // ---- videos
  function card(v, privacy, connected) {
    const up = v.uploads && v.uploads.youtube;
    const [cls, label] = STATES[v.state] || ["", v.state];
    const src = `/api/borderlinewhy/videos/${encodeURIComponent(v.key)}/video.mp4?t=${encodeURIComponent(v.created_at || "")}`;
    const yt = v.youtube || {};
    const sources = (v.sources || []).map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a></li>`).join("");
    const done = v.state === "posted";
    return `<article class="da-vcard" data-key="${esc(v.key)}">
      <div class="da-vcard-player">
        <video controls preload="metadata" src="${src}"></video>
        <a class="hint" href="${src}" download="${esc(v.key)}.mp4">Download</a>
      </div>
      <div class="da-vcard-body">
        <b>${esc(v.title)}</b>
        <div class="pills"><span class="pill ${cls}">${label}</span><span class="pill">${Math.round(v.duration_seconds)}s</span>
          <span class="pill">📅 ${esc(fmtWhen((v.job || {}).post_at))}</span><span class="pill">${esc(v.words)} words</span></div>
        <span class="hint">made ${esc(fmtWhen(v.created_at))} · ${esc(v.origin || "")}${v.music ? ` · 🎵 ${esc(v.music.title)}` : ""} · palette ${esc(v.theme || "")}</span>
        ${v.check && v.check.length ? `<span class="da-note">${esc(v.check.join("; "))}</span>` : ""}
        ${up ? `<span class="hint"><a href="${esc(up.url)}" target="_blank" rel="noopener">${esc(up.url)}</a>${up.studio_url ? ` · <a href="${esc(up.studio_url)}" target="_blank" rel="noopener">YouTube Studio ↗</a>` : ""}</span>` : ""}
        <div class="da-yt-text">
          <div class="da-yt-row"><span class="da-yt-label">Question</span><span>${esc(v.question || "")}</span></div>
          <div class="da-yt-row"><span class="da-yt-label">Sources</span><ul class="bw-sources">${sources || "<li>none</li>"}</ul></div>
          <div class="da-yt-row"><span class="da-yt-label">YouTube title</span><span class="da-yt-title">${esc(yt.title || "")}</span></div>
          <div class="da-yt-row"><span class="da-yt-label">Description</span><pre class="da-desc">${esc(yt.description || "")}</pre></div>
        </div>
        <div class="da-vcard-actions da-actions">
          ${!done && v.state !== "approved" && v.state !== "blocked" ? `<button type="button" class="btn-primary" data-approve>✅ Approve</button>` : ""}
          ${v.state !== "vetoed" ? `<button type="button" class="btn-secondary" data-veto>🚫 ${done ? "Make it private" : "Don't post this"}</button>` : ""}
          ${!done && v.state !== "blocked" ? `<select data-privacy aria-label="Privacy">${["public", "unlisted", "private"].map((p) => `<option value="${p}" ${p === privacy ? "selected" : ""}>${p}</option>`).join("")}</select>
            <button type="button" class="btn-secondary" data-upload ${connected ? "" : "disabled"}>⬆️ Upload now</button>` : ""}
          <span class="hint bw-card-status"></span>
        </div>
      </div>
    </article>`;
  }

  async function loadVideos() {
    try {
      const { body } = await api("/api/borderlinewhy/videos");
      const connected = !!(state.status && state.status.youtube.connected);
      els.count.textContent = `· ${body.videos.length}`;
      const waiting = body.videos.filter((v) => v.state === "waiting").length;
      els.navWaiting.textContent = waiting;
      els.navWaiting.hidden = !waiting;
      els.list.innerHTML = body.videos.length ? body.videos.map((v) => card(v, body.privacy, connected)).join("")
        : '<div class="empty">No videos yet. <b>Make the next video now</b>, or wait for the daily run.</div>';
    } catch (err) {
      showError(err.message);
    }
  }

  async function pollUpload(statusEl) {
    clearTimeout(state.polling.upload);
    try {
      const { body } = await api("/api/borderlinewhy/upload-job");
      const el = statusEl || els.list.querySelector(`[data-key="${CSS.escape(body.key || "")}"] .bw-card-status`);
      if (el) el.textContent = body.running ? `${body.message || "Uploading"}…` : body.error ? `❌ ${body.error}` : "";
      if (body.running) state.polling.upload = setTimeout(() => pollUpload(statusEl), 2000);
      else { loadVideos(); loadStatus(); }
    } catch (err) {
      showError(err.message);
    }
  }

  els.list.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    const cardEl = e.target.closest("[data-key]");
    if (!btn || !cardEl) return;
    const key = cardEl.dataset.key;
    const status = cardEl.querySelector(".bw-card-status");
    try {
      if (btn.hasAttribute("data-approve")) {
        const { body } = await post(`/api/borderlinewhy/videos/${encodeURIComponent(key)}/approve`);
        if (body.uploading) { status.textContent = "Uploading…"; return pollUpload(status); }
      } else if (btn.hasAttribute("data-veto")) {
        if (!confirm("Stop this video? Before the upload it won't be posted; after it, it becomes private on YouTube.")) return;
        await post(`/api/borderlinewhy/videos/${encodeURIComponent(key)}/veto`);
      } else if (btn.hasAttribute("data-upload")) {
        const privacy = cardEl.querySelector("select[data-privacy]").value;
        if (!confirm(`Upload it to Borderline Why now, as ${privacy}?`)) return;
        await post(`/api/borderlinewhy/videos/${encodeURIComponent(key)}/upload`, { privacy });
        status.textContent = "Uploading…";
        return pollUpload(status);
      } else {
        return;
      }
      loadVideos();
    } catch (err) {
      showError(err.message);
    }
  });

  // ---- topics
  const TOPIC = { next: "▶ next", waiting: "", used: "✅ made", dropped: "✖ dropped" };
  async function loadTopics() {
    try {
      const { body } = await api("/api/borderlinewhy/topics");
      const s = body.summary;
      els.topicsCount.textContent = `· ${s.left} left · ${s.used} made · ${s.rejected} dropped`;
      els.topics.innerHTML = body.topics.map((t) => `<div class="bw-topic ${esc(t.status)}">
          <span class="bw-n">${t.order != null ? t.order + 1 : ""}</span>
          <span>${esc(t.question)}<small>Wikipedia: ${esc(t.article)}${t.source !== "seed" ? ` · ${esc(t.source)}` : ""}${t.note ? ` · ${esc(t.note)}` : ""}</small></span>
          <span class="pill">${TOPIC[t.status] || esc(t.status)}</span>
        </div>`).join("") || '<div class="empty">No topics.</div>';
      els.topicsError.hidden = true;
    } catch (err) {
      els.topicsError.textContent = err.message;
      els.topicsError.hidden = false;
    }
  }

  $("bwAdd").addEventListener("submit", async (e) => {
    e.preventDefault();
    const status = $("bwAddStatus");
    status.textContent = "Checking Wikipedia…";
    try {
      const { body } = await post("/api/borderlinewhy/topics", { question: $("bwAddQuestion").value, article: $("bwAddArticle").value });
      status.textContent = `✅ Added (Wikipedia: ${body.article}); it is made next`;
      $("bwAddQuestion").value = "";
      $("bwAddArticle").value = "";
      loadTopics();
    } catch (err) {
      status.textContent = `❌ ${err.message}`;
    }
  });
  $("bwTopicsRefresh").addEventListener("click", loadTopics);
  $("bwRefresh").addEventListener("click", () => { loadStatus().then(loadVideos); });

  // ---- sections
  function showSection(name) {
    state.section = name;
    document.querySelectorAll("#bwNav [data-bw-view]").forEach((b) => {
      b.classList.toggle("active", b.dataset.bwView === name);
      b.setAttribute("aria-selected", b.dataset.bwView === name ? "true" : "false");
    });
    document.querySelectorAll("[data-bw-panel]").forEach((p) => { p.hidden = p.dataset.bwPanel !== name; });
    if (name === "topics") loadTopics();
  }
  $("bwNav").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-bw-view]");
    if (btn) showSection(btn.dataset.bwView);
  });

  const onShow = () => {
    if (document.body.dataset.workspace !== "borderlinewhy") return;
    showSection(state.section);
    loadStatus().then(loadVideos);
  };
  new MutationObserver(onShow).observe(document.body, { attributes: true, attributeFilter: ["data-workspace"] });
  onShow();
})();
