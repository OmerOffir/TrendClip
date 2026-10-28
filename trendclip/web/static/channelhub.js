// Dark Outliers and Borderline Why on the sending machine: watch a planned video, send one to the Linux plan
// (a free time picked from Linux's own list), send the whole upcoming plan, and see Linux's plan for the week.
(() => {
  const { api, esc } = window.TrendClip;
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const dayLabel = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  let status = null;
  // The hub endpoints answer errors with {message, free}; api() hands that over as JSON text.
  const failure = (err) => {
    try {
      const d = JSON.parse(err.message);
      if (d && d.message) return { message: d.message, free: d.free || [] };
    } catch { /* plain text */ }
    return { message: err.message, free: [] };
  };

  const sender = () => {
    status = status || api("/api/hub/status").then(({ body }) => !!body.sender).catch(() => false);
    return status;
  };

  // A player under `row` (a second click closes it).
  function watch(row, src) {
    const next = row.nextElementSibling;
    if (next && next.classList.contains("ch-watch")) return next.remove();
    row.insertAdjacentHTML("afterend", `<div class="ch-watch"><video controls autoplay preload="metadata" src="${esc(src)}"></video>
      <a class="hint" href="${esc(src)}" download>Download</a></div>`);
  }

  // A free-time picker under `row`; onSent(reply) once Linux took the video.
  async function picker(row, name, ref, onSent) {
    const next = row.nextElementSibling;
    if (next && next.classList.contains("ch-picker")) return next.remove();
    row.insertAdjacentHTML("afterend", '<div class="ch-picker"><span class="hint">Asking Linux for its free times…</span></div>');
    const box = row.nextElementSibling;
    const show = (free, message) => {
      box.innerHTML = (free.length
        ? `<select aria-label="Time on the Linux plan">${free.map((f) => `<option value="${esc(f.date)} ${esc(f.slot)}">${esc(dayLabel(f.date))} ${esc(f.slot)}</option>`).join("")}</select>
           <button type="button" class="btn-primary" data-ch-send>📤 Send</button>`
        : '<span class="hint">No free time on the Linux plan this week</span>')
        + '<button type="button" class="btn-ghost" data-ch-cancel>Cancel</button>'
        + `<span class="hint ch-status">${message ? esc(message) : "Linux posts it in the channel's Discord and uploads it at that time"}</span>`;
    };
    try {
      show((await api(`/api/channel-hub/${name}/free`)).body.free);
    } catch (err) {
      box.innerHTML = `<span class="da-note">Couldn't reach Linux: ${esc(failure(err).message)}</span><button type="button" class="btn-ghost" data-ch-cancel>Close</button>`;
    }
    box.onclick = async (e) => {
      if (e.target.closest("[data-ch-cancel]")) return box.remove();
      const btn = e.target.closest("[data-ch-send]");
      if (!btn) return;
      const [date, slot] = box.querySelector("select").value.split(" ");
      btn.disabled = true;
      box.querySelector(".ch-status").textContent = "Sending through Discord…";
      try {
        const { body } = await post(`/api/channel-hub/${name}/send`, { ref: String(ref), date, slot });
        box.innerHTML = `<span class="pill good">✅ On the Linux plan · ${esc(dayLabel(body.date))} ${esc(body.slot)}</span>`;
        onSent(body);
      } catch (err) {
        const { message, free } = failure(err);
        show(free, `⚠️ ${message}`);
      }
    };
  }

  // On the sending machine the Plan tab shows the Linux plan: a note for its head, and an empty Linux slot's
  // "send one of this machine's videos here" (data-send-here on the button, the <select> next to it).
  function source(body) {
    if (body.source === "linux") return '<span class="pill good">🐧 Linux plan</span> ';
    if (body.hub_error) return `<span class="pill warn" title="${esc(body.hub_error)}">💻 This machine's plan: Linux didn't answer</span> `;
    return "";
  }

  function sendHere(day, slot, sendable) {
    if (!sendable || !sendable.length) return '<span class="hint">open on Linux · no video on this machine to send</span>';
    return `<select aria-label="Video to send for ${esc(day)} ${esc(slot)}"><option value="">— a video from this machine —</option>
      ${sendable.map((v) => `<option value="${esc(v.ref)}">${esc(v.title)}${v.seconds ? ` (${Math.round(v.seconds)}s)` : ""}</option>`).join("")}</select>
      <button type="button" class="btn-secondary" data-send-here="${esc(day)} ${esc(slot)}">📤 Send here</button>`;
  }

  // A click on a "Send here" button: send the picked video to that Linux slot. Resolves true once Linux took it.
  async function sendHereClick(btn, name) {
    const sel = btn.parentElement.querySelector("select");
    if (!sel || !sel.value) return false;
    const [date, slot] = btn.dataset.sendHere.split(" ");
    btn.disabled = true;
    btn.textContent = "Sending through Discord…";
    try {
      await post(`/api/channel-hub/${name}/send`, { ref: sel.value, date, slot });
      return true;
    } catch (err) {
      btn.disabled = false;
      btn.textContent = "📤 Send here";
      alert(`Linux didn't take it: ${failure(err).message}`);
      return false;
    }
  }

  // The "Send plan to Linux" bar and this machine's videos not on Linux yet, in `box` (shown on the sending machine only).
  function mount(box, name, onChange) {
    box.innerHTML = `<div class="ch-hub-head"><b>💻 On this machine, not on Linux yet</b>
        <span class="hint">The plan above is the Linux one, which posts; these go there at their own day and time</span>
        <span class="spacer"></span>
        <button type="button" class="btn-primary" data-ch-plan hidden></button>
        <button type="button" class="btn-secondary" data-ch-refresh>Refresh</button></div>
      <div class="hub-results" hidden></div>
      <div class="hub-week"><div class="empty">Loading…</div></div>`;
    const els = { send: box.querySelector("[data-ch-plan]"), refresh: box.querySelector("[data-ch-refresh]"),
      results: box.querySelector(".hub-results"), week: box.querySelector(".hub-week") };
    let pending = [];

    async function counts() {
      if (!(await sender())) return;
      try {
        pending = (await api(`/api/channel-hub/${name}/planned`)).body.items;
      } catch { pending = []; }
      els.send.hidden = !pending.length;
      els.send.textContent = `📤 Send plan to Linux (${pending.length})`;
    }

    async function refresh() {
      if (!(await sender())) return;
      box.hidden = false;
      els.refresh.disabled = true;
      await counts();
      els.week.innerHTML = pending.length
        ? pending.map((p) => `<div class="hub-slot taken"><b>${esc(dayLabel(p.date))} ${esc(p.slot)}</b> ${esc(p.title)}</div>`).join("")
        : '<div class="empty">Nothing planned here that Linux doesn\'t have</div>';
      els.refresh.disabled = false;
    }

    els.refresh.addEventListener("click", refresh);
    els.send.addEventListener("click", async () => {
      const n = pending.length;
      if (!confirm(`Send ${n} video${n === 1 ? "" : "s"} to the Linux plan at the same day and time?\nEach one Linux takes leaves this machine's plan.`)) return;
      els.send.disabled = true;
      els.send.textContent = `Sending ${n} through Discord…`;
      els.results.hidden = true;
      try {
        const { body } = await post(`/api/channel-hub/${name}/send-plan`);
        els.results.innerHTML = body.results.length ? body.results.map((r) => `
          <div class="${r.ok ? "ok" : "error"}">${r.ok ? "✅" : "⚠️"} ${esc(dayLabel(r.date))} ${esc(r.slot)} · <b>${esc(r.title)}</b>: ${esc(r.message)}</div>`).join("")
          : "<div>Nothing to send.</div>";
      } catch (err) {
        els.results.innerHTML = `<div class="error">⚠️ Could not send to Linux: ${esc(failure(err).message)}</div>`;
      }
      els.results.hidden = false;
      els.send.disabled = false;
      onChange();
      refresh();
    });
    return { refresh, counts };
  }

  window.TrendClip.channelHub = { sender, watch, picker, mount, source, sendHere, sendHereClick };
})();
