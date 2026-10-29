// SideQuestLogic "Write a week": Gamer IQ questions for several games at once. You carry ONE prompt to any LLM and paste
// its answer back; the shape checks run here, then one fact-check prompt; the questions that pass go to Linux's queue,
// whose production run asks them as the next levels before asking Gemini (trendclip/gamer_week.py).
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const box = $("gkSession");
  if (!box) return;
  const error = $("gkError");
  const info = $("gkInfo");
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const STEP = {
    facts: ["waits for the fact check", ""], ready: ["✅ passed", "good"], sent: ["📤 on Linux", "good"],
    rejected: ["❌ failed the fact check", "warn"], dropped: ["left out", ""],
  };
  let current = null;
  let busy = false;
  let sender = false;
  let defaultsSet = false;

  const showError = (msg) => { error.textContent = msg || ""; error.hidden = !msg; };

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

  function promptBlock(title, hint, text, chars, act, button, placeholder) {
    return `<div class="bw-w-step"><b>${title}</b>
        <span class="hint">${hint} · ${chars.toLocaleString()} characters</span>
        <textarea class="bw-w-prompt" readonly rows="5">${esc(text)}</textarea>
        <button type="button" class="btn-secondary" data-k="copy">📋 Copy the prompt</button>
        <b>Paste its answer</b>
        <textarea class="bw-w-answer" rows="5" placeholder="${placeholder}"></textarea>
        <button type="button" class="btn-primary" data-k="${act}">${button}</button></div>`;
  }

  function questionRow(q) {
    const [text, cls] = STEP[q.step] || [q.step, ""];
    const said = q.spoken && q.spoken !== q.question ? `<small>Spoken: “${esc(q.spoken)}”</small>` : "";
    const why = q.why ? `<ul><li>${esc(q.why)}</li></ul>` : "";
    const drop = ["facts", "ready", "rejected"].includes(q.step)
      ? `<button type="button" class="btn-ghost" data-k="drop" data-i="${q.i}" title="Leave this question out of the week">✕</button>` : "";
    return `<div class="bw-k-topic ${esc(q.step)}">
      <span class="num">${q.i}</span>
      <span class="what"><span><b>${esc(q.game)}</b> · ${esc(q.shape)} · ${esc(q.question)} <small>${esc(q.options.join(" / "))} → <b>${esc(q.correct)}</b></small></span>
        ${said}${why}${q.sent ? `<small>queued as ${esc(q.sent.name || "")}</small>` : ""}</span>
      <span class="actions"><span class="pill ${cls}">${text}</span>${drop}</span>
    </div>`;
  }

  function render(w) {
    current = w;
    if (!w) {
      box.innerHTML = '<div class="empty">No week started yet. Say which games and how many levels each.</div>';
      return;
    }
    const prompts = [];
    if (w.to_write) {
      const need = w.need.map((n) => `${n.game} ${n.missing}`).join(", ");
      prompts.push(promptBlock(`1 · Write (${need})`, "Copy into any LLM; the answer is one JSON with every question", w.write_prompt, w.write_chars,
        "answer", "🔎 Check the questions", "Paste the whole answer here (the JSON)"));
    }
    if (w.to_check) {
      prompts.push(promptBlock(`2 · Fact check (${w.to_check} question${w.to_check > 1 ? "s" : ""})`,
        "Best in a second LLM with web search", w.facts_prompt, w.facts_chars, "facts", "🔎 Read the verdicts", 'Paste its "1: PASS" / "2: FAIL: why" lines'));
    }
    let send = "";
    if (w.ready) {
      const where = sender ? "Linux" : "this machine's queue";
      send = `<div class="bw-w-step"><b>3 · ${w.ready} question${w.ready > 1 ? "s" : ""} passed the fact check</b>
        <span class="hint">They go to ${where}: the production run asks them as the next levels, oldest first, before asking Gemini.</span>
        <button type="button" class="btn-primary" data-k="send">📤 Send to ${esc(where)}</button></div>`;
    }
    const problems = w.problems.length ? `<div class="da-note"><b>Not taken from the last answer</b><ul>${w.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>` : "";
    const ls = w.last_send;
    const last = ls ? `<div class="hint">Last send ${esc(ls.at.slice(0, 16).replace("T", " "))}: ${ls.queued.length} queued${ls.skipped.length ? `, ${ls.skipped.length} skipped` : ""}
      · waiting on Linux: ${Object.entries(ls.in_queue || {}).map(([g, n]) => `${esc(g)} ${n}`).join(", ") || "none"}</div>` : "";
    box.innerHTML = `<div class="bw-w-head"><b>${w.games.map((g) => `${esc(g.game)} × ${g.count}`).join(", ")}</b>
        <span class="pill">${w.to_write} to write</span><span class="pill">${w.to_check} to fact-check</span>
        <span class="pill good">${w.ready} ready</span><span class="pill good">${w.sent} on Linux</span>
        <span class="hint">started ${esc(w.created_at.slice(0, 16).replace("T", " "))} · next here: Level ${w.level}</span></div>
      ${last}${problems}
      <div class="bw-k-topics">${w.questions.map(questionRow).join("")}</div>
      <div class="bw-k-grid">${prompts.join("")}</div>${send}`;
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

  async function load() {
    try {
      const { body } = await api("/api/gamer/week");
      sender = body.sender;
      if (!defaultsSet) {
        const daily = body.daily_game || body.games[0] || "Minecraft";
        $("gkGames").value = `${daily} 7`;
        defaultsSet = true;
      }
      const queue = Object.entries(body.in_queue || {}).map(([g, n]) => `${g} ${n}`).join(", ");
      info.textContent = `Gameplay here: ${body.games.join(", ")}` + (queue ? ` · waiting here: ${queue}` : "");
      render(body.week);
    } catch (err) {
      showError(err.message);
    }
  }

  box.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-k]");
    if (!btn) return;
    const what = btn.dataset.k;
    if (what === "copy") return copy(btn.parentElement.querySelector("textarea.bw-w-prompt"), btn);
    if (what === "answer" || what === "facts") {
      const text = btn.parentElement.querySelector("textarea.bw-w-answer").value;
      if (!text.trim()) return;
      return act(async () => {
        btn.disabled = true;
        btn.textContent = "Checking…";
        try {
          render((await post(`/api/gamer/week/${what}`, { text })).body);
        } finally {
          btn.disabled = false;
        }
      });
    }
    if (what === "drop") {
      return act(async () => { render((await post(`/api/gamer/week/drop/${btn.dataset.i}`)).body); });
    }
    if (what === "send") {
      if (!confirm(`Send the ${current.ready} question(s)? Linux asks them as the next levels, oldest first, before asking Gemini.`)) return;
      return act(async () => {
        btn.disabled = true;
        btn.textContent = "Sending…";
        try {
          render((await post("/api/gamer/week/send")).body);
        } finally {
          btn.disabled = false;
        }
      });
    }
  });

  $("gkStart").addEventListener("click", () => act(async () => {
    if (current && current.questions.some((q) => ["facts", "ready"].includes(q.step))
        && !confirm("Start a new week? The current one, with its questions, is replaced.")) return;
    render((await post("/api/gamer/week", { games: $("gkGames").value })).body);
  }));
  document.addEventListener("trendclip:view", (e) => { if (e.detail.view === "week") load(); });
})();
