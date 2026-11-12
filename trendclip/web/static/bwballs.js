// Borderline Why "Talking flag balls": a batch of balls Shorts written by your own LLM. Get the prompt, paste the
// LLM's JSON back (the rules check), then the fact-check prompt and its answer; what passes goes to the queue
// (modules/bw_balls_write.py).
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const box = $("bwBallsSession");
  if (!box) return;
  const error = $("bwBallsError");
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const showError = (msg) => { error.textContent = msg || ""; error.hidden = !msg; };
  const STEP = { write: "1 · Copy this prompt into any LLM", fix: "Copy this prompt back into the LLM (it carries what to fix)",
    facts: "2 · The rules pass ✅. Copy this fact-check prompt into a second LLM" };

  async function copy(textarea, btn) {  // the hub's dashboard is plain http on the LAN: select-and-copy too
    try {
      await navigator.clipboard.writeText(textarea.value);
    } catch {
      textarea.select();
      document.execCommand("copy");
    }
    btn.textContent = "✅ Copied";
    setTimeout(() => { btn.textContent = "📋 Copy the prompt"; }, 2000);
  }

  function render(v) {
    $("bwBallsStatus").textContent = `${v.waiting} waiting in the queue${v.slot ? ` · the ${v.slot} slot makes one a day` : " · no balls slot is set (BW_BALLS_SLOT)"}`;
    if (v.step === "none") {
      box.innerHTML = `${(v.queued || []).length ? `<p>📥 ${v.queued.length} script${v.queued.length === 1 ? "" : "s"} added to the queue.</p>` : ""}
        <form class="bw-add" data-x="start">
          <label class="hint">Scripts <input type="number" id="bwBallsCount" min="1" max="10" value="7" style="width:70px"></label>
          <button type="submit" class="btn-primary">Get the prompt</button>
        </form>
        <textarea id="bwBallsIdeas" rows="2" placeholder="Your own questions, one per line (optional): Which country has the most volcanoes?"></textarea>`;
      return;
    }
    const drafts = (v.drafts || []).map((d, i) => `<div class="pw-slot"><span class="pw-time">${i + 1}</span>
        ${d.problems.length ? `<span class="pill warn">❌ ${v.flagged ? "facts" : "rules"}</span>` : '<span class="pill good">✅</span>'}
        <span class="pw-what"><b>${esc(d.title)}</b> <span class="hint">${d.words} words · ${d.lines} lines</span>
        ${d.problems.length ? `<span class="da-note">${esc(d.problems.join("; "))}</span>` : ""}</span></div>`).join("");
    const general = (v.general || []).length ? `<div class="da-note">${esc(v.general.join("; "))}</div>` : "";
    const paste = v.step === "facts" ? "facts" : "answer";
    const work = v.step === "ready"
      ? `<div class="bw-w-step"><b>✅ ${v.skipped_facts ? "The rules pass (you skipped the fact check)" : "Both checks passed"}</b>
          <button type="button" class="btn-primary" data-x="queue">📥 Put them in the queue</button></div>`
      : `<div class="bw-w-step"><b>${STEP[v.step] || ""}</b>
          <textarea class="bw-w-prompt" readonly rows="6">${esc(v.prompt)}</textarea>
          <button type="button" class="btn-secondary" data-x="copy">📋 Copy the prompt</button></div>
        <div class="bw-w-step"><b>Paste its answer (the JSON)</b>
          <textarea class="bw-w-answer" rows="5" placeholder="Paste the whole answer here"></textarea>
          <button type="button" class="btn-primary" data-x="${paste}">Check it</button>
          ${v.step === "facts" ? '<button type="button" class="btn-secondary" data-x="skip-facts" title="Only when you checked the numbers yourself">Skip the fact check</button>' : ""}</div>`;
    box.innerHTML = `${general}${drafts}${work}
      <p><button type="button" class="btn-secondary" data-x="drop">✖ Throw this batch away</button></p>`;
  }

  async function load() {
    try {
      const { body } = await api("/api/borderlinewhy/balls");
      showError("");
      render(body);
    } catch (err) {
      showError(err.message);
    }
  }

  box.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const { body } = await post("/api/borderlinewhy/balls", { count: Number($("bwBallsCount").value) || 7, ideas: $("bwBallsIdeas").value });
      showError("");
      render(body);
    } catch (err) {
      showError(err.message);
    }
  });

  box.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-x]");
    if (!btn) return;
    const what = btn.dataset.x;
    if (what === "copy") return copy(box.querySelector(".bw-w-prompt"), btn);
    if (what === "drop" && !window.confirm("Throw this batch of scripts away?")) return;
    btn.disabled = true;
    try {
      const text = (box.querySelector(".bw-w-answer") || {}).value || "";
      const { body } = await post(`/api/borderlinewhy/balls/${what}`, { text });
      showError("");
      render(body);
    } catch (err) {
      showError(err.message);
      btn.disabled = false;
    }
  });

  load();
})();
