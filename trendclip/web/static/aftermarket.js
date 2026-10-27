// After Market tab: today's jobs, "make the video now", the last session's facts, the videos and their uploads.
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const els = {
    error: $("amError"), hint: $("amAutoHint"), yt: $("amYouTube"), jobs: $("amJobs"), facts: $("amFacts"),
    factsWhen: $("amFactsWhen"), videos: $("amVideos"), count: $("amVideosCount"), run: $("amRun"), runKind: $("amRunKind"),
    runUpload: $("amRunUpload"), runTemplate: $("amRunTemplate"), runStatus: $("amRunStatus"),
    plan: $("amPlan"), planHint: $("amPlanHint"), navVideos: $("amNavVideos"), navPlan: $("amNavPlan"),
  };
  const state = { yt: null, polling: null, status: null, section: "today" };
  const KIND = { premarket: "Pre-market", close: "Close", weekend: "Weekend" };
  const showError = (msg) => { els.error.textContent = msg; els.error.hidden = !msg; };
  const pct = (v, d = 2) => (v === null || v === undefined ? "" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
  const cls = (v) => (v > 0 ? "am-up" : v < 0 ? "am-down" : "");
  const money = (v) => (v === null || v === undefined ? "" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }));
  const fmtWhen = (iso) => (iso || "").replace("T", " ").slice(0, 16);

  function renderStatus(s) {
    state.status = s;
    els.hint.textContent = `${s.auto_post ? "Runs by itself" : "Automation is off (AM_AUTO_POST)"} · slots ${s.slots.join(" and ")} ${s.timezone}` +
      ` · the pre-market job starts ${s.lead_minutes} min early · uploads as ${s.privacy} · now it is ${fmtWhen(s.now).slice(11)} (${KIND[s.now_kind] || s.now_kind})`;
    els.jobs.innerHTML = s.today.map((j) => {
      const next = s.next_job && s.next_job.key === j.key;
      const past = !next && j.start < s.now;
      return `<div class="am-job${next ? " next" : past ? " past" : ""}">
        <b>${j.slot} · ${esc(j.focus || KIND[j.kind] || j.kind)}</b>
        <span>starts ${fmtWhen(j.start).slice(11)}${j.start !== j.post_at ? `, on YouTube at ${fmtWhen(j.post_at).slice(11)}` : ", uploads when done"}</span>
        <span class="hint">${next ? "next" : past ? "started" : "today"}</span>
      </div>`;
    }).join("") + (s.next_job && !s.today.some((j) => j.key === s.next_job.key)
      ? `<div class="am-job next"><b>Next: ${esc(s.next_job.day)} ${s.next_job.slot}</b><span>${esc(s.next_job.focus || KIND[s.next_job.kind])}</span><span class="hint">starts ${fmtWhen(s.next_job.start).slice(11)}</span></div>` : "");
    if (!s.folder) showError("channels/aftermarket/channel.env is missing on this machine (copy channels/channel.env.example; set CHANNEL_HANDLE and DISCORD_CHANNEL_ID).");
    else if (!s.discord) showError("DISCORD_CHANNEL_ID is empty in channels/aftermarket/channel.env: the bot has nowhere to post the videos and the veto button.");
    else showError("");
    renderFacts(s.facts);
  }

  function renderFacts(f) {
    if (!f) {
      els.factsWhen.textContent = "";
      els.facts.innerHTML = '<div class="empty">No session read yet.</div>';
      return;
    }
    els.factsWhen.textContent = `· ${esc(f.key)} · ${esc(f.ny_time || "")}${f.focus ? ` · ${esc(f.focus)}` : ""}`;
    const row = (name, price, change, extra = "") => `<tr><td>${esc(name)}</td><td class="num">${money(price)}</td><td class="num ${cls(change)}">${pct(change)}</td>${extra}</tr>`;
    const board = [...(f.futures || []), ...(f.indices || [])];
    const movers = (f.movers || []).map((m) => {
      const ext = m.extended_change_pct !== null && m.extended_change_pct !== undefined ? `<td class="num ${cls(m.extended_change_pct)}">${esc(m.session || "")} ${pct(m.extended_change_pct)}</td>` : "<td></td>";
      const why = m.why && m.why.length ? `<span class="why">${esc(m.why[0].title)}</span>` : "";
      return `<tr><td>${esc(m.symbol)} <span class="hint">${esc(m.name || "")}</span>${why}</td><td class="num">${money(m.price)}</td><td class="num ${cls(m.change_pct)}">${pct(m.change_pct)}</td>${ext}</tr>`;
    }).join("");
    const crypto = (f.crypto || []).map((c) => row(c.symbol, c.price, c.change_24h_pct)).join("");
    const due = (f.earnings_today || []).map((e) => `<li>${esc(e.symbol)} <span class="hint">${esc(e.name || "")} · ${esc(e.time || "")}</span></li>`).join("");
    const macro = (f.macro || []).map((e) => `<li>${esc(e.time_et || "")} ET · ${esc(e.title)}${e.forecast ? ` <span class="hint">(f ${esc(e.forecast)}${e.previous ? `, prev ${esc(e.previous)}` : ""})</span>` : ""}</li>`).join("");
    const news = (f.news || []).map((n) => `<li><a href="${esc(n.url || "#")}" target="_blank" rel="noopener">${esc(n.title)}</a></li>`).join("");
    els.facts.innerHTML = `
      <div><h4>Board</h4><table>${board.map((q) => row(q.name, q.price, q.change_pct)).join("") || "<tr><td class='hint'>none</td></tr>"}</table></div>
      <div><h4>Movers</h4><table>${movers || "<tr><td class='hint'>none</td></tr>"}</table></div>
      <div><h4>Crypto</h4><table>${crypto || "<tr><td class='hint'>none</td></tr>"}</table>
        ${f.problems && f.problems.length ? `<h4 style="margin-top:10px">Sources that failed</h4><ul>${f.problems.map((p) => `<li class="am-check">${esc(p)}</li>`).join("")}</ul>` : ""}</div>
      <div><h4>Earnings today</h4><ul>${due || "<li class='hint'>none</li>"}</ul><h4 style="margin-top:10px">Macro</h4><ul>${macro || "<li class='hint'>none</li>"}</ul></div>
      <div><h4>Headlines</h4><ul>${news || "<li class='hint'>none</li>"}</ul></div>`;
  }

  async function renderYouTube() {
    try {
      const { body: yt } = await api("/api/aftermarket/youtube/status");
      state.yt = yt;
      const who = yt.channel ? `${esc(yt.channel.title)}${yt.handle ? ` (${esc(yt.handle)})` : ""}` : esc(yt.handle || "After Market");
      els.yt.innerHTML = `<b>YouTube</b>
        ${yt.connected
          ? `<span class="pill good">Connected: ${who}</span><button type="button" class="btn-secondary" id="amYtLogout">Disconnect</button>
             <span class="hint">Each slot's video goes out by itself (News &amp; Politics, as a Short)</span>`
          : `<span class="pill">Not connected</span><button type="button" class="btn-primary" id="amYtConnect" ${yt.client_secret ? "" : "disabled"}>Connect YouTube</button>
             <span class="hint">Google opens in a new window: pick the <b>After Market</b> channel there. Until then the videos are made but nothing is uploaded</span>`}
        ${!yt.client_secret ? '<span class="da-note">client_secret.json is missing (Upload tab → Connect YouTube explains)</span>' : ""}
        ${yt.error ? `<span class="da-note">${esc(yt.error)}</span>` : ""}`;
    } catch (err) {
      els.yt.innerHTML = `<span class="da-note">YouTube: ${esc(err.message)}</span>`;
    }
    els.videos.querySelectorAll("button[data-upload]").forEach((b) => { b.disabled = !(state.yt && state.yt.connected); });
  }

  async function connectYouTube() {
    let popup = window.open("", "trendclip-youtube", "width=520,height=720");
    try {
      const { body } = await api("/api/aftermarket/youtube/connect", { method: "POST" });
      if (popup) popup.location = body.auth_url; else window.open(body.auth_url, "_blank");
    } catch (err) {
      if (popup) popup.close();
      showError(err.message);
    }
  }
  els.yt.addEventListener("click", (e) => {
    if (e.target.closest("#amYtConnect")) connectYouTube();
    if (e.target.closest("#amYtLogout")) api("/api/aftermarket/youtube/logout", { method: "POST" }).then(renderYouTube).catch((err) => showError(err.message));
  });
  window.addEventListener("message", (e) => { if (e.data === "trendclip:youtube") renderYouTube(); });

  // ---- Today | Videos | Plan
  function showSection(name) {
    state.section = name;
    document.querySelectorAll("#amNav [data-am-view]").forEach((b) => {
      b.classList.toggle("active", b.dataset.amView === name);
      b.setAttribute("aria-selected", b.dataset.amView === name ? "true" : "false");
    });
    document.querySelectorAll("[data-am-panel]").forEach((p) => { p.hidden = p.dataset.amPanel !== name; });
    if (name === "videos") loadVideos();
    if (name === "plan") loadPlan();
  }
  $("amNav").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-am-view]");
    if (btn) showSection(btn.dataset.amView);
  });

  // ---- Plan: the week's slots, each with its brief's state
  const STATE = {
    posted: ["good", "▶️ on YouTube"], made: ["warn", "made, not on YouTube"], blocked: ["warn", "failed the check"],
    vetoed: ["", "🚫 held back"], missed: ["warn", "⚠️ not made"], due: ["", "⏳ being made"], waiting: ["", "waits for its time"],
  };
  function renderPlan(d) {
    els.navPlan.textContent = d.pending;
    els.navPlan.hidden = !d.pending;
    els.planHint.textContent = d.auto_post
      ? `Two briefs a trading day at ${d.slots.join(" and ")} ${d.timezone}: the pre-market one starts ${d.lead_minutes} min early and goes on YouTube at its slot, the close one uploads as soon as it is rendered; weekends get one review. Uploads as ${d.privacy}.`
      : "Automatic posting is off (AM_AUTO_POST): each brief is made at its slot, upload it yourself from Videos.";
    els.plan.innerHTML = d.days.map((day) => `
      <div class="da-plan-day${day.today ? " today" : ""}${day.day < d.now.slice(0, 10) ? " past" : ""}">
        <div class="da-plan-date"><b>${esc(day.weekday)}</b><small>${esc(day.day)}${day.trading ? "" : " · no trading"}</small></div>
        <div class="da-plan-slots">${day.jobs.map((j) => {
          const making = d.making === j.key;
          const [cls, label] = making ? ["", "⏳ being made now"] : (STATE[j.state] || ["", j.state]);
          const v = j.video;
          const when = j.start !== j.post_at ? `starts ${fmtWhen(j.start).slice(11)} · YouTube ${fmtWhen(j.post_at).slice(11)}` : `starts ${fmtWhen(j.start).slice(11)} · uploads when done`;
          const canMake = !making && !v && j.state !== "missed" && day.today;
          const canUpload = v && j.state === "made";
          return `<div class="da-plan-slot${j.state === "posted" ? " done" : j.state === "missed" || j.state === "blocked" ? " late" : ""}">
            <span class="time">${esc(j.slot)}</span>
            <span class="title">${esc(j.focus ? j.focus[0].toUpperCase() + j.focus.slice(1) : KIND[j.kind] || j.kind)}${v ? ` · 🎬 ${esc(v.title)}` : ""} <span class="hint">${when}</span></span>
            <span class="pill ${cls}">${label}</span>
            ${v && v.youtube && v.youtube.url ? `<a class="hint" href="${esc(v.youtube.url)}" target="_blank" rel="noopener">open ↗</a>` : ""}
            ${v && v.check && v.check.length ? `<span class="am-check">${esc(v.check.join("; "))}</span>` : ""}
            <span class="spacer"></span>
            ${v ? `<button type="button" class="btn-ghost" data-watch="${esc(v.key)}">Watch</button>` : ""}
            ${canUpload ? `<button type="button" class="btn-secondary" data-plan-upload="${esc(v.key)}" ${state.yt && state.yt.connected ? "" : "disabled"}>⬆️ Upload now</button>` : ""}
            ${canMake ? `<button type="button" class="btn-secondary" data-make="${esc(j.kind)}" title="Make this brief now (and upload it, when posting is on)">🎬 Make now</button>` : ""}
          </div>`;
        }).join("") || '<div class="da-plan-slot"><span class="hint">nothing scheduled</span></div>'}</div>
      </div>`).join("");
  }
  async function loadPlan() {
    try {
      const { body } = await api("/api/aftermarket/plan");
      renderPlan(body);
    } catch (err) {
      els.plan.innerHTML = `<div class="da-note">Couldn't load the plan: ${esc(err.message)}</div>`;
    }
  }
  els.plan.addEventListener("click", async (e) => {
    const watch = e.target.closest("button[data-watch]");
    const make = e.target.closest("button[data-make]");
    const upl = e.target.closest("button[data-plan-upload]");
    if (watch) {
      showSection("videos");
      await loadVideos();
      const card = els.videos.querySelector(`[data-key="${watch.dataset.watch}"]`);
      if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    if (make) {
      make.disabled = true;
      try {
        await api("/api/aftermarket/run", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind: make.dataset.make, upload: true, template: false }) });
        if (!state.polling) pollRun();
        loadPlan();
      } catch (err) {
        make.disabled = false;
        showError(err.message);
      }
    }
    if (upl) {
      upl.disabled = true;
      try {
        await api(`/api/aftermarket/videos/${upl.dataset.planUpload}/upload`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        loadPlan();
      } catch (err) {
        upl.disabled = false;
        showError(err.message);
      }
    }
  });
  $("amPlanRefresh").addEventListener("click", loadPlan);
  $("amVideosRefresh").addEventListener("click", loadVideos);

  async function loadVideos() {
    try {
      const { body } = await api("/api/aftermarket/videos");
      els.count.textContent = body.videos.length ? `· ${body.videos.length}` : "";
      els.navVideos.textContent = body.videos.length;
      els.navVideos.hidden = !body.videos.length;
      if (!body.videos.length) {
        els.videos.innerHTML = '<div class="empty">No video yet. The bot makes one at each slot; <b>Make the video now</b> makes one right away.</div>';
        return;
      }
      els.videos.innerHTML = body.videos.map((v) => {
        const up = v.uploads && v.uploads.youtube;
        const yt = v.youtube_texts || {};
        const src = `/api/aftermarket/videos/${esc(v.key)}/video?t=${encodeURIComponent(v.created_at || "")}`;
        const check = v.check && v.check.length;
        return `<article class="da-vcard" data-key="${esc(v.key)}">
          <div class="da-vcard-player">
            <video controls preload="metadata" src="${src}"></video>
            <a class="hint" href="${src}" download="aftermarket_${esc(v.key)}.mp4">Download</a>
          </div>
          <div class="da-vcard-body">
            <b>${esc(v.title)}</b>
            <div class="pills"><span class="pill">${esc(v.key)}</span><span class="pill">${v.duration_seconds}s</span><span class="pill">${v.words} words</span><span class="pill">script: ${esc(v.writer || "?")}</span>
              ${up ? `<span class="pill good">▶️ YouTube · ${esc(up.privacy)}</span>` : check ? '<span class="pill warn">failed the check</span>' : v.veto ? '<span class="pill warn">vetoed</span>' : '<span class="pill">not on YouTube</span>'}</div>
            ${check ? `<span class="am-check">Not uploaded: ${esc(v.check.join("; "))}</span>` : ""}
            <span class="hint">made ${esc(fmtWhen(v.created_at))} in ${v.seconds_to_make || "?"} s · pictures: ${esc(v.pictures || "")}${v.music ? ` · 🎵 ${esc(v.music.title)}` : ""}${v.job ? ` · for ${esc(v.job.slot)}` : ""}</span>
            ${up ? `<span class="hint"><a href="${esc(up.url)}" target="_blank" rel="noopener">${esc(up.url)}</a>${up.studio_url ? ` · <a href="${esc(up.studio_url)}" target="_blank" rel="noopener">YouTube Studio ↗</a>` : ""}</span>` : ""}
            <div class="da-yt-text">
              <div class="da-yt-row"><span class="da-yt-label">YouTube title</span><span class="da-yt-title">${esc(yt.title || "")}</span></div>
              <div class="da-yt-row"><span class="da-yt-label">Description</span><pre class="da-desc">${esc(yt.description || v.description || "")}</pre></div>
              <div class="da-yt-row"><span class="da-yt-label">Tags</span><span class="pills">${(yt.tags || []).map((t) => `<span class="pill">${esc(t)}</span>`).join("")}</span></div>
            </div>
            <div class="da-vcard-actions">
              <a class="btn-secondary" href="/api/aftermarket/videos/${esc(v.key)}/facts" target="_blank" rel="noopener">Facts</a>
              ${!up && !check ? `<button type="button" class="btn-primary" data-upload="${esc(v.key)}" disabled>⬆️ Upload now (${esc(body.privacy)})</button>` : ""}
              ${!v.veto ? `<button type="button" class="btn-ghost da-delete" data-veto="${esc(v.key)}" title="${up ? "Make it private on YouTube" : "Don't upload it"}">🚫 ${up ? "Make private" : "Don't post"}</button>` : ""}
              <span class="hint da-upload-status"></span>
            </div>
          </div>
        </article>`;
      }).join("");
      els.videos.querySelectorAll("button[data-upload]").forEach((b) => { b.disabled = !(state.yt && state.yt.connected); });
    } catch (err) {
      showError(`Couldn't load the videos: ${err.message}`);
    }
  }

  els.videos.addEventListener("click", async (e) => {
    const upl = e.target.closest("button[data-upload]");
    const veto = e.target.closest("button[data-veto]");
    if (upl) {
      const status = upl.closest(".da-vcard-actions").querySelector(".da-upload-status");
      upl.disabled = true;
      status.textContent = "Uploading…";
      try {
        const { body } = await api(`/api/aftermarket/videos/${upl.dataset.upload}/upload`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        status.textContent = `On YouTube: ${body.url || ""}`;
        loadVideos();
      } catch (err) {
        status.textContent = err.message;
        upl.disabled = false;
      }
    }
    if (veto) {
      const card = veto.closest(".da-vcard");
      if (!window.confirm(`${veto.textContent.trim()} for "${card.querySelector("b").textContent}"?`)) return;
      try {
        await api(`/api/aftermarket/videos/${veto.dataset.veto}/veto`, { method: "POST" });
        loadVideos();
      } catch (err) {
        showError(err.message);
      }
    }
  });

  async function pollRun() {
    try {
      const { body } = await api("/api/aftermarket/run-job");
      if (body.running) {
        const p = body.progress === null || body.progress === undefined ? "" : ` (${Math.round(body.progress * 100)}%)`;
        els.runStatus.textContent = `⏳ ${body.job ? `${body.job.focus || KIND[body.job.kind] || body.job.kind}: ` : ""}${body.message || ""}${p}`;
        els.run.disabled = true;
        state.polling = setTimeout(pollRun, 2000);
        return;
      }
      els.run.disabled = false;
      state.polling = null;
      if (body.error) els.runStatus.textContent = `❌ ${body.error}`;
      else if (body.result) {
        const r = body.result;
        els.runStatus.textContent = r.error ? `❌ ${r.error}` : `✅ ${r.title || r.key}${r.record ? ` · on YouTube: ${r.record.url}` : body.upload ? " · not uploaded (vetoed, not connected, or AM_AUTO_POST off)" : ""}`;
        loadVideos();
        load();
        loadPlan();
      } else els.runStatus.textContent = "";
    } catch (err) {
      els.runStatus.textContent = err.message;
      state.polling = null;
    }
  }

  els.run.addEventListener("click", async () => {
    els.run.disabled = true;
    try {
      await api("/api/aftermarket/run", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: els.runKind.value, upload: els.runUpload.checked, template: els.runTemplate.checked }) });
      if (!state.polling) pollRun();
    } catch (err) {
      els.run.disabled = false;
      els.runStatus.textContent = err.message;
    }
  });
  $("amRefresh").addEventListener("click", () => load());

  async function load() {
    try {
      const { body } = await api("/api/aftermarket/status");
      renderStatus(body);
    } catch (err) {
      showError(err.message === "Not Found"
        ? "The dashboard server is older than this page: restart the dashboard to load the After Market tab."
        : `Couldn't load the After Market status: ${err.message}`);
    }
    renderYouTube();
    loadVideos();
    if (!state.polling) pollRun();
  }

  const onShow = () => {
    if (document.body.dataset.workspace !== "aftermarket") return;
    showSection(state.section);
    load();
    loadPlan();
  };
  new MutationObserver(onShow).observe(document.body, { attributes: true, attributeFilter: ["data-workspace"] });
  onShow();
})();
