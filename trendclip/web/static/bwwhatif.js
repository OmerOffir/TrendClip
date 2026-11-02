// Borderline Why "What if…?": type the question, carry the prompt to any LLM, paste its JSON back; a script that
// passes the check goes to the queue (modules/bw_whatif.py). Also the Topics tab's "Shaped Like This: add a country".
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const box = $("bwWhatIfSession");
  if (!box) return;
  const error = $("bwWhatIfError");
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const STEP = { write: "✍️ write", fix: "🔧 fix", rejected: "✖ rejected", ready: "✅ ready", queued: "📥 queued" };
  let current = null;

  const showError = (msg) => { error.textContent = msg || ""; error.hidden = !msg; };

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

  function render(s) {
    current = s;
    const p = s.preview;
    const problems = s.problems.length ? `<div class="da-note"><b>${s.step === "rejected" ? "The LLM turned it down" : "The draft missed the rules"}:</b><ul>${s.problems.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>${s.step === "fix" ? "The prompt below asks the LLM to fix them." : ""}</div>` : "";
    const preview = p ? `<div class="bw-w-step"><b>${esc(p.title)}</b>
        <span class="hint">${p.words} words · ${p.scenes} scenes · ${s.format === "attempts" ? `${p.armies} armies · ${p.battles} battles` : `${p.transfers} transfer${p.transfers === 1 ? "" : "s"}`} · cover: ${esc(p.cover_text)}</span>
        <p class="hint">${esc(p.narration)}</p></div>` : "";
    const prompt = s.prompt ? `<div class="bw-w-step"><b>1 · Copy this prompt into any LLM${s.step === "fix" ? " (it carries the problems to fix)" : ""}</b>
        <textarea class="bw-w-prompt" readonly rows="6">${esc(s.prompt)}</textarea>
        <button type="button" class="btn-secondary" data-x="copy">📋 Copy the prompt</button></div>
      <div class="bw-w-step"><b>2 · Paste its answer (the JSON)</b>
        <textarea class="bw-w-answer" rows="5" placeholder="Paste the whole answer here"></textarea>
        <button type="button" class="btn-primary" data-x="answer">Check it</button></div>` : "";
    const queue = s.step === "ready" ? `<div class="bw-w-step"><b>3 · It passed the check</b>
        <button type="button" class="btn-primary" data-x="queue">📥 Put it in the queue (the next slot makes it)</button></div>` : "";
    const queued = s.queued ? `<p class="hint">📥 In the queue since ${esc(s.queued.at)} (${esc(s.queued.name)}): the next slot makes it.</p>` : "";
    box.innerHTML = `<p><b>${esc(s.question)}</b> <span class="pill">${STEP[s.step] || esc(s.step)}</span></p>${problems}${preview}${prompt}${queue}${queued}`;
  }

  async function open(id) {
    if (!id) return;
    try {
      const { body } = await api(`/api/borderlinewhy/whatif/${encodeURIComponent(id)}`);
      showError("");
      render(body);
    } catch (err) {
      showError(err.message);
    }
  }

  async function loadSessions() {
    try {
      const { body } = await api("/api/borderlinewhy/whatif");
      $("bwWhatIfSessions").innerHTML = `<option value="">Earlier what-ifs…</option>`
        + body.sessions.map((s) => `<option value="${esc(s.id)}">${esc(s.question)} (${esc(STEP[s.step] || s.step)})</option>`).join("");
    } catch { /* the list is a convenience */ }
  }

  $("bwWhatIfStart").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const { body } = await post("/api/borderlinewhy/whatif", { question: $("bwWhatIfQuestion").value, format: $("bwWhatIfFormat").value });
      showError("");
      render(body);
      loadSessions();
    } catch (err) {
      showError(err.message);
    }
  });
  $("bwWhatIfSessions").addEventListener("change", (e) => open(e.target.value));
  $("bwWhatIfFormat").addEventListener("change", (e) => {
    $("bwWhatIfQuestion").placeholder = e.target.value === "attempts" ? "4 times the US tried to take Canada" : "What if Alaska was still Russian?";
  });

  box.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-x]");
    if (!btn || !current) return;
    const what = btn.dataset.x;
    if (what === "copy") return copy(box.querySelector(".bw-w-prompt"), btn);
    btn.disabled = true;
    try {
      const path = `/api/borderlinewhy/whatif/${encodeURIComponent(current.id)}`;
      const { body } = what === "answer"
        ? await post(`${path}/answer`, { text: box.querySelector(".bw-w-answer").value })
        : await post(`${path}/queue`);
      showError("");
      render(body);
      loadSessions();
    } catch (err) {
      showError(err.message);
      btn.disabled = false;
    }
  });

  $("bwCountry").addEventListener("submit", async (e) => {
    e.preventDefault();
    const status = $("bwCountryStatus");
    status.textContent = "Checking the map and Wikipedia…";
    try {
      const { body } = await post("/api/borderlinewhy/topics/country", { country: $("bwCountryName").value });
      status.textContent = `✅ "${body.question}" added (Wikipedia: ${body.article}); it is made next`;
      $("bwCountryName").value = "";
      $("bwTopicsRefresh").click();
    } catch (err) {
      status.textContent = `❌ ${err.message}`;
    }
  });

  loadSessions();
})();
