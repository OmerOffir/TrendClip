// Upcoming: every channel's upload slots for the week in one list (GET /api/upcoming).
(() => {
  const $ = (id) => document.getElementById(id);
  const list = $("upList");
  if (!list) return;
  const error = $("upError");
  const hint = $("upHint");
  const nav = $("upNavEmpty");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const STATES = {
    waits: ["⏳ waits for its time", ""], posted: ["▶️ on YouTube", "good"], late: ["⚠️ not posted", "warn"],
    missing: ["⚠️ video missing", "warn"], empty: ["empty slot", "warn"], passed: ["passed, nothing went out", ""],
    approval: ["✋ waiting for Approve", "warn"], making: ["🎬 being made", ""], blocked: ["⛔ failed its check", "warn"],
    vetoed: ["🚫 vetoed", ""], error: ["❌ could not read this channel", "warn"],
  };
  const PLAN_VIEW = { sidequestlogic: ["plan", "data-view"], darkarchives: ["plan", "data-da-view"], borderlinewhy: ["plan", "data-bw-view"] };
  const dayLabel = (iso) => {
    const d = new Date(`${iso}T12:00:00`);
    return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
  };

  function render(data) {
    nav.textContent = data.empty;
    nav.hidden = !data.empty;
    const twice = data.twice ? ` · <b class="da-note" style="display:inline">${data.twice} slot${data.twice > 1 ? "s" : ""} hold a video that is planned twice</b>` : "";
    hint.innerHTML = `${data.empty} empty slot${data.empty === 1 ? "" : "s"} this week across the channels${twice}. Times are ${esc(data.now.slice(11, 16))} now, in the channels' time zone.`;
    list.innerHTML = data.days.map((day) => `
      <div class="up-day${day.today ? " today" : ""}">
        <div class="up-date"><b>${esc(dayLabel(day.day))}</b><small>${esc(day.day)}${day.today ? " · today" : ""}</small></div>
        ${day.rows.length ? day.rows.map((r) => {
          const [text, cls] = STATES[r.state] || [r.state, ""];
          const planIt = r.state === "empty" ? `<button type="button" class="btn-ghost" data-go="${r.channel}" title="Open this channel's Plan tab">Plan it →</button>` : "";
          const open = r.url ? `<a class="hint" href="${esc(r.url)}" target="_blank" rel="noopener">open ↗</a>` : "";
          const made = r.state === "empty" && r.made_at ? `<small>the bot makes it at ${esc(r.made_at.slice(11, 16))}</small>` : "";
          return `<div class="up-row ${esc(r.state)}${r.twice ? " twice" : ""}">
            <span class="time">${esc(r.time || "—")}</span>
            <span class="mark ${esc(r.channel)}" title="${esc(r.label)}">${{ sidequestlogic: "SQ", darkarchives: "DA", borderlinewhy: "BW" }[r.channel] || "?"}</span>
            <span class="title">${r.title ? `🎬 ${esc(r.title)}` : r.state === "empty" ? "— nothing planned —" : ""}${r.seconds ? `<small>${Math.round(r.seconds)} s</small>` : ""}${made}${r.twice ? ' <span class="pill warn">planned twice</span>' : ""}</span>
            <span class="actions"><span class="pill ${cls}">${text}</span>${open}${planIt}</span>
          </div>`;
        }).join("") : '<div class="empty">No slots</div>'}
      </div>`).join("");
  }

  async function load() {
    try {
      const res = await fetch("/api/upcoming");
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || res.statusText);
      error.hidden = true;
      render(body);
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  }

  list.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-go]");
    if (!btn) return;
    const channel = btn.dataset.go;
    document.querySelector(`#workspaces [data-workspace="${channel}"]`)?.click();
    const [view, attr] = PLAN_VIEW[channel];
    document.querySelector(`[${attr}="${view}"]`)?.click();
  });
  $("upRefresh").addEventListener("click", load);
  document.addEventListener("trendclip:workspace", (e) => { if (e.detail.workspace === "upcoming") load(); });
  if (document.body.dataset.workspace === "upcoming") load();
  else fetch("/api/upcoming").then((r) => r.json()).then((d) => { if (d && d.empty !== undefined) { nav.textContent = d.empty; nav.hidden = !d.empty; } }).catch(() => {});
})();
