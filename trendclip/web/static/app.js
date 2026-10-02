(() => {
  "use strict";

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
    topics: $("topics"),
    outliers: $("outliers"),
    videoRows: $("videoRows"),
    search: $("search"),
    statVideos: $("statVideos"),
    statOutliers: $("statOutliers"),
    statTopics: $("statTopics"),
    statFastest: $("statFastest"),
  };

  const state = {
    data: null,
    categoryNames: {},
    sort: { key: "velocity_score", dir: "desc" },
    requestSeq: 0,
  };

  const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
  const whole = new Intl.NumberFormat("en", { maximumFractionDigits: 0 });

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  const fmtAge = (hours) => (hours < 48 ? `${Math.round(hours)}h` : `${Math.round(hours / 24)}d`);

  const fmtDuration = (sec) => {
    if (sec == null || sec === 0) return "";
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = String(sec % 60).padStart(2, "0");
    return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
  };

  const fmtRatio = (r) => (r == null ? "hidden" : r >= 10 ? `${whole.format(r)}x` : `${r.toFixed(1)}x`);

  function badges(v) {
    const out = [];
    if (v.is_outlier) out.push('<span class="pill hot">OUTLIER</span>');
    if (v.live_status === "live") out.push('<span class="pill live">LIVE</span>');
    if (v.duration_seconds && v.duration_seconds <= 180) out.push('<span class="pill">SHORT</span>');
    return out.join("");
  }

  async function api(path) {
    const resp = await fetch(path);
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
    els.loading.hidden = !on;
    els.refresh.disabled = on;
  }

  function showError(message) {
    els.error.hidden = !message;
    els.error.textContent = message || "";
  }

  function syncUrl() {
    const params = new URLSearchParams({
      region: els.region.value,
      category: els.category.value,
      max_results: els.maxResults.value,
      top: els.top.value,
    });
    history.replaceState(null, "", `?${params}`);
  }

  function ensureOption(select, value, label) {
    if (![...select.options].some((o) => o.value === value)) {
      select.add(new Option(label ?? value, value));
    }
    select.value = value;
  }

  async function loadCategories(preferred) {
    try {
      const { body } = await api(`/api/categories?region=${encodeURIComponent(els.region.value)}`);
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
    syncUrl();
    showError("");
    setLoading(true);
    const seq = ++state.requestSeq;
    const params = new URLSearchParams({
      region: els.region.value,
      category: els.category.value,
      max_results: els.maxResults.value,
      top: els.top.value,
    });
    if (refresh) params.set("refresh", "true");
    try {
      const { body, cache } = await api(`/api/trends?${params}`);
      if (seq !== state.requestSeq) return;
      state.data = body;
      render(cache);
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
    const time = new Date(d.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    els.status.innerHTML = `
      <span>${esc(d.regions.join(", "))} · ${esc(catLabel)}</span>
      <span class="pill ${cache === "HIT" ? "" : "good"}">${cache === "HIT" ? "cached" : "fresh"} · ${esc(time)}</span>`;

    if (d.warnings.length) {
      els.warnings.hidden = false;
      els.warnings.innerHTML = `<strong>Heads up</strong><ul>${d.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`;
    } else {
      els.warnings.hidden = true;
    }

    const fastest = d.videos.reduce((m, v) => Math.max(m, v.views_per_hour), 0);
    els.statVideos.textContent = whole.format(d.stats.videos_fetched);
    els.statOutliers.textContent = whole.format(d.stats.outlier_videos);
    els.statTopics.textContent = whole.format(d.candidates.length);
    els.statFastest.textContent = fastest ? compact.format(fastest) : "–";

    renderTopics(d.candidates);
    renderOutliers(d.outlier_videos);
    renderTable();
  }

  function renderTopics(topics) {
    if (!topics.length) {
      els.topics.innerHTML = '<li class="empty">No topics found for this selection.</li>';
      return;
    }
    const maxScore = Math.max(...topics.map((t) => t.score));
    els.topics.innerHTML = topics
      .map((t, i) => {
        const extra = t.keywords.slice(1).map((k) => `<span class="chip">${esc(k)}</span>`).join("");
        const vids = t.videos
          .map(
            (v) => `
            <a class="mini-video" href="${esc(v.url)}" target="_blank" rel="noopener">
              <img src="${esc(v.thumbnail_url)}" alt="" loading="lazy" />
              <div>
                <div class="t">${esc(v.title)}</div>
                <div class="m">${esc(v.channel_title)} · ${compact.format(v.views_per_hour)} views/h · ${compact.format(v.views)} views</div>
              </div>
            </a>`
          )
          .join("");
        return `
          <li class="topic">
            <div class="topic-head" role="button" tabindex="0" aria-expanded="false">
              <span class="rank">${i + 1}</span>
              <div>
                <div class="topic-name">${esc(t.topic)}</div>
                <div class="topic-meta">${t.video_count} video${t.video_count === 1 ? "" : "s"} · ${compact.format(t.total_views)} views · peak ${compact.format(t.max_views_per_hour)}/h</div>
                ${extra ? `<div class="chips">${extra}</div>` : ""}
              </div>
              <div class="score">
                <strong>${t.score.toFixed(1)}</strong>
                <div class="bar"><i style="width:${Math.max(4, (t.score / maxScore) * 100)}%"></i></div>
              </div>
            </div>
            <div class="topic-videos">${vids}</div>
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
      .map(
        (v) => `
        <a class="vcard" href="${esc(v.url)}" target="_blank" rel="noopener">
          <div class="thumb">
            <img src="${esc(v.thumbnail_url)}" alt="" loading="lazy" />
            <div class="badges">${v.live_status === "live" ? '<span class="pill live">LIVE</span>' : ""}</div>
            ${fmtDuration(v.duration_seconds) ? `<span class="dur">${fmtDuration(v.duration_seconds)}</span>` : ""}
          </div>
          <div class="vbody">
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

  function renderTable() {
    if (!state.data) return;
    const q = els.search.value.trim().toLowerCase();
    const { key, dir } = state.sort;
    const rows = state.data.videos
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
          <td class="num"><strong>${v.velocity_score.toFixed(2)}</strong></td>
          <td class="num">${compact.format(v.views_per_hour)}</td>
          <td class="num">${compact.format(v.views)}</td>
          <td class="num ${v.outlier_ratio == null ? "dim" : ""}">${fmtRatio(v.outlier_ratio)}</td>
          <td class="num">${(v.engagement_rate * 100).toFixed(1)}%</td>
          <td class="num">${fmtAge(v.age_hours)}</td>
        </tr>`
      )
      .join("");
  }

  function bindEvents() {
    els.region.addEventListener("change", async () => {
      const current = els.category.value;
      await loadCategories(current);
      loadTrends();
    });
    els.category.addEventListener("change", () => loadTrends());
    els.maxResults.addEventListener("change", () => loadTrends());
    els.top.addEventListener("change", () => loadTrends());
    els.refresh.addEventListener("click", () => loadTrends(true));
    els.search.addEventListener("input", renderTable);

    document.querySelectorAll(".videos th.sortable").forEach((th) =>
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        state.sort = { key, dir: state.sort.key === key && state.sort.dir === "desc" ? "asc" : "desc" };
        renderTable();
      })
    );

    const toggleTopic = (head) => {
      const li = head.closest(".topic");
      const open = li.classList.toggle("open");
      head.setAttribute("aria-expanded", String(open));
    };
    els.topics.addEventListener("click", (e) => {
      const head = e.target.closest(".topic-head");
      if (head) toggleTopic(head);
    });
    els.topics.addEventListener("keydown", (e) => {
      const head = e.target.closest(".topic-head");
      if (head && (e.key === "Enter" || e.key === " ")) {
        e.preventDefault();
        toggleTopic(head);
      }
    });
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

    for (const [code, name] of Object.entries(cfg.regions)) els.region.add(new Option(`${name} (${code})`, code));
    ensureOption(els.region, (params.get("region") || d.region).toUpperCase());
    ensureOption(els.maxResults, params.get("max_results") || String(d.max_results));
    ensureOption(els.top, params.get("top") || String(d.top));

    await loadCategories(params.get("category") || d.category);
    loadTrends();
  }

  init();
})();
