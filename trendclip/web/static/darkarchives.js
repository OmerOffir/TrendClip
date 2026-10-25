// DarkArchives tab: the story bank (Reddit / Wikipedia / Gemini), reading, status and fetching.
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const els = {
    error: $("daError"), sources: $("daSources"), list: $("daList"), detail: $("daDetail"), shown: $("daShown"),
    source: $("daSource"), status: $("daStatus"), pillar: $("daPillar"), query: $("daQuery"), fetch: $("daFetch"),
    fetchStatus: $("daFetchStatus"), navCount: $("daNavCount"), mix: $("daMix"),
    n: { reddit: $("daNReddit"), wikipedia: $("daNWiki"), gemini: $("daNGemini"), rss: $("daNRss") },
  };
  const SOURCES = { reddit: "Reddit", wikipedia: "Wikipedia", gemini: "Gemini", rss: "RSS feeds" };
  const PILLARS = { mystery: "🕵️ Mystery", history: "🏚️ Creepy history", outrage: "⚠️ Disturbing" };
  const pillarPill = (p) => (p ? `<span class="pill da-pillar-${p}">${PILLARS[p] || p}</span>` : "");
  const STATUSES = ["NEW", "PROCESSING", "SCRIPT_READY", "USED", "REJECTED"];
  const LABELS = { NEW: "Back to new", PROCESSING: "Processing", SCRIPT_READY: "Script ready", USED: "Used", REJECTED: "Rejected" };
  const state = { selected: null, nextId: null, loaded: false, polling: null, searchTimer: null };

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
            ${s.status !== "NEW" ? `<span class="pill">${s.status}</span>` : ""} ${s.chars.toLocaleString()} chars</small>
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
      renderSources(body.sources, body.counts);
      if (body.mix) renderMix(body.mix, body.pillar_labels || {}, body.next_pillar);
      renderList(body.stories);
      showError("");
      state.loaded = true;
    } catch (err) {
      showError(err.message === "Not Found"
        ? "The dashboard server is older than this page: restart the dashboard to load the DarkArchives story bank."
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
        <details class="da-raw" ${s.status === "SCRIPT_READY" ? "" : "open"}><summary>Raw story</summary>
          <div class="da-text">${esc(s.raw_text).replace(/\n/g, "<br>")}</div></details>`;
      loadScript(s.id);
      api("/api/darkarchives/script-job").then(({ body }) => {
        showScriptJob(body);
        if (body.running && body.story_id === s.id) setTimeout(pollScript, 2000);
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
        </div>`;
    } catch (_) {
      box.innerHTML = "";
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

  els.list.addEventListener("click", (e) => {
    const item = e.target.closest(".da-item");
    if (item) open(Number(item.dataset.id));
  });
  els.detail.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-status]");
    if (btn) setStatus(btn.dataset.status);
    if (e.target.closest("#daWrite")) writeScript();
  });
  els.source.addEventListener("change", load);
  els.status.addEventListener("change", load);
  els.pillar.addEventListener("change", load);
  els.query.addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(load, 250);
  });
  els.fetch.addEventListener("click", startFetch);

  const onShow = () => {
    if (document.body.dataset.workspace !== "darkarchives") return;
    if (!state.loaded) load();
    if (!state.polling) poll();
  };
  new MutationObserver(onShow).observe(document.body, { attributes: true, attributeFilter: ["data-workspace"] });
  onShow();
})();
