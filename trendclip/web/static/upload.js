(() => {
  "use strict";

  const T = window.TrendClip;
  const { api, esc } = T;
  const $ = (id) => document.getElementById(id);

  const els = {
    view: $("viewUpload"),
    error: $("uploadError"),
    list: $("readyList"),
    panel: $("uploadPanel"),
    navCount: $("navUploadCount"),
    video: $("uVideo"),
    title: $("uTitle"),
    meta: $("uMeta"),
    download: $("uDownload"),
    unready: $("uUnready"),
    tabs: $("uTabs"),
    source: $("uSource"),
    gemini: $("uGemini"),
    template: $("uTemplate"),
    textsJob: $("uTextsJob"),
    ytAccount: $("ytAccount"),
    ytTitle: $("ytTitle"),
    ytTitleCount: $("ytTitleCount"),
    ytDesc: $("ytDesc"),
    ytHashtags: $("ytHashtags"),
    ytTags: $("ytTags"),
    ytTagsCount: $("ytTagsCount"),
    ytFinal: $("ytFinal"),
    ytPrivacy: $("ytPrivacy"),
    ytKids: $("ytKids"),
    ytSynthetic: $("ytSynthetic"),
    ytUpload: $("ytUpload"),
    ytWhenField: $("ytWhenField"),
    ytWhen: $("ytWhen"),
    ytTz: $("ytTz"),
    ytQuick: $("ytQuick"),
    ytWhenHint: $("ytWhenHint"),
    ytJob: $("ytJob"),
    ytDone: $("ytDone"),
    ytPosted: $("ytPosted"),
    ytPostedField: $("ytPostedField"),
    ytAfterPrev: $("ytAfterPrev"),
    ytSeries: $("ytSeries"),
    ytSeriesLinks: $("ytSeriesLinks"),
    ytComment: $("ytComment"),
    ytCommentLabel: $("ytCommentLabel"),
    ytCommentCopy: $("ytCommentCopy"),
    ytCommentPost: $("ytCommentPost"),
    ytCommentHint: $("ytCommentHint"),
    igCaption: $("igCaption"),
    igHashtags: $("igHashtags"),
    igMentions: $("igMentions"),
    igTagCount: $("igTagCount"),
    igFinal: $("igFinal"),
    igCount: $("igCount"),
    igCopy: $("igCopy"),
    igDownload: $("igDownload"),
    igPosted: $("igPosted"),
    ttCaption: $("ttCaption"),
    ttHashtags: $("ttHashtags"),
    ttMentions: $("ttMentions"),
    ttFinal: $("ttFinal"),
    ttCount: $("ttCount"),
    ttCopy: $("ttCopy"),
    ttDownload: $("ttDownload"),
    ttPosted: $("ttPosted"),
  };

  const CAPTION_MAX = 2200;
  const IG_MAX_TAGS = 5;
  const YT_TAGS_MAX = 450;

  const state = {
    started: false,
    shorts: [],
    selected: null,
    platform: "youtube",
    yt: null,
    saveTimer: null,
    busy: { texts: false, upload: false, comment: false },
  };

  const current = () => state.shorts.find((s) => s.filename === state.selected) || null;

  function showError(message) {
    els.error.textContent = message;
    els.error.hidden = !message;
    if (message) els.error.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // ---- text helpers --------------------------------------------------------------------

  const words = (value) => value.split(/[\s,]+/).map((w) => w.trim()).filter(Boolean);
  const asTags = (value, prefix) => words(value).map((w) => prefix + w.replace(/^[#@]+/, "")).filter((w) => w.length > 1);
  const ytTagList = (value) => value.split(",").map((t) => t.trim()).filter(Boolean);
  const ytTagChars = (tags) => tags.reduce((n, t) => n + t.length + (t.includes(" ") ? 2 : 0) + 1, 0);
  const join = (parts) => parts.map((p) => (p || "").trim()).filter(Boolean).join("\n\n");

  function readTexts() {
    const s = current();
    const prev = s.texts;
    return {
      ...prev,
      youtube: {
        title: els.ytTitle.value,
        description: els.ytDesc.value,
        tags: ytTagList(els.ytTags.value),
        hashtags: asTags(els.ytHashtags.value, "#"),
      },
      instagram: {
        caption: els.igCaption.value,
        hashtags: asTags(els.igHashtags.value, "#"),
        mentions: asTags(els.igMentions.value, "@"),
      },
      tiktok: {
        caption: els.ttCaption.value,
        hashtags: asTags(els.ttHashtags.value, "#"),
        mentions: asTags(els.ttMentions.value, "@"),
      },
    };
  }

  const seriesLinks = () => (current() && current().series ? current().series.links : "");
  const ytDescription = (t) => join([t.youtube.description, seriesLinks(), t.youtube.hashtags.join(" "), t.credit]);
  const social = (p, credit) => join([p.caption, p.mentions.join(" "), p.hashtags.join(" "), credit]).slice(0, CAPTION_MAX);

  function renderFinals() {
    const s = current();
    if (!s || !s.texts) return;
    const t = readTexts();
    els.ytFinal.textContent = ytDescription(t);
    const title = els.ytTitle.value.trim();
    els.ytTitleCount.textContent = `${title.length}/100${/#shorts/i.test(title) ? "" : " · #shorts is added"}`;
    const tagChars = ytTagChars(t.youtube.tags);
    els.ytTagsCount.textContent = `${tagChars}/${YT_TAGS_MAX} characters`;
    els.ytTagsCount.classList.toggle("over", tagChars > YT_TAGS_MAX);

    const ig = social(t.instagram, t.credit);
    els.igFinal.textContent = ig;
    els.igCount.textContent = `${ig.length}/${CAPTION_MAX}`;
    const igTags = t.instagram.hashtags.length;
    els.igTagCount.textContent = `${igTags}/${IG_MAX_TAGS}${igTags > IG_MAX_TAGS ? " · Instagram allows 5, extra ones are dropped" : ""}`;
    els.igTagCount.classList.toggle("over", igTags > IG_MAX_TAGS);

    const tt = social(t.tiktok, t.credit);
    els.ttFinal.textContent = tt;
    els.ttCount.textContent = `${tt.length}/${CAPTION_MAX}`;
  }

  // ---- list ----------------------------------------------------------------------------

  function badges(s) {
    const out = [];
    const yt = s.uploads && s.uploads.youtube;
    if (yt) {
      const label = yt.scheduled_for ? `scheduled ${fmtWhen(new Date(yt.scheduled_for))}` : yt.privacy;
      out.push(`<span class="pill done">YouTube · ${esc(label)}</span>`);
    }
    else if (s.posted && s.posted.youtube) out.push('<span class="pill done">YouTube</span>');
    if (s.posted && s.posted.instagram) out.push('<span class="pill done">Instagram</span>');
    if (s.posted && s.posted.tiktok) out.push('<span class="pill done">TikTok</span>');
    return out.join("");
  }

  function renderList() {
    els.navCount.textContent = String(state.shorts.length);
    els.navCount.hidden = !state.shorts.length;
    if (!state.shorts.length) {
      els.list.innerHTML = '<div class="empty">Nothing here yet. In <a href="#create" data-goto="create"><b>Create</b></a> → Your Shorts, press <b>Ready to upload</b> on a video.</div>';
      els.panel.hidden = true;
      return;
    }
    els.list.innerHTML = state.shorts.map((s) => `
      <button type="button" class="ready-card${s.filename === state.selected ? " selected" : ""}" data-select="${esc(s.filename)}">
        <video src="/media/shorts/${encodeURIComponent(s.filename)}#t=1" muted preload="metadata" playsinline></video>
        <span class="ready-body">
          <span class="dl-title">${s.part ? `<span class="pill part">PART ${s.part}/${s.parts_total || 2}</span> ` : ""}${esc(s.title)}</span>
          <span class="dl-meta">${esc(s.game)} · ${s.duration_seconds ? `${Math.round(s.duration_seconds)}s` : ""}</span>
          <span class="ready-badges">${badges(s)}</span>
        </span>
      </button>`).join("");
  }

  function fill() {
    const s = current();
    els.panel.hidden = !s;
    if (!s) return;
    const src = `/media/shorts/${encodeURIComponent(s.filename)}`;
    if (els.video.getAttribute("src") !== src) els.video.src = src;
    for (const a of [els.download, els.igDownload, els.ttDownload]) {
      a.href = src;
      a.setAttribute("download", s.filename);
    }
    els.title.textContent = s.title;
    els.meta.innerHTML = `${esc(s.game)} · ${Math.round(s.duration_seconds || 0)}s${s.music_title ? ` · ♪ ${esc(s.music_title)}` : ""}`;

    const t = s.texts;
    els.source.textContent = t.source === "gemini" ? `Texts written by Gemini (${t.model})` : "Texts from the template (edit freely, or let Gemini improve them)";
    els.ytTitle.value = t.youtube.title;
    els.ytDesc.value = t.youtube.description;
    els.ytHashtags.value = t.youtube.hashtags.join(" ");
    els.ytTags.value = t.youtube.tags.join(", ");
    els.igCaption.value = t.instagram.caption;
    els.igHashtags.value = t.instagram.hashtags.join(" ");
    els.igMentions.value = t.instagram.mentions.join(" ");
    els.ttCaption.value = t.tiktok.caption;
    els.ttHashtags.value = t.tiktok.hashtags.join(" ");
    els.ttMentions.value = t.tiktok.mentions.join(" ");
    els.igPosted.checked = !!(s.posted && s.posted.instagram);
    els.ttPosted.checked = !!(s.posted && s.posted.tiktok);
    renderUploaded();
    renderSeries();
    renderFinals();
    updateButtons();
  }

  function renderSeries() {
    const s = current();
    const info = s && s.series;
    els.ytSeries.hidden = !info;
    els.ytAfterPrev.hidden = !(info && info.part > 1 && prevPartTime());
    if (!info) return;
    const rows = info.siblings.map((p) => {
      const me = p.filename === s.filename;
      const status = p.youtube_url
        ? `<a href="${esc(p.youtube_url)}" target="_blank" rel="noopener">${esc(p.youtube_url)}</a>${p.scheduled_for ? ` · goes public ${esc(fmtWhen(new Date(p.scheduled_for)))}` : ""}`
        : p.ready ? "not uploaded yet" : "not marked ready (Create tab)";
      return `<div>${me ? "<b>" : ""}Part ${p.part}${me ? " (this one)</b>" : ""}: ${status}</div>`;
    });
    els.ytSeriesLinks.innerHTML = `<div><b>“${esc(info.story_name)}”</b> · Part ${info.part} of ${info.total} · ${esc(info.handle)}</div>${rows.join("")}`;
    els.ytCommentLabel.textContent = info.part < info.total ? "Pinned comment (teases / links Part 2)" : "Pinned comment (links back to Part 1)";
    if (document.activeElement !== els.ytComment) els.ytComment.value = info.pinned_comment;
    const posted = s.uploads && s.uploads.youtube && s.uploads.youtube.comment;
    els.ytCommentHint.innerHTML = (posted
      ? `Posted ${esc(fmtWhen(new Date(posted.posted_at)))} · <a href="${esc(posted.url)}" target="_blank" rel="noopener">open comment</a>. `
      : "") + "YouTube's API can't pin comments: open the video, tap <b>⋮</b> on your comment → <b>Pin</b>. " +
      (info.part < info.total ? "Once the next part is uploaded, this comment switches to its link." : "");
  }

  function prevPartTime() {
    const info = current() && current().series;
    if (!info) return null;
    const prev = info.siblings.find((p) => p.part === info.part - 1);
    const when = prev && (prev.scheduled_for || prev.uploaded_at);
    return when ? new Date(when) : null;
  }

  async function postComment() {
    const s = current();
    const text = els.ytComment.value.trim();
    if (!text || !confirm("Post this comment on YouTube as your channel?")) return;
    state.busy.comment = true;
    updateButtons();
    try {
      const { body } = await api(`/api/upload/shorts/${encodeURIComponent(s.filename)}/youtube/comment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      replace(body);
      renderSeries();
      showError("");
    } catch (err) {
      showError(err.message);
    } finally {
      state.busy.comment = false;
      updateButtons();
    }
  }

  function renderUploaded() {
    const up = current() && current().uploads && current().uploads.youtube;
    els.ytDone.hidden = !up;
    els.ytPostedField.hidden = !!up;
    els.ytPosted.checked = !!(current() && current().posted && current().posted.youtube);
    if (!up) return;
    const scheduled = up.scheduled_for ? new Date(up.scheduled_for) : null;
    const wanted = up.requested_publish_at ? new Date(up.requested_publish_at) : null;
    let note = "";
    if (wanted && !scheduled) {
      note = `You asked to publish on ${esc(fmtWhen(wanted))} but YouTube kept it private without a schedule (unverified API project). Set the schedule in Studio.`;
    } else if (!wanted && up.requested_privacy && up.requested_privacy !== up.privacy) {
      note = `You asked for ${esc(up.requested_privacy)}, YouTube set it to ${esc(up.privacy)} (unverified API project). Change it in Studio.`;
    }
    const label = scheduled ? `scheduled for ${esc(fmtWhen(scheduled))}` : esc(up.privacy);
    els.ytDone.innerHTML = `Uploaded to YouTube (${label}): <a href="${esc(up.url)}" target="_blank" rel="noopener">${esc(up.url)}</a>
      · <a href="${esc(up.studio_url)}" target="_blank" rel="noopener">Open in YouTube Studio</a>
      ${note ? `<br><span class="hint">${note}</span>` : ""}`;
  }

  function replace(short) {
    const i = state.shorts.findIndex((s) => s.filename === short.filename);
    if (i >= 0) state.shorts[i] = short;
  }

  async function load() {
    try {
      state.shorts = (await api("/api/upload/shorts")).body;
      showError("");
    } catch (err) {
      showError(`Could not load the upload list: ${err.message}`);
      return;
    }
    if (!state.shorts.some((s) => s.filename === state.selected)) state.selected = state.shorts[0] ? state.shorts[0].filename : null;
    renderList();
    fill();
  }

  function select(filename) {
    flushSave();
    state.selected = filename;
    renderList();
    fill();
  }

  function setPlatform(platform) {
    state.platform = platform;
    els.tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.platform === platform));
    document.querySelectorAll("#uploadPanel .platform").forEach((p) => (p.hidden = p.dataset.pane !== platform));
  }

  // ---- saving edits --------------------------------------------------------------------

  function scheduleSave() {
    renderFinals();
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(save, 700);
  }

  function flushSave() {
    if (state.saveTimer) {
      clearTimeout(state.saveTimer);
      save();
    }
  }

  async function save() {
    state.saveTimer = null;
    const s = current();
    if (!s) return;
    const texts = readTexts();
    s.texts = texts;
    try {
      const { body } = await api(`/api/upload/shorts/${encodeURIComponent(s.filename)}/texts`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(texts),
      });
      replace(body);
    } catch (err) {
      showError(`Could not save the texts: ${err.message}`);
    }
  }

  // ---- jobs ----------------------------------------------------------------------------

  function jobHtml(job) {
    const pct = job.progress == null ? null : Math.round(job.progress * 100);
    return `<div class="dl-meta">${esc(job.message)}${pct != null ? ` · ${pct}%` : ""}</div>
      <div class="progress${pct == null ? " indeterminate" : ""}"><i style="width:${pct ?? 30}%"></i></div>`;
  }

  async function poll(id, box) {
    box.hidden = false;
    for (;;) {
      const { body } = await api(`/api/create/jobs/${id}`);
      box.innerHTML = jobHtml(body);
      if (body.status === "done") {
        box.hidden = true;
        return body.result;
      }
      if (body.status === "error") {
        box.hidden = true;
        throw new Error(body.error || "Failed");
      }
      await new Promise((r) => setTimeout(r, 1500));
    }
  }

  function updateButtons() {
    const s = current();
    els.gemini.disabled = state.busy.texts || !s;
    els.template.disabled = state.busy.texts || !s;
    const connected = state.yt && state.yt.connected;
    const when = scheduledDate();
    const badTime = scheduling() && (!when || when.getTime() - Date.now() < MIN_SCHEDULE_MS);
    els.ytUpload.disabled = state.busy.upload || !s || !connected || !els.ytTitle.value.trim() || badTime;
    const again = s && s.uploads && s.uploads.youtube;
    els.ytCommentPost.disabled = state.busy.comment || !again || !connected;
    els.ytCommentPost.title = !connected ? "Connect YouTube first" : !again ? "Upload this part first" : "";
    els.ytCommentPost.textContent = state.busy.comment ? "Posting…" : "Post comment on YouTube";
    els.ytUpload.textContent = state.busy.upload ? "Uploading…"
      : !connected ? "Connect YouTube to upload"
      : scheduling() ? `${again ? "Upload again and schedule" : "Upload and schedule"}${when ? ` for ${fmtWhen(when)}` : ""}`
      : again ? "Upload again to YouTube" : "Upload to YouTube as a Short";
  }

  async function improveWithGemini() {
    const s = current();
    flushSave();
    state.busy.texts = true;
    updateButtons();
    try {
      const { body } = await api(`/api/upload/shorts/${encodeURIComponent(s.filename)}/texts/gemini`, { method: "POST" });
      const short = await poll(body.id, els.textsJob);
      replace(short);
      if (state.selected === short.filename) fill();
    } catch (err) {
      showError(`Gemini could not write the texts: ${err.message}`);
    } finally {
      state.busy.texts = false;
      updateButtons();
    }
  }

  async function resetTemplate() {
    const s = current();
    if (!confirm("Replace the texts of all three platforms with the template?")) return;
    clearTimeout(state.saveTimer);
    state.saveTimer = null;
    try {
      const { body } = await api(`/api/upload/shorts/${encodeURIComponent(s.filename)}/texts/template`, { method: "POST" });
      replace(body);
      fill();
    } catch (err) {
      showError(err.message);
    }
  }

  // ---- scheduling ----------------------------------------------------------------------

  const MIN_SCHEDULE_MS = 15 * 60 * 1000;
  const pad = (n) => String(n).padStart(2, "0");
  const toLocalInput = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const fmtWhen = (d) => d.toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const scheduling = () => els.ytPrivacy.value === "schedule";

  function scheduledDate() {
    if (!scheduling() || !els.ytWhen.value) return null;
    const d = new Date(els.ytWhen.value); // datetime-local is parsed as local time
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function at(dayOffset, hour, weekday) {
    const d = new Date();
    d.setDate(d.getDate() + dayOffset);
    if (weekday != null) d.setDate(d.getDate() + ((weekday - d.getDay() + 7) % 7 || 7));
    d.setHours(hour, 0, 0, 0);
    return d;
  }

  function quickTime(key) {
    const map = { "today-18": at(0, 18), "tomorrow-12": at(1, 12), "tomorrow-18": at(1, 18), "weekend-11": at(0, 11, 6) };
    const prev = prevPartTime();
    if (prev) map["after-prev"] = new Date(prev.getTime() + 24 * 3600000);
    let d = map[key];
    if (!d) return;
    if (d.getTime() - Date.now() < MIN_SCHEDULE_MS) d = at(1, d.getHours());
    els.ytWhen.value = toLocalInput(d);
    renderSchedule();
  }

  function renderSchedule() {
    const on = scheduling();
    els.ytWhenField.hidden = !on;
    els.ytQuick.hidden = !on;
    els.ytWhenHint.hidden = !on;
    if (on && !els.ytWhen.value) els.ytWhen.value = toLocalInput(at(1, 18));
    els.ytWhen.min = toLocalInput(new Date(Date.now() + MIN_SCHEDULE_MS));
    if (on) {
      const d = scheduledDate();
      const ms = d ? d.getTime() - Date.now() : 0;
      const hours = ms / 3600000;
      const until = hours < 48 ? `in ${Math.max(1, Math.round(hours))} h` : `in ${Math.round(hours / 24)} days`;
      els.ytWhenHint.innerHTML = !d ? "Pick a date and time."
        : ms < MIN_SCHEDULE_MS ? '<span class="over">Pick a time at least 15 minutes from now.</span>'
        : `Uploads now as <b>private</b>; YouTube makes it <b>public on ${esc(fmtWhen(d))}</b> (${until}).`;
    }
    updateButtons();
  }

  // ---- YouTube -------------------------------------------------------------------------

  async function loadYouTube() {
    els.ytAccount.innerHTML = '<span class="hint">Checking YouTube connection…</span>';
    try {
      state.yt = (await api("/api/upload/youtube/status")).body;
    } catch (err) {
      state.yt = { connected: false, client_secret: true, error: err.message };
    }
    renderAccount();
    updateButtons();
  }

  function renderAccount() {
    const yt = state.yt;
    if (!yt.client_secret) {
      els.ytAccount.innerHTML = '<div class="banner banner-warn">Put your Google OAuth file <b>client_secret.json</b> (Desktop app) in the project folder to enable uploads.</div>';
      return;
    }
    if (yt.connected) {
      const ch = yt.channel || {};
      els.ytAccount.innerHTML = `
        <div class="account">
          ${ch.thumbnail ? `<img src="${esc(ch.thumbnail)}" alt="" referrerpolicy="no-referrer" />` : ""}
          <span>Uploading to <b>${esc(ch.title || "your channel")}</b></span>
          <button type="button" class="dl-copy" id="ytLogout">Disconnect</button>
        </div>
        ${yt.can_comment === false ? '<div class="hint">To post the Part 1/Part 2 comments from here, Disconnect and Connect once more (adds comment permission).</div>' : ""}`;
      return;
    }
    els.ytAccount.innerHTML = `
      <div class="account">
        <span>${yt.error ? esc(yt.error) : "Not connected to YouTube."}</span>
        <button type="button" class="btn-primary" id="ytConnect">Connect YouTube</button>
      </div>
      <div class="hint">Google opens in a new window. If it says the app isn't verified, it's your own app: choose
        <b>Advanced → Go to app</b>. Your Google account must be a test user on the OAuth consent screen.</div>`;
  }

  async function connectYouTube() {
    let popup = window.open("", "trendclip-youtube", "width=520,height=720");
    try {
      const { body } = await api("/api/upload/youtube/connect", { method: "POST" });
      if (popup) popup.location.href = body.auth_url;
      else popup = window.open(body.auth_url, "_blank");
    } catch (err) {
      if (popup) popup.close();
      showError(`Could not start the Google login: ${err.message}`);
      return;
    }
    const timer = setInterval(() => {
      if (!popup || popup.closed) {
        clearInterval(timer);
        loadYouTube();
      }
    }, 1000);
  }

  async function logoutYouTube() {
    if (!confirm("Disconnect YouTube? You can connect again any time.")) return;
    await api("/api/upload/youtube/logout", { method: "POST" });
    loadYouTube();
  }

  async function uploadYouTube() {
    const s = current();
    flushSave();
    if (s.uploads && s.uploads.youtube && !confirm("This Short is already on YouTube. Upload it again as a new video?")) return;
    const t = readTexts();
    const when = scheduledDate();
    const privacy = when ? "private" : els.ytPrivacy.value;
    if (privacy === "public" && !confirm("Publish publicly on YouTube now?")) return;
    if (when && !confirm(`Upload now and publish publicly on ${fmtWhen(when)}?`)) return;
    state.busy.upload = true;
    updateButtons();
    showError("");
    try {
      const { body } = await api(`/api/upload/shorts/${encodeURIComponent(s.filename)}/youtube`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: els.ytTitle.value.trim(),
          description: ytDescription(t),
          tags: t.youtube.tags,
          privacy,
          publish_at: when ? when.toISOString() : null,
          made_for_kids: els.ytKids.checked,
          synthetic_media: els.ytSynthetic.checked,
        }),
      });
      await poll(body.id, els.ytJob);
      await load();
      document.dispatchEvent(new CustomEvent("trendclip:shorts-changed"));
    } catch (err) {
      showError(err.message);
    } finally {
      state.busy.upload = false;
      updateButtons();
    }
  }

  // ---- misc ----------------------------------------------------------------------------

  async function copyText(text, button) {
    try {
      await navigator.clipboard.writeText(text);
      const label = button.textContent;
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = label), 1200);
    } catch (_) {
      window.prompt("Copy:", text);
    }
  }

  async function setPosted(platform, posted) {
    const s = current();
    try {
      const { body } = await api(`/api/upload/shorts/${encodeURIComponent(s.filename)}/posted/${platform}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ posted }),
      });
      replace(body);
      renderList();
      document.dispatchEvent(new CustomEvent("trendclip:posted"));
    } catch (err) {
      showError(err.message);
    }
  }

  async function unready() {
    const s = current();
    flushSave();
    try {
      await api(`/api/shorts/${encodeURIComponent(s.filename)}/ready`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ready: false }),
      });
      state.selected = null;
      await load();
      document.dispatchEvent(new CustomEvent("trendclip:shorts-changed"));
    } catch (err) {
      showError(err.message);
    }
  }

  function bind() {
    els.list.addEventListener("click", (e) => {
      const goto = e.target.closest("[data-goto]");
      if (goto) {
        e.preventDefault();
        return T.showView(goto.dataset.goto);
      }
      const card = e.target.closest("[data-select]");
      if (card) select(card.dataset.select);
    });
    els.tabs.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-platform]");
      if (btn) setPlatform(btn.dataset.platform);
    });
    for (const el of [els.ytTitle, els.ytDesc, els.ytHashtags, els.ytTags, els.igCaption, els.igHashtags,
      els.igMentions, els.ttCaption, els.ttHashtags, els.ttMentions]) {
      el.addEventListener("input", () => {
        scheduleSave();
        updateButtons();
      });
    }
    els.gemini.addEventListener("click", improveWithGemini);
    els.template.addEventListener("click", resetTemplate);
    els.ytUpload.addEventListener("click", uploadYouTube);
    els.ytPrivacy.addEventListener("change", renderSchedule);
    els.ytWhen.addEventListener("input", renderSchedule);
    els.ytQuick.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-quick]");
      if (btn) quickTime(btn.dataset.quick);
    });
    els.ytTz.textContent = `(${Intl.DateTimeFormat().resolvedOptions().timeZone})`;
    els.ytAccount.addEventListener("click", (e) => {
      if (e.target.id === "ytConnect") connectYouTube();
      if (e.target.id === "ytLogout") logoutYouTube();
    });
    els.igCopy.addEventListener("click", () => copyText(els.igFinal.textContent, els.igCopy));
    els.ttCopy.addEventListener("click", () => copyText(els.ttFinal.textContent, els.ttCopy));
    els.ytCommentCopy.addEventListener("click", () => copyText(els.ytComment.value, els.ytCommentCopy));
    els.ytCommentPost.addEventListener("click", postComment);
    els.ytPosted.addEventListener("change", () => setPosted("youtube", els.ytPosted.checked));
    els.igPosted.addEventListener("change", () => setPosted("instagram", els.igPosted.checked));
    els.ttPosted.addEventListener("change", () => setPosted("tiktok", els.ttPosted.checked));
    els.unready.addEventListener("click", unready);
    window.addEventListener("message", (e) => {
      if (e.data === "trendclip:youtube") loadYouTube();
    });
    document.addEventListener("trendclip:ready", load);
    document.addEventListener("trendclip:plan-changed", load);
    document.addEventListener("trendclip:view", (e) => {
      if (e.detail.view === "upload") {
        if (e.detail.clip) {
          flushSave();
          state.selected = e.detail.clip;
        }
        start();
      }
      else {
        els.video.pause();
        flushSave();
      }
    });
  }

  function start() {
    if (!state.started) {
      state.started = true;
      loadYouTube();
    }
    load();
  }

  bind();
  if (!els.view.hidden) start();
  else load(); // fills the nav counter
})();
