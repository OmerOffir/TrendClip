(() => {
  "use strict";

  const T = window.TrendClip;
  const { api, esc } = T;
  const $ = (id) => document.getElementById(id);
  const els = {
    view: $("viewInsights"),
    error: $("inError"),
    notes: $("inNotes"),
    days: $("inDays"),
    refresh: $("inRefresh"),
    stats: $("inStats"),
    dimensions: $("inDimensions"),
    hooks: $("inHooks"),
    picker: $("inPicker"),
    pickerHint: $("inPickerHint"),
    rows: $("inRows"),
    madeHere: $("inMadeHere"),
  };

  const state = { report: null, loaded: false, loading: false };
  const fmt = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
  const n = (v) => (v === null || v === undefined ? "–" : fmt.format(v));
  const pct = (v) => (v === null || v === undefined ? "–" : `${Math.round(v)}%`);
  const MODE = { story: "Story", math: "Math", riddle: "Riddle", trivia: "Trivia", paradox: "Paradox", movie: "Movie", other: "Other" };
  const HOOK = { dare: "Dare", question: "Question", number: "Number", quote: "Quote", command: "Command", first_person: "First person", claim: "Claim", "": "–" };

  function showError(text) {
    els.error.textContent = text;
    els.error.hidden = !text;
  }

  function vs(ratio) {
    if (ratio === null || ratio === undefined) return "";
    if (Math.abs(ratio - 1) < 0.15) return '<span class="in-vs">≈ average</span>';
    const cls = ratio > 1 ? "up" : "down";
    return `<span class="in-vs ${cls}">${ratio.toFixed(1)}× channel</span>`;
  }

  function renderStats(r) {
    const latest = r.latest_snapshot ? `read ${esc(r.latest_snapshot)}` : "no numbers read yet";
    els.stats.innerHTML = `
      <div class="stat"><div class="stat-label">Videos in window</div><div class="stat-value">${r.videos}</div>
        <div class="stat-sub">${r.ranked} ranked (≥ ${r.min_age_days} days old)</div></div>
      <div class="stat"><div class="stat-label">Views</div><div class="stat-value accent">${n(r.total_views)}</div>
        <div class="stat-sub">${latest}</div></div>
      <div class="stat"><div class="stat-label">Median views / video</div><div class="stat-value">${n(r.median_views)}</div>
        <div class="stat-sub">groups are compared to this</div></div>
      <div class="stat"><div class="stat-label">Retention known</div><div class="stat-value">${r.with_retention}</div>
        <div class="stat-sub">${r.with_retention ? "average view % from YouTube Analytics" : "needs the analytics permission"}</div></div>`;
  }

  function renderNotes(r) {
    const yt = r.youtube || {};
    const notes = [...(r.notes || [])];
    if (!yt.connected) notes.unshift("YouTube is not connected: Upload tab → Connect YouTube. The insights use the channel's own login.");
    const collected = r.collected;
    if (collected) {
      notes.unshift(`Read YouTube: ${collected.videos} videos tracked (${collected.from_sidecars} made here), ${collected.snapshots} snapshots, ${collected.with_retention} with retention.`);
      for (const w of collected.warnings || []) notes.push(w);
    }
    els.notes.innerHTML = notes.map((t, i) => `<div class="banner ${i === 0 && collected ? "banner-ok" : "banner-warn"}">${esc(t)}</div>`).join("");
  }

  function renderDimensions(r) {
    if (!r.dimensions.length) {
      els.dimensions.innerHTML = `<section class="card"><div class="in-empty">Nothing to rank yet. Upload a few videos, then press <b>Read YouTube now</b> (the Discord bot also reads them every morning).</div></section>`;
      return;
    }
    els.dimensions.innerHTML = r.dimensions.map((d) => {
      const top = Math.max(...d.groups.map((g) => g.median_views), 1);
      return `<section class="card">
        <div class="card-head"><div><h2>${esc(d.label)}</h2><span class="hint">by median views, ${d.groups.reduce((a, g) => a + g.n, 0)} videos</span></div></div>
        <div class="in-groups">${d.groups.map((g) => `
          <div class="in-group">
            <div class="in-group-name"><span>${esc(g.label)}</span><small>${g.n} video${g.n === 1 ? "" : "s"}</small></div>
            <div class="metric"><b>${n(g.median_views)}</b><span>median views</span></div>
            <div class="share-bar"><i style="width:${Math.max(3, (g.median_views / top) * 100)}%"></i></div>
            <div class="in-group-meta">
              ${vs(g.vs_channel)}
              <span>${n(g.views_per_day)} views/day</span>
              ${g.avg_view_pct !== null ? `<span>${pct(g.avg_view_pct)} watched</span>` : ""}
              ${g.likes_per_k !== null ? `<span>${g.likes_per_k} likes/1k</span>` : ""}
              ${g.comments_per_k !== null ? `<span>${g.comments_per_k} comments/1k</span>` : ""}
              ${g.best_title ? `<span title="Best video of this group">★ ${esc(g.best_title.slice(0, 50))}</span>` : ""}
            </div>
          </div>`).join("")}</div>
      </section>`;
    }).join("");
  }

  function renderHooks(r) {
    if (!r.top_hooks.length) {
      els.hooks.innerHTML = `<div class="in-empty">Hooks appear here for videos made with TrendClip once they have views.</div>`;
      return;
    }
    els.hooks.innerHTML = r.top_hooks.map((v) => `
      <div class="in-hook">
        <b>${n(v.views)}</b>
        <div><q>${esc(v.hook)}</q>
          <small>${esc(HOOK[v.hook_style] || v.hook_style)} · ${esc(MODE[v.mode] || v.mode)} · ${esc(v.game || "")}${v.avg_view_pct !== null ? ` · ${pct(v.avg_view_pct)} watched` : ""} · <a href="${esc(v.url)}" target="_blank" rel="noopener">${esc(v.title.slice(0, 60))}</a></small>
        </div>
      </div>`).join("");
  }

  function renderRows(r) {
    const rows = r.rows.filter((v) => !els.madeHere.checked || v.filename);
    if (!rows.length) {
      els.rows.innerHTML = `<tr><td colspan="12" class="in-empty">No videos in this window.</td></tr>`;
      return;
    }
    els.rows.innerHTML = rows.map((v) => {
      const perK = (x) => (x === null || x === undefined || !v.views ? "–" : (x / v.views * 1000).toFixed(1));
      const d17 = v.day1_views === null && v.day7_views === null ? "–" : `${n(v.day1_views)} → ${n(v.day7_views)}`;
      return `<tr class="${v.ranked ? "" : "young"}">
        <td><a href="${esc(v.url)}" target="_blank" rel="noopener">${esc(v.title.slice(0, 70) || v.video_id)}</a>
          <div class="dim">${v.published_at ? esc(v.published_at.slice(0, 10)) : ""}${v.ranked ? "" : " · too new to rank"}</div></td>
        <td class="dim-cell">${esc(MODE[v.mode] || v.mode)}${v.format === "multi" ? " · part" : v.format === "long" ? " · 16:9" : ""}</td>
        <td class="dim-cell">${esc(v.game || "–")}</td>
        <td class="dim-cell">${esc(HOOK[v.hook_style] || v.hook_style || "–")}</td>
        <td class="dim-cell">${esc(v.length_bucket || "–")}</td>
        <td class="dim-cell">${esc(v.slot || "–")}</td>
        <td class="metric"><b>${n(v.views)}</b></td>
        <td class="metric">${n(v.views_per_day)}</td>
        <td class="metric">${pct(v.avg_view_pct)}</td>
        <td class="metric">${perK(v.likes)}</td>
        <td class="metric">${perK(v.comments)}</td>
        <td class="metric">${d17}</td>
      </tr>`;
    }).join("");
  }

  function renderPicker(p) {
    const DIM = { mode: "Kind of video", game: "Game", length_bucket: "Story length" };
    const LABEL = { story: "Story", math: "Math challenge", riddle: "Riddle", trivia: "Trivia trap", paradox: "Paradox trap", movie: "Movie quiz" };
    els.pickerHint.textContent = p.enabled
      ? `Chances from the last ${p.days} days (${p.videos} ranked videos); ${Math.round(p.explore * 100)}% of picks are random so untried ideas still get a turn.`
      : "AUTO_PICK is off: the bot uses its fixed weights (math most often). Set AUTO_PICK=true to use these chances.";
    els.picker.innerHTML = Object.entries(DIM).map(([key, title]) => {
      const rows = p[key] || [];
      const total = rows.reduce((a, w) => a + w.weight, 0) || 1;
      return `<section class="card">
        <div class="card-head"><div><h2>${esc(title)}</h2></div></div>
        <div class="in-groups">${rows.map((w) => `
          <div class="in-group">
            <div class="in-group-name"><span>${esc(LABEL[w.key] || w.key)}</span><small>${w.n ? `${w.n} video${w.n === 1 ? "" : "s"}` : "no numbers yet"}</small></div>
            <div class="metric"><b>${Math.round((w.weight / total) * 100)}%</b><span>chance</span></div>
            <div class="share-bar"><i style="width:${Math.max(3, (w.weight / total) * 100)}%"></i></div>
            <div class="in-group-meta">${vs(w.vs_channel)}${w.avg_view_pct !== null && w.avg_view_pct !== undefined ? `<span>${pct(w.avg_view_pct)} watched</span>` : ""}</div>
          </div>`).join("")}</div>
      </section>`;
    }).join("");
  }

  async function loadPicker() {
    try {
      const { body } = await api("/api/insights/picker");
      renderPicker(body);
    } catch (err) {
      els.picker.innerHTML = `<div class="in-empty">${esc(err.message || String(err))}</div>`;
    }
  }

  function render(r) {
    state.report = r;
    loadPicker();

    renderNotes(r);
    renderStats(r);
    renderDimensions(r);
    renderHooks(r);
    renderRows(r);
  }

  async function load(refresh) {
    if (state.loading) return;
    state.loading = true;
    showError("");
    els.refresh.disabled = true;
    els.refresh.textContent = refresh ? "Reading YouTube…" : "↻ Read YouTube now";
    try {
      const path = `/api/insights${refresh ? "/refresh" : ""}?days=${encodeURIComponent(els.days.value)}`;
      const { body } = await api(path, refresh ? { method: "POST" } : undefined);
      render(body);
      state.loaded = true;
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      state.loading = false;
      els.refresh.disabled = false;
      els.refresh.textContent = "↻ Read YouTube now";
    }
  }

  els.refresh.addEventListener("click", () => load(true));
  els.days.addEventListener("change", () => load(false));
  els.madeHere.addEventListener("change", () => state.report && renderRows(state.report));
  document.addEventListener("trendclip:view", (e) => {
    if (e.detail.view === "insights" && !state.loaded) load(false);
  });
  if (location.hash === "#insights") load(false);
})();
