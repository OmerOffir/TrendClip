// SideQuestLogic "Write it myself": Gamer IQ questions written with any LLM. You carry each prompt to the LLM and
// paste its answer back; the fact check, then a video for each ticked question that passed, each the next level in
// the next free slot, over the game's own gameplay (trendclip/gamer_manual.py).
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

  const makeable = (s) => s.questions.map((q, i) => [q, i]).filter(([q]) => q.passed && !q.video);

  function questions(s) {
    if (!s.questions.length) return "";
    const ready = ["ready", "made"].includes(s.step);
    const rows = s.questions.map((q, i) => {
      const mark = q.passed === true ? "✅" : q.passed === false ? "❌" : "·";
      const tick = ready && q.passed && !q.video ? `<input type="checkbox" data-w="pick" value="${i}" checked> ` : "";
      const said = q.spoken && q.spoken !== q.question ? `<br><small class="hint">Spoken: “${esc(q.spoken)}”</small>` : "";
      const why = q.passed === false && q.why ? ` <small class="hint">${esc(q.why)}</small>` : "";
      const made = q.video ? ` <span class="pill good">🎬 ${esc(q.video)}</span>` : "";
      const slot = tick ? ' <span class="hint" data-w="slotof"></span>' : "";
      return `<li><label>${tick}${mark} <b>${esc(q.question)}</b> ${esc(q.options.join(" / "))} → <b>${esc(q.correct)}</b></label>${made}${slot}${why}${said}</li>`;
    }).join("");
    return `<details class="bw-w-preview" open><summary>${s.questions.length} question${s.questions.length === 1 ? "" : "s"}</summary><ol>${rows}</ol></details>`;
  }

  // Which slot each ticked question takes: the free slots in order, as the server assigns them.
  function showSlots() {
    const plan = box.querySelector('input[data-w="plan"]')?.checked;
    const picked = [...box.querySelectorAll('input[data-w="pick"]')];
    let n = 0;
    for (const input of picked) {
      const note = input.closest("li").querySelector('[data-w="slotof"]');
      const slot = input.checked && plan ? slots[n++] : null;
      note.textContent = !input.checked ? "" : slot ? `→ ${slot.day} ${slot.slot}` : plan ? "→ no free slot: waits in Upload" : "→ waits in Upload";
    }
    const btn = box.querySelector('button[data-w="make"]');
    if (btn) {
      const count = picked.filter((i) => i.checked).length;
      btn.disabled = !count;
      btn.textContent = `🎬 Make ${count} video${count === 1 ? "" : "s"}`;
    }
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
    const left = makeable(s);
    if (["ready", "made"].includes(s.step) && left.length) {
      body += `<div class="bw-w-step"><b>✅ Tick the questions to make (from Level ${s.level})</b><span class="hint">Each becomes the next level, over new no-copyright ${esc(s.game)} gameplay, one after the other</span>
        <div class="bw-w-make"><label class="hint"><input type="checkbox" data-w="plan" checked> Plan each in the next free slot</label>
        <button type="button" class="btn-primary" data-w="make">🎬 Make videos</button></div><ul class="hint" data-w="jobs"></ul></div>`;
    } else if (s.step === "made") {
      body += '<div class="bw-w-step"><b>🎬 Every question that passed is a video</b><span class="hint">They are in Upload, and in Plan when planned. Start a new question for more.</span></div>';
    }
    box.innerHTML = `<div class="bw-w-head"><b>${esc(s.game)}</b><span class="hint">Shape: ${esc(SHAPES[s.shape] || s.shape)}</span>${steps}</div>
      ${questions(s)}${body}`;
    showSlots();
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

  // One line per video; the panel reloads once every job has finished.
  async function watchMake(jobs, list) {
    const states = await Promise.all(jobs.map((j) => api(`/api/create/jobs/${encodeURIComponent(j.id)}`).then((r) => r.body)));
    list.innerHTML = states.map((job, n) => {
      const q = current.questions[jobs[n].index];
      const where = jobs[n].day ? ` → ${jobs[n].day} ${jobs[n].slot}` : "";
      const state = job.status === "done" ? "✅ made" : job.status === "error" ? `❌ ${esc(job.error)}`
        : job.status === "queued" ? "waiting" : `${esc(job.message)}${job.progress != null ? ` (${Math.round(job.progress * 100)}%)` : ""}…`;
      return `<li>${esc(q.question)}${esc(where)}: ${state}</li>`;
    }).join("");
    if (states.some((j) => j.status === "queued" || j.status === "running")) return setTimeout(() => watchMake(jobs, list), 3000);
    const failed = states.filter((j) => j.status === "error").length;
    await open(current.id);
    load();
    if (failed) showError(`${failed} video${failed === 1 ? "" : "s"} failed; ${failed === 1 ? "its question is" : "their questions are"} still ticked, try again.`);
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
      const indices = [...box.querySelectorAll('input[data-w="pick"]:checked')].map((i) => Number(i.value));
      const plan = box.querySelector('input[data-w="plan"]').checked;
      if (!indices.length) return;
      return act(async () => {
        btn.disabled = true;
        box.querySelectorAll('input[data-w]').forEach((i) => { i.disabled = true; });
        const { body } = await post(`/api/gamer/manual/${encodeURIComponent(current.id)}/make`, { indices, plan });
        watchMake(body.jobs, box.querySelector('[data-w="jobs"]'));
      });
    }
  });

  box.addEventListener("change", (e) => {
    if (e.target.matches('input[data-w="pick"], input[data-w="plan"]')) showSlots();
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
