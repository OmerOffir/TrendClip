// Upcoming: every channel's upload slots for the week in one list (GET /api/upcoming).
(() => {
  const $ = (id) => document.getElementById(id);
  const list = $("upList");
  if (!list) return;
  const error = $("upError");
  const hint = $("upHint");
  const nav = $("upNavEmpty");
  const local = $("upLocal");
  const SEND_PLAN = { sidequestlogic: "/api/hub/send-plan", darkarchives: "/api/channel-hub/darkarchives/send-plan",
    borderlinewhy: "/api/channel-hub/borderlinewhy/send-plan" };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const STATES = {
    waits: ["⏳ waits for its time", ""], posted: ["▶️ on YouTube", "good"], late: ["⚠️ not posted", "warn"],
    missing: ["⚠️ video missing", "warn"], empty: ["empty slot", "warn"], passed: ["passed, nothing went out", ""],
    approval: ["✋ waiting for Approve", "warn"], making: ["🎬 being made", ""], blocked: ["⛔ failed its check", "warn"],
    vetoed: ["🚫 vetoed", ""], held: ["⏸ on hold", ""], error: ["❌ could not read this channel", "warn"],
  };
  const PLAN_VIEW = { sidequestlogic: ["plan", "data-view"], darkarchives: ["plan", "data-da-view"], borderlinewhy: ["plan", "data-bw-view"], pricetagwhy: ["plan", "data-pw-view"] };
  // A made video's thumbnail, to upload by hand in YouTube Studio (GET /api/thumbnail/<channel>/<ref>).
  const NO_THUMB = new Set(["empty", "passed", "making", "missing", "error"]);
  const opened = new Set();
  let thumbsHere = true;
  const thumbUrl = (r) => `/api/thumbnail/${encodeURIComponent(r.channel)}/${encodeURIComponent(r.ref)}`;
  const thumbKey = (r) => `${r.channel}/${r.ref}`;
  function thumbBox(r) {
    const url = thumbUrl(r);
    return `<div class="up-thumb" data-thumb-box="${esc(thumbKey(r))}">
      <img src="${esc(url)}" alt="Thumbnail" loading="lazy" data-thumb-img>
      <span class="hint" data-thumb-state>Drawing the thumbnail… (the map ones take up to a minute)</span>
      <a class="btn-primary" href="${esc(url)}?download=1" download hidden data-thumb-dl>⬇️ Download</a>
      <span class="hint">1080x1920 · upload it in YouTube Studio: the video → Details → Thumbnail</span>
    </div>`;
  }
  const dayLabel = (iso) => {
    const d = new Date(`${iso}T12:00:00`);
    return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
  };

  function render(data) {
    thumbsHere = data.source !== "linux";
    nav.textContent = data.empty;
    nav.hidden = !data.empty;
    const twice = data.twice ? ` · <b class="da-note" style="display:inline">${data.twice} slot${data.twice > 1 ? "s" : ""} hold a video that is planned twice</b>` : "";
    const source = data.source === "linux" ? '<span class="pill good up-source">🐧 Linux plan</span>'
      : data.hub_error ? '<span class="pill warn up-source">This machine only</span>' : "";
    const covered = data.covered ? ` · ${data.covered} of them ${data.covered === 1 ? "has" : "have"} a script written for later waiting` : "";
    hint.innerHTML = `${source}${data.empty} empty slot${data.empty === 1 ? "" : "s"} this week across the channels${covered}${twice}. Times are ${esc(data.now.slice(11, 16))} now, in the channels' time zone.${geminiLine(data.gemini)}${waitingLine(data)}`;
    renderLocal(data);
    list.innerHTML = data.days.map((day) => `
      <div class="up-day${day.today ? " today" : ""}">
        <div class="up-date"><b>${esc(dayLabel(day.day))}</b><small>${esc(day.day)}${day.today ? " · today" : ""}</small></div>
        ${day.rows.length ? day.rows.map((r) => {
          const [text, cls] = STATES[r.state] || [r.state, ""];
          const planIt = r.state === "empty" ? `<button type="button" class="btn-ghost" data-go="${r.channel}" title="Open this channel's Plan tab">Plan it →</button>` : "";
          const canThumb = thumbsHere && r.ref !== null && r.ref !== undefined && r.title && !NO_THUMB.has(r.state);
          const thumb = canThumb ? `<button type="button" class="btn-ghost" data-thumb="${esc(thumbKey(r))}" title="A thumbnail for this video, to download and upload in YouTube Studio">🖼️ Thumbnail</button>` : "";
          const open = r.url ? `<a class="hint" href="${esc(r.url)}" target="_blank" rel="noopener">open ↗</a>` : "";
          const made = r.state === "empty" && r.made_at ? `<small>the bot makes it at ${esc(r.made_at.slice(11, 16))}</small>` : "";
          const waiting = r.state === "empty" && r.waiting;
          const pill = waiting ? '<span class="pill good">📝 script waiting, no Gemini needed</span>' : `<span class="pill ${cls}">${text}</span>`;
          const what = r.title ? `🎬 ${esc(r.title)}` : waiting ? `📝 the bot makes it from: ${esc(r.waiting)}` : r.state === "empty" ? "— nothing planned —" : "";
          return `<div class="up-row ${esc(r.state)}${r.twice ? " twice" : ""}${waiting ? " waiting" : ""}">
            <span class="time">${esc(r.time || "—")}</span>
            <span class="mark ${esc(r.channel)}" title="${esc(r.label)}">${{ sidequestlogic: "SQ", darkarchives: "DA", borderlinewhy: "BW", pricetagwhy: "PW" }[r.channel] || "?"}</span>
            <span class="title">${what}${r.seconds ? `<small>${Math.round(r.seconds)} s</small>` : ""}${made}${r.twice ? ' <span class="pill warn">planned twice</span>' : ""}</span>
            <span class="actions">${pill}${open}${planIt}${thumb}</span>
            ${canThumb && opened.has(thumbKey(r)) ? thumbBox(r) : ""}
          </div>`;
        }).join("") : '<div class="empty">No slots</div>'}
      </div>`).join("");
  }

  // The scripts written for later (each channel's "Write a week"), waiting on the machine that makes the videos.
  function waitingLine(data) {
    const w = data.waiting || {};
    const parts = Object.entries(w).filter(([, q]) => q.count || q.note).map(([channel, q]) => {
      const label = { sidequestlogic: "SQ", darkarchives: "DA", borderlinewhy: "BW", pricetagwhy: "PW" }[channel] || channel;
      if (q.note) return `${label} ?`;
      if (channel === "darkarchives") {
        const rankings = q.items.filter((i) => i.ranking).length;
        return `${label} ${q.count - rankings}${rankings ? ` + ${rankings} ranking${rankings > 1 ? "s" : ""}` : ""}`;
      }
      if (channel === "sidequestlogic") {
        const games = {};
        q.items.forEach((i) => { games[i.game] = (games[i.game] || 0) + 1; });
        return `${label} ${Object.entries(games).map(([g, n]) => `${esc(g)} ×${n}`).join(", ")}`;
      }
      return `${label} ${q.count}`;
    });
    const where = data.source === "linux" ? "on Linux" : "here";
    return parts.length ? ` <span class="pill good" title="Scripts from the Write a week tabs: each channel's run makes its videos from them, oldest first, before asking Gemini">📝 written for later ${where}: ${parts.join(" · ")}</span>`
      : ` <span class="pill" title="Write a week of scripts in each channel's Write a week tab: the bot makes the videos from them without Gemini">📝 nothing written for later ${where}</span>`;
  }

  // Gemini's free tier today: calls used, what is left, until when Google closed every model.
  function geminiLine(g) {
    if (!g || g.error) return "";
    const reset = g.reset_at ? g.reset_at.slice(11, 16) : "";
    let state, cls;
    if (g.all_closed && g.reopens_at) {
      state = `every model closed until ${esc(g.reopens_at.slice(11, 16))}`; cls = "warn";
    } else if (g.low) {
      state = `low: ${g.left} left, kept for the videos (resets ${esc(reset)})`; cls = "warn";
    } else {
      state = `${g.left} left (resets ${esc(reset)})`; cls = "good";
    }
    return ` <span class="pill ${cls}" title="Google's free tier: about ${g.limit} requests a day across the models that work, counted from midnight in California">🔮 Gemini free tier: ${g.used} used today · ${state}</span>`;
  }

  // On the sending machine: what is planned here and not on Linux yet, with each channel's "Send plan to Linux".
  function renderLocal(data) {
    const rows = data.local || [];
    const why = data.hub_error ? `<div class="da-note">Couldn't get the Linux week, showing this machine's: ${esc(data.hub_error)}</div>` : "";
    local.hidden = !rows.length && !why;
    if (local.hidden) return;
    const groups = {};
    rows.forEach((r) => (groups[r.channel] = groups[r.channel] || []).push(r));
    local.innerHTML = why + (rows.length ? `<div class="up-local-head"><b>💻 On this machine, not on Linux yet</b>
        <span class="hint">Each goes to Linux at its own day and time; Linux posts it</span></div>`
      + Object.entries(groups).map(([channel, items]) => `<div class="up-local-group">
        <b>${esc(items[0].label)}</b> · ${items.length} video${items.length === 1 ? "" : "s"}
        <button type="button" class="btn-primary" data-send="${esc(channel)}">📤 Send to Linux</button>
        <span class="hint" data-result="${esc(channel)}"></span>
        <ul>${items.map((r) => `<li>${esc(r.day)} ${esc(r.time || "no time")} · ${esc(r.title)}</li>`).join("")}</ul></div>`).join("") : "");
  }

  async function send(btn) {
    const channel = btn.dataset.send;
    const out = local.querySelector(`[data-result="${channel}"]`);
    if (!confirm("Send these videos to the Linux plan at the same day and time?\nEach one Linux takes leaves this machine's plan.")) return;
    btn.disabled = true;
    out.textContent = "Sending through Discord…";
    try {
      const res = await fetch(SEND_PLAN[channel], { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const body = await res.json();
      if (!res.ok) throw new Error((body.detail && (body.detail.message || body.detail)) || res.statusText);
      const bad = body.results.filter((r) => !r.ok);
      out.textContent = `✅ ${body.results.length - bad.length} sent` + (bad.length ? ` · ⚠️ ${bad.map((r) => `${r.title}: ${r.message}`).join(" | ")}` : "");
      load(true);
    } catch (err) {
      out.textContent = `⚠️ ${err.message}`;
      btn.disabled = false;
    }
  }

  async function load(fresh) {
    try {
      const res = await fetch(`/api/upcoming${fresh ? "?fresh=1" : ""}`);
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || res.statusText);
      error.hidden = true;
      render(body);
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  }

  local.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-send]");
    if (btn) send(btn);
  });
  // the thumbnail's picture loaded (or failed): show Download, or the reason
  list.addEventListener("load", (e) => {
    const box = e.target.closest && e.target.matches("[data-thumb-img]") && e.target.closest(".up-thumb");
    if (!box) return;
    box.querySelector("[data-thumb-state]").hidden = true;
    box.querySelector("[data-thumb-dl]").hidden = false;
  }, true);
  list.addEventListener("error", async (e) => {
    const box = e.target.closest && e.target.matches("[data-thumb-img]") && e.target.closest(".up-thumb");
    if (!box) return;
    e.target.hidden = true;
    let why = "";
    try { why = (await (await fetch(e.target.src)).json()).detail; } catch { /* keep the plain message */ }
    box.querySelector("[data-thumb-state]").textContent = `⚠️ No thumbnail${why ? `: ${why}` : ""}`;
  }, true);
  list.addEventListener("click", (e) => {
    const t = e.target.closest("button[data-thumb]");
    if (t) {
      const key = t.dataset.thumb;
      const row = t.closest(".up-row");
      const box = row.querySelector(".up-thumb");
      if (box) { box.remove(); opened.delete(key); return; }
      opened.add(key);
      const [channel, ref] = [key.slice(0, key.indexOf("/")), key.slice(key.indexOf("/") + 1)];
      row.insertAdjacentHTML("beforeend", thumbBox({ channel, ref }));
      return;
    }
    const btn = e.target.closest("button[data-go]");
    if (!btn) return;
    const channel = btn.dataset.go;
    document.querySelector(`#workspaces [data-workspace="${channel}"]`)?.click();
    const [view, attr] = PLAN_VIEW[channel];
    document.querySelector(`[${attr}="${view}"]`)?.click();
  });
  $("upRefresh").addEventListener("click", () => load(true));
  document.addEventListener("trendclip:workspace", (e) => { if (e.detail.workspace === "upcoming") load(); });
  if (document.body.dataset.workspace === "upcoming") load();
  else fetch("/api/upcoming").then((r) => r.json()).then((d) => { if (d && d.empty !== undefined) { nav.textContent = d.empty; nav.hidden = !d.empty; } }).catch(() => {});
})();
