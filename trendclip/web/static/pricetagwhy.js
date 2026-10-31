// Pricetag Why tab: the hand-checked scripts (rule check, claims and their sources, "Make the video") and the made
// videos with their description. Schedule, Discord and YouTube come later.
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const els = { error: $("pwError"), hint: $("pwHint"), scripts: $("pwScripts"), videos: $("pwVideos"), count: $("pwVideosCount") };
  const state = { poll: null };
  const showError = (msg) => { els.error.textContent = msg || ""; els.error.hidden = !msg; };
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const fmtWhen = (iso) => (iso || "").replace("T", " ").slice(0, 16);
  const sourceList = (sources) => (sources || []).map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a></li>`).join("");
  const claimList = (claims) => (claims || []).map((c) => `<li>${esc(c.claim)} <span class="hint">[${(c.sources || []).map(esc).join(", ")}]</span></li>`).join("");

  function scriptCard(s, busy) {
    const ok = !s.check.length;
    return `<article class="da-vcard pw-script"><div class="da-vcard-body">
      <b>${esc(s.title)}</b>
      <div class="pills"><span class="pill ${ok ? "good" : "warn"}">${ok ? "✅ Rules OK" : "❌ " + esc(s.check.join("; "))}</span>
        <span class="pill">${esc(s.words)} words</span><span class="pill">${s.sources.length} sources</span><span class="pill">${esc(s.file)}</span></div>
      <div class="da-yt-text">
        <div class="da-yt-row"><span class="da-yt-label">Question</span><span>${esc(s.question)}</span></div>
        <div class="da-yt-row"><span class="da-yt-label">Claims</span><ul class="bw-sources">${claimList(s.claims)}</ul></div>
        <div class="da-yt-row"><span class="da-yt-label">Sources</span><ol class="bw-sources">${sourceList(s.sources)}</ol></div>
      </div>
      <div class="da-vcard-actions da-actions">
        <button type="button" class="btn-primary" data-make="${esc(s.file)}" ${ok && !busy ? "" : "disabled"}>💲 Make the video</button>
      </div>
    </div></article>`;
  }

  function videoCard(v) {
    const src = `/api/pricetagwhy/videos/${encodeURIComponent(v.key)}/video.mp4?t=${encodeURIComponent(v.created_at || "")}`;
    return `<article class="da-vcard">
      <div class="da-vcard-player">${v.has_video ? `<video controls preload="metadata" src="${src}"></video>
        <a class="hint" href="${src}" download="${esc(v.key)}.mp4">Download</a>` : '<span class="hint">no video file</span>'}</div>
      <div class="da-vcard-body">
        <b>${esc(v.title)}</b>
        <div class="pills"><span class="pill">${Math.round(v.duration_seconds || 0)}s</span><span class="pill">${esc(v.words)} words</span>
          <span class="pill">🎙️ ${esc(v.voice || "")}</span>${v.music ? `<span class="pill">🎵 ${esc(v.music)}</span>` : ""}</div>
        <span class="hint">made ${esc(fmtWhen(v.created_at))} · ${esc(v.key)}</span>
        ${v.check && v.check.length ? `<span class="da-note">${esc(v.check.join("; "))}</span>` : ""}
        <div class="da-yt-text">
          <div class="da-yt-row"><span class="da-yt-label">Sources</span><ol class="bw-sources">${sourceList(v.sources)}</ol></div>
          <div class="da-yt-row"><span class="da-yt-label">Description</span><pre class="da-desc">${esc(v.description || "")}</pre></div>
        </div>
      </div>
    </article>`;
  }

  async function load() {
    showError("");
    try {
      const [{ body: st }, { body: sc }, { body: vids }] = await Promise.all([api("/api/pricetagwhy/status"),
        api("/api/pricetagwhy/scripts"), api("/api/pricetagwhy/videos")]);
      const busy = st.making.length > 0;
      els.hint.innerHTML = `${esc(st.handle)} · voice Kokoro ${esc(st.voice)} · hand-checked scripts only for now (no schedule, Discord or upload yet)`
        + (busy ? ` · <b>making ${esc(st.making.join(", "))}…</b>` : "");
      els.scripts.innerHTML = sc.scripts.length ? sc.scripts.map((s) => scriptCard(s, busy)).join("") : '<div class="empty">No scripts in content/pricetagwhy/scripts/</div>';
      els.count.textContent = `· ${vids.videos.length}`;
      els.videos.innerHTML = vids.videos.length ? vids.videos.map(videoCard).join("") : '<div class="empty">No videos yet</div>';
      clearTimeout(state.poll);
      if (busy) state.poll = setTimeout(load, 5000);
    } catch (err) {
      showError(err.message);
    }
  }

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-make]");
    if (btn) {
      btn.disabled = true;
      try { await post("/api/pricetagwhy/make", { file: btn.dataset.make }); } catch (err) { showError(err.message); }
      load();
    }
    if (e.target.closest("#pwRefresh")) load();
  });
  const onShow = () => { if (document.body.dataset.workspace === "pricetagwhy") load(); };
  new MutationObserver(onShow).observe(document.body, { attributes: true, attributeFilter: ["data-workspace"] });
  onShow();
})();
