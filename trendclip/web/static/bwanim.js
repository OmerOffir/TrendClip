// Borderline Why "Animations": the short 2D clips generated once on the GPU (modules.bw_anim), one row per tag and one
// cell per model, with the pilot's progress while it runs.
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const grid = $("bwAGrid");
  if (!grid) return;
  const error = $("bwAError");
  const pilotLine = $("bwAPilot");
  let timer = null;

  const minutes = (s) => (s >= 90 ? `${(s / 60).toFixed(1)} min` : `${Math.round(s)} s`);

  function render(data) {
    const p = data.pilot || {};
    const failed = (p.failed || []).length ? ` · ⚠️ ${p.failed.map((f) => `${esc(f.clip)}: ${esc(f.error)}`).join(" | ")}` : "";
    if (p.running) {
      pilotLine.innerHTML = `<span class="pill">🎞️ pilot running on the GPU: ${esc(p.current)} — ${esc(p.step)} · ${p.done} of ${p.total} done</span>${failed}`;
    } else if (p.finished) {
      pilotLine.innerHTML = `<span class="pill good">✅ pilot finished ${esc(p.finished.replace("T", " "))} · ${data.clips.length} clip${data.clips.length === 1 ? "" : "s"}</span>${failed}`;
    } else {
      pilotLine.innerHTML = data.clips.length ? "" : '<span class="pill">No clips yet. The pilot runs on Linux: <code>python -m modules.bw_anim pilot</code></span>';
    }
    const by = {};
    data.clips.forEach((c) => { (by[c.tag] = by[c.tag] || {})[c.model] = c; });
    const models = Object.entries(data.models);
    grid.innerHTML = data.tags.map((tag) => `<div class="bw-a-row">
        <b>${esc(tag)}<small>${esc((data.clips.find((c) => c.tag === tag) || {}).prompt?.split(", flat 2D")[0] || "")}</small></b>
        <div class="bw-a-cells">${models.map(([key, label]) => {
          const c = by[tag] && by[tag][key];
          if (!c) return `<div class="bw-a-cell missing"><b>${esc(label)}</b><span class="hint">${p.running && p.current === `${key} ${tag}` ? `⏳ ${esc(p.step)}…` : "not made yet"}</span></div>`;
          return `<div class="bw-a-cell"><b>${esc(label)}</b>
            <video src="/api/borderlinewhy/anim/${encodeURIComponent(c.name)}.mp4" controls loop muted autoplay playsinline preload="metadata"></video>
            <span class="hint">${c.seconds} s · ${c.width}×${c.height} · ${c.fps} fps · took ${minutes(c.took)} · ${esc(c.note || "")} · seed ${c.seed}</span></div>`;
        }).join("")}</div>
      </div>`).join("");
    clearTimeout(timer);
    if (p.running) timer = setTimeout(load, 20000);
  }

  async function load() {
    try {
      const data = await api("/api/borderlinewhy/anim");
      error.hidden = true;
      render(data);
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  }

  $("bwARefresh").addEventListener("click", load);
  document.querySelector('[data-bw-view="anim"]')?.addEventListener("click", load);
  document.addEventListener("trendclip:workspace", (e) => {
    if (e.detail.workspace === "borderlinewhy" && !$("viewBwAnim").hidden) load();
  });
})();
