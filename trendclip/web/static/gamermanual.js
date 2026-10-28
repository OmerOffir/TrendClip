// SideQuestLogic "Write it myself": a Gamer IQ question written with any LLM. You carry each prompt to the LLM
// and paste its answer back; the fact check, then the video over the game's own gameplay (trendclip/gamer_manual.py).
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const box = $("gwSession");
  if (!box) return;
  const error = $("gwError");
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const STORE = "gamerManualSession";
  const STEPS = [["write", "1 · Questions"], ["facts", "2 · Fact check"], ["ready", "3 · Make the video"]];
  const NOW = { write: "write", facts: "facts", ready: "ready", made: "ready" };
  const SHAPES = { number: "a number players meet", fake: "which one is NOT in the game", first: "which came first", lore: "a fact from its makers" };
  let current = null;
  let slots = [];
  let busy = false;

  const showError = (msg) => { error.textContent = msg || ""; error.hidden = !msg; };
  const remember = (id) => { try { id ? localStorage.setItem(STORE, id) : localStorage.removeItem(STORE); } catch { /* private window */ } };
  const recalled = () => { try { return localStorage.getItem(STORE); } catch { return null; } };

  // The clipboard API needs https or localhost; the hub's dashboard is plain http on the LAN, so select-and-copy too.
  async function copy(textarea, btn) {
    try {
      await navigator.clipboard.writeText(textarea.value);
    } catch {
      textarea.select();
      document.execCommand("copy");
    }
    btn.textContent = "✅ Copied";
    setTimeout(() => { btn.textContent = "📋 Copy the prompt"; }, 2000);
  }

  function promptBlock(s, where) {
    return `<div class="bw-w-step"><b>${where}</b>
        <span class="hint">${s.prompt.length.toLocaleString()} characters · each prompt is whole, so a new chat or another LLM works too</span>
        <textarea readonly rows="6">${esc(s.prompt)}</textarea>
        <button type="button" class="btn-secondary" data-w="copy">📋 Copy the prompt</button></div>`;
  }

  function pasteBlock(label, act, button, placeholder) {
    return `<div class="bw-w-step"><b>${label}</b>
        <textarea rows="6" placeholder="${placeholder}"></textarea>
        <button type="button" class="btn-primary" data-w="${act}">${button}</button></div>`;
  }

  function questions(s) {
    if (!s.questions.length) return "";
    const rows = s.questions.map((q, i) => {
      const mark = q.passed === true ? "✅" : q.passed === false ? "❌" : "·";
      const pick = s.step === "ready" && q.passed
        ? ` <label class="hint"><input type="radio" name="gwChosen" value="${i}"${i === s.chosen ? " checked" : ""}> use this one</label>` : "";
      const why = q.passed === false && q.why ? ` <small class="hint">${esc(q.why)}</small>` : "";
      return `<li>${mark} <b>${esc(q.question)}</b> ${esc(q.options.join(" / "))} → <b>${esc(q.correct)}</b>${pick}${why}</li>`;
    }).join("");
    return `<details class="bw-w-preview" open><summary>${s.questions.length} question${s.questions.length === 1 ? "" : "s"}</summary><ol>${rows}</ol></details>`;
  }

  function slotPicker() {
    const options = slots.map((r) => `<option value="${esc(r.day)} ${esc(r.slot)}">${esc(r.day)} ${esc(r.slot)}</option>`).join("");
    return `<select data-w="slot" aria-label="Slot">${options}<option value="">Don't plan it (it waits in Upload)</option></select>`;
  }

  function render(s) {
    current = s;
    remember(s.id);
    const at = NOW[s.step] || "write";
    const steps = STEPS.map(([key, label]) => `<span class="pill${key === at ? " good" : ""}">${label}</span>`).join(" ");
    let body = "";
    if (s.problems.length) {
      const head = s.step === "write" && s.questions.length === 0 ? "Not usable yet" : "Skipped";
      body += `<div class="da-note"><b>${head}:</b><ul>${s.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>${s.step === "write" ? "The prompt below asks for new ones (the rejected ones are listed as already asked)." : ""}</div>`;
    }
    if (s.step === "write") {
      body += promptBlock(s, "Copy this prompt into your LLM");
      body += pasteBlock("Paste its answer", "answer", "🔎 Check the questions", "Paste the whole answer here (the JSON)");
    }
    if (s.step === "facts") {
      body += promptBlock(s, "Copy this fact-check prompt into a second LLM (one with web search is best)");
      body += pasteBlock("Paste the fact checker's answer", "facts", "🔎 Read the verdicts", "1: PASS\n2: FAIL: reason");
    }
    if (s.step === "ready") {
      body += `<div class="bw-w-step"><b>✅ Ready: Level ${s.level}</b><span class="hint">The video is made over new no-copyright ${esc(s.game)} gameplay, then planned in the slot</span>
        <div class="bw-w-make">${slotPicker()}<button type="button" class="btn-primary" data-w="make">🎬 Make the video</button><span class="hint" data-w="status"></span></div></div>`;
    }
    if (s.step === "made") body += `<div class="bw-w-step"><b>🎬 Made: ${esc(s.video || "")}</b><span class="hint">It's in Upload (and in Plan when a slot was picked)</span></div>`;
    box.innerHTML = `<div class="bw-w-head"><b>${esc(s.game)}</b><span class="hint">Shape: ${esc(SHAPES[s.shape] || s.shape)}</span>${steps}</div>
      ${questions(s)}${body}`;
  }

  async function act(fn) {
    if (busy) return;
    busy = true;
    showError("");
    try {
      await fn();
    } catch (err) {
      showError(err.message);
    }
    busy = false;
  }

  async function watchMake(jobId, status) {
    const { body: job } = await api(`/api/create/jobs/${encodeURIComponent(jobId)}`);
    if (job.status === "queued" || job.status === "running") {
      status.textContent = `${job.message}${job.progress != null ? ` (${Math.round(job.progress * 100)}%)` : ""}…`;
      return setTimeout(() => watchMake(jobId, status), 3000);
    }
    if (job.status === "error") {
      status.textContent = `❌ ${job.error}`;
      box.querySelector('[data-w="make"]').disabled = false;
      return;
    }
    open(current.id);
    load();
  }

  box.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-w]");
    if (!btn) return;
    const what = btn.dataset.w;
    if (what === "copy") return copy(btn.parentElement.querySelector("textarea"), btn);
    if (what === "answer" || what === "facts") {
      const text = btn.parentElement.querySelector("textarea").value;
      if (!text.trim()) return;
      return act(async () => {
        btn.disabled = true;
        btn.textContent = "Checking…";
        try {
          render((await post(`/api/gamer/manual/${encodeURIComponent(current.id)}/${what}`, { text })).body);
        } finally {
          btn.disabled = false;
        }
      });
    }
    if (what === "make") {
      const value = box.querySelector('select[data-w="slot"]').value;
      const [day, slot] = value ? value.split(" ") : [];
      return act(async () => {
        btn.disabled = true;
        const { body: job } = await post(`/api/gamer/manual/${encodeURIComponent(current.id)}/make`, day ? { day, slot } : {});
        watchMake(job.id, box.querySelector('[data-w="status"]'));
      });
    }
  });

  box.addEventListener("change", (e) => {
    if (e.target.name !== "gwChosen") return;
    act(async () => render((await post(`/api/gamer/manual/${encodeURIComponent(current.id)}/choose`, { index: Number(e.target.value) })).body));
  });

  async function open(id) {
    try {
      render((await api(`/api/gamer/manual/${encodeURIComponent(id)}`)).body);
    } catch {
      remember(null);
      box.innerHTML = "";
    }
  }

  async function load() {
    try {
      const { body } = await api("/api/gamer/manual");
      slots = body.slots;
      const game = $("gwGame");
      const picked = game.value;
      game.innerHTML = body.games.map((g) => `<option value="${esc(g)}">${esc(g)}</option>`).join("");
      if (picked && body.games.includes(picked)) game.value = picked;
      $("gwLevel").textContent = `Next: Level ${body.level}`;
      const sessions = $("gwSessions");
      sessions.innerHTML = body.sessions.map((s) => `<option value="${esc(s.id)}">${esc(s.game)} · ${esc(s.created_at.replace("T", " ").slice(0, 16))} · ${esc(s.step)}</option>`).join("");
      sessions.hidden = $("gwOpen").hidden = !body.sessions.length;
    } catch (err) {
      showError(err.message);
    }
    const last = recalled();
    if (last && (!current || current.id !== last)) open(last);
  }

  $("gwStart").addEventListener("click", (e) => act(async () => {
    e.target.disabled = true;
    try {
      render((await post("/api/gamer/manual", { game: $("gwGame").value })).body);
      load();
    } finally {
      e.target.disabled = false;
    }
  }));
  $("gwOpen").addEventListener("click", () => open($("gwSessions").value));
  document.addEventListener("trendclip:view", (e) => { if (e.detail.view === "write") load(); });
})();
