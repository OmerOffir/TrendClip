// Dark Outliers "Write it myself": you carry each prompt to any LLM and paste its answer back; the rules check,
// the fact check against the story, then the video for a slot (modules/da_manual.py).
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const box = $("daWSession");
  if (!box) return;
  const error = $("daWError");
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const STORE = "daManualSession";
  const STEPS = [["write", "1 · Write"], ["facts", "2 · Fact check"], ["ready", "3 · Make the video"]];
  const NOW = { write: "write", rejected: "write", fix: "write", facts: "facts", facts_fix: "write", ready: "ready", made: "ready" };
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
        <textarea class="bw-w-prompt" readonly rows="6">${esc(s.prompt)}</textarea>
        <button type="button" class="btn-secondary" data-w="copy">📋 Copy the prompt</button></div>`;
  }

  function pasteBlock(label, act, button) {
    return `<div class="bw-w-step"><b>${label}</b>
        <textarea class="bw-w-answer" rows="6" placeholder="Paste the whole answer here (the JSON)"></textarea>
        <button type="button" class="btn-primary" data-w="${act}">${button}</button></div>`;
  }

  function preview(p) {
    if (!p) return "";
    return `<details class="bw-w-preview"${["ready", "facts"].includes(current.step) ? " open" : ""}>
      <summary><b>${esc(p.title)}</b> · cover “${esc(p.cover_text)}” · ${p.words} words · ${p.scenes.length} scenes</summary>
      <ol><li><span class="pill">hook</span> ${esc(p.hook)}</li>${p.scenes.map((s) => `<li><span class="pill">${esc(s.overlay)}</span> ${esc(s.narration)}</li>`).join("")}</ol></details>`;
  }

  function slotPicker() {
    return slots.length
      ? `<select data-w="slot" aria-label="Slot">${slots.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("")}</select>`
      : '<span class="hint">no open slot this week: the next free slot is used</span>';
  }

  function render(s) {
    current = s;
    remember(s.id);
    const at = NOW[s.step] || "write";
    const steps = STEPS.map(([key, label]) => `<span class="pill${key === at ? " good" : ""}">${label}</span>`).join(" ");
    const source = s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.source)}</a>` : esc(s.source);
    let body = "";
    if (s.step === "rejected") body += `<div class="da-note">The LLM said the story can't be told: ${esc(s.problems.join("; "))}. It's marked Rejected in the bank; pick another story.</div>`;
    if (s.step === "fix") body += `<div class="da-note"><b>The draft missed the rules:</b><ul>${s.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>The prompt below asks the LLM to fix them.</div>`;
    if (s.step === "facts_fix") body += `<div class="da-note"><b>The fact checker flagged:</b><ul>${s.flagged.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>The prompt below asks the writer to fix them; the fixed draft is checked again.</div>`;
    if (["write", "fix", "facts_fix"].includes(s.step)) {
      body += promptBlock(s, s.step === "write" ? "Copy this prompt into your LLM" : "Copy this fix prompt into the same LLM");
      body += pasteBlock("Paste its answer", "answer", "🔎 Check the draft");
    }
    if (s.step === "facts") {
      body += '<div class="hint">✅ The draft passed the rules (length, sentences, scenes).</div>';
      body += promptBlock(s, "Copy this fact-check prompt into a second LLM (another one than the writer is best)");
      body += pasteBlock("Paste the fact checker's answer", "facts", "🔎 Check the facts");
    }
    if (s.step === "ready") {
      body += `<div class="bw-w-step"><b>✅ Both checks passed: the script is saved</b><span class="hint">The video is made here (voice, pictures, motion), planned for the slot and posted in Discord with "Don't post this"</span>
        <div class="bw-w-make">${slotPicker()}<button type="button" class="btn-primary" data-w="make">🎬 Make the video</button><span class="hint" data-w="status"></span></div></div>`;
    }
    if (s.step === "made") body += `<div class="bw-w-step"><b>🎬 Made${s.planned ? ` and planned for ${esc(s.planned)}` : ""}</b><span class="hint">It's in Plan and in Discord; it goes out by itself</span></div>`;
    box.innerHTML = `<div class="bw-w-head"><b>${esc(s.title)}</b><span class="hint">story ${s.story_id} · ${source}${s.pillar ? ` · ${esc(s.pillar)}` : ""}</span>${steps}</div>
      ${preview(s.preview)}${body}`;
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

  async function watchMake(status) {
    const { body: job } = await api("/api/darkarchives/video-job");
    status.textContent = job.running ? `${job.message || "Working"}${job.progress != null ? ` (${Math.round(job.progress * 100)}%)` : ""}…`
      : job.error ? `❌ ${job.error}` : "Done";
    if (job.running) return setTimeout(() => watchMake(status), 3000);
    if (!job.error) open(current.id);
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
          render((await post(`/api/darkarchives/manual/${encodeURIComponent(current.id)}/${what}`, { text })).body);
        } finally {
          btn.disabled = false;
        }
      });
    }
    if (what === "make") {
      const sel = box.querySelector('select[data-w="slot"]');
      const [day, slot] = sel ? sel.value.split(" ") : [];
      if (!confirm(`Make the video and plan it for ${day ? `${day} ${slot}` : "the next free slot"}?`)) return;
      return act(async () => {
        btn.disabled = true;
        await post(`/api/darkarchives/manual/${encodeURIComponent(current.id)}/make`, day ? { day, slot } : {});
        watchMake(box.querySelector('[data-w="status"]'));
      });
    }
  });

  async function open(id) {
    try {
      render((await api(`/api/darkarchives/manual/${encodeURIComponent(id)}`)).body);
    } catch {
      remember(null);
      box.innerHTML = "";
    }
  }

  async function load() {
    try {
      const { body } = await api("/api/darkarchives/manual");
      slots = body.slots;
      $("daWStory").innerHTML = body.stories.map((s) => `<option value="${s.next ? "" : s.id}">${s.next ? "Next: " : ""}${esc(s.title.slice(0, 90))} · ${s.score}${s.pillar ? ` · ${esc(s.pillar)}` : ""}</option>`).join("")
        || '<option value="">No NEW stories: fetch more in Stories</option>';
      const sessions = $("daWSessions");
      sessions.innerHTML = body.sessions.map((s) => `<option value="${esc(s.id)}">${esc(s.title.slice(0, 70))} · ${esc(s.step)}</option>`).join("");
      sessions.hidden = $("daWOpen").hidden = !body.sessions.length;
    } catch (err) {
      showError(err.message);
    }
    const last = recalled();
    if (last && (!current || current.id !== last)) open(last);
    else if (current) render(current);
  }

  // A story from the Stories list ("✍️ Write it myself" on its card) opens here.
  window.TrendClip.writeDarkStory = (storyId) => act(async () => {
    render((await post("/api/darkarchives/manual", { story_id: storyId })).body);
    load();
  });

  $("daWStart").addEventListener("click", (e) => act(async () => {
    const id = $("daWStory").value;
    e.target.disabled = true;
    try {
      render((await post("/api/darkarchives/manual", id ? { story_id: Number(id) } : {})).body);
      load();
    } finally {
      e.target.disabled = false;
    }
  }));
  $("daWOpen").addEventListener("click", () => open($("daWSessions").value));
  document.querySelector('[data-da-view="write"]')?.addEventListener("click", load);
})();
