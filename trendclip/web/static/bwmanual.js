// Borderline Why "Write it myself": the code reads the sources, you carry each prompt to any LLM and paste its
// answer back; the rules check, the fact check, then the video (modules/bw_manual.py).
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const box = $("bwWSession");
  if (!box) return;
  const error = $("bwWError");
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const STORE = "bwManualSession";
  const STEPS = [["write", "1 · Write"], ["facts", "2 · Fact check"], ["ready", "3 · Make the video"]];
  const NOW = { write: "write", rejected: "write", fix: "write", facts: "facts", facts_fix: "write", ready: "ready", made: "ready" };
  let current = null;
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
      <summary><b>${esc(p.title)}</b> · cover “${esc(p.cover_text)}” · ${p.words} words · ${p.scenes.length} scenes · ${p.claims} facts with quotes</summary>
      <ol>${p.scenes.map((s) => `<li><span class="pill">${esc(s.kind)}</span> ${esc(s.narration)}${s.elements.length ? ` <small class="hint">${esc(s.elements.join(", "))}</small>` : ""}</li>`).join("")}</ol></details>`;
  }

  async function slotPicker() {
    try {
      const { body } = await api("/api/borderlinewhy/plan");
      const now = body.now.slice(0, 16).replace("T", " ");
      const open = body.days.flatMap((d) => d.slots.filter((r) => !r.video && `${d.day} ${r.slot}` > now).map((r) => [d.day, r.slot]));
      return open.length
        ? `<select data-w="slot" aria-label="Slot">${open.map(([day, slot]) => `<option value="${esc(day)} ${esc(slot)}">${esc(day)} ${esc(slot)}</option>`).join("")}</select>`
        : '<span class="hint">no open slot this week: the next slot is used</span>';
    } catch {
      return "";
    }
  }

  async function render(s) {
    current = s;
    remember(s.id);
    const at = NOW[s.step] || "write";
    const steps = STEPS.map(([key, label]) => `<span class="pill${key === at ? " good" : ""}">${label}</span>`).join(" ");
    const sources = s.sources.map((src) => `<a href="${esc(src.url)}" target="_blank" rel="noopener">[${src.id}] ${esc(src.title)}</a>`).join(" · ");
    let body = "";
    if (s.step === "rejected") body += `<div class="da-note">The LLM said the sources can't support it: ${esc(s.problems.join("; "))}. Try again or pick another topic.</div>`;
    if (s.step === "fix") body += `<div class="da-note"><b>The draft missed the rules:</b><ul>${s.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>The prompt below asks the LLM to fix them.</div>`;
    if (s.step === "facts_fix") body += `<div class="da-note"><b>The fact checker flagged:</b><ul>${s.flagged.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>The prompt below asks the writer to fix them; the fixed draft is checked again.</div>`;
    if (["write", "rejected", "fix", "facts_fix"].includes(s.step)) {
      body += promptBlock(s, s.step === "write" || s.step === "rejected" ? "Copy this prompt into your LLM" : "Copy this fix prompt into the same LLM");
      body += pasteBlock("Paste its answer", "answer", "🔎 Check the draft");
    }
    if (s.step === "facts") {
      body += '<div class="hint">✅ The draft passed the rules and every quote is in its source.</div>';
      body += promptBlock(s, "Copy this fact-check prompt into a second LLM (another one than the writer is best)");
      body += pasteBlock("Paste the fact checker's answer", "facts", "🔎 Check the facts");
    }
    if (s.step === "ready") {
      body += `<div class="bw-w-step"><b>✅ Both checks passed</b><span class="hint">The video is made here with the voice and the satellite look, then posted in Discord for Approve</span>
        <div class="bw-w-make">${await slotPicker()}<button type="button" class="btn-primary" data-w="make">🎬 Make the video</button><span class="hint" data-w="status"></span></div></div>`;
    }
    if (s.step === "made") body += `<div class="bw-w-step"><b>🎬 Made: ${esc(s.video || "")}</b><span class="hint">It's in Plan and in Discord, waiting for Approve</span></div>`;
    box.innerHTML = `<div class="bw-w-head"><b>${esc(s.question)}</b><span class="hint">Wikipedia: ${esc(s.article)}</span>${steps}</div>
      <div class="hint bw-w-sources">Sources: ${sources}</div>${preview(s.preview)}${body}`;
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
    const { body: job } = await api("/api/borderlinewhy/make-job");
    status.textContent = job.running ? `${job.message}${job.fraction != null ? ` (${Math.round(job.fraction * 100)}%)` : ""}…`
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
          render((await post(`/api/borderlinewhy/manual/${encodeURIComponent(current.id)}/${what}`, { text })).body);
        } finally {
          btn.disabled = false;
        }
      });
    }
    if (what === "make") {
      const sel = box.querySelector('select[data-w="slot"]');
      const [day, slot] = sel ? sel.value.split(" ") : [];
      if (!confirm(`Make the video${day ? ` for ${day} ${slot}` : " for the next slot"}? A video already made for that slot (not uploaded) is set aside.`)) return;
      return act(async () => {
        btn.disabled = true;
        await post(`/api/borderlinewhy/manual/${encodeURIComponent(current.id)}/make`, day ? { day, slot } : {});
        watchMake(box.querySelector('[data-w="status"]'));
      });
    }
  });

  async function open(id) {
    try {
      render((await api(`/api/borderlinewhy/manual/${encodeURIComponent(id)}`)).body);
    } catch (err) {
      remember(null);
      box.innerHTML = "";
    }
  }

  async function load() {
    try {
      const { body } = await api("/api/borderlinewhy/manual");
      $("bwWTopic").innerHTML = '<option value="">The next topic on the list</option>'
        + body.topics.map((t) => `<option value="${esc(t.article)}" data-q="${esc(t.question)}">${esc(t.question)}</option>`).join("");
      const sessions = $("bwWSessions");
      sessions.innerHTML = body.sessions.map((s) => `<option value="${esc(s.id)}">${esc(s.question)} · ${esc(s.step)}</option>`).join("");
      sessions.hidden = $("bwWOpen").hidden = !body.sessions.length;
    } catch (err) {
      showError(err.message);
    }
    const last = recalled();
    if (last && (!current || current.id !== last)) open(last);
  }

  $("bwWStart").addEventListener("click", (e) => act(async () => {
    const sel = $("bwWTopic");
    const opt = sel.selectedOptions[0];
    e.target.disabled = true;
    box.innerHTML = '<div class="empty">Reading Wikipedia and the pages it cites (about a minute)…</div>';
    try {
      render((await post("/api/borderlinewhy/manual", { article: sel.value, question: sel.value ? opt.dataset.q : "" })).body);
      load();
    } catch (err) {
      box.innerHTML = "";
      throw err;
    } finally {
      e.target.disabled = false;
    }
  }));
  $("bwWOpen").addEventListener("click", () => open($("bwWSessions").value));
  document.querySelector('[data-bw-view="write"]')?.addEventListener("click", load);
})();
