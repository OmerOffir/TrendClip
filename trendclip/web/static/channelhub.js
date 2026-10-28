// Dark Outliers and Borderline Why on the sending machine: watch a planned video, send one to the Linux plan
// (a free time picked from Linux's own list), send the whole upcoming plan, and see Linux's plan for the week.
(() => {
  const { api, esc } = window.TrendClip;
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const dayLabel = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  const addDays = (iso, n) => {
    const d = new Date(`${iso}T12:00:00`);
    d.setDate(d.getDate() + n);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  };
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

  function linuxDay(day, plan) {
    const items = plan.items.filter((i) => i.date === day);
    const rows = plan.slots.map((slot) => {
      const item = items.find((i) => i.slot === slot);
      return item ? `<div class="hub-slot taken"><b>${esc(slot)}</b> ${item.state === "posted" ? "✅ " : ""}${esc(item.title)}</div>`
        : `<div class="hub-slot open"><b>${esc(slot)}</b> open</div>`;
    });
    items.filter((i) => !plan.slots.includes(i.slot)).forEach((i) => rows.push(`<div class="hub-slot taken"><b>${esc(i.slot)}</b> ${esc(i.title)}</div>`));
    return `<div class="hub-day"><div class="hub-day-name">${esc(dayLabel(day))}</div>${rows.join("")}</div>`;
  }

  // The "Send plan to Linux" bar and Linux's plan, in `box` (shown on the sending machine only).
  function mount(box, name, onChange) {
    box.innerHTML = `<div class="ch-hub-head"><b>🐧 Linux plan</b>
        <span class="hint">The Linux machine posts these; a video sent there leaves this machine's plan</span>
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
      counts();
      els.refresh.disabled = true;
      els.week.innerHTML = '<div class="empty">Asking the Linux machine…</div>';
      try {
        const { body: plan } = await api(`/api/channel-hub/${name}/plan`);
        els.week.innerHTML = Array.from({ length: plan.days }, (_, n) => linuxDay(addDays(plan.today, n), plan)).join("");
      } catch (err) {
        els.week.innerHTML = `<div class="empty error">Couldn't get the Linux plan: ${esc(failure(err).message)}</div>`;
      }
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

  window.TrendClip.channelHub = { sender, watch, picker, mount };
})();
