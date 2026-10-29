// Borderline Why "Write a week": the code reads the sources of the next topics, you carry ONE prompt to any LLM and
// paste its answer back; the rules check and the fact check run on every script; the ready ones go to Linux's queue
// (modules/bw_week.py).
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const box = $("bwKSession");
  if (!box) return;
  const error = $("bwKError");
  const jobLine = $("bwKJob");
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const STEP = {
    write: ["to write", ""], fix: ["missed the rules → fix", "warn"], facts: ["waits for the fact check", ""],
    facts_fix: ["flagged by the fact checker → fix", "warn"], ready: ["✅ ready", "good"], sent: ["📤 on Linux", "good"],
    rejected: ["the LLM said the sources can't support it", "warn"], dropped: ["left out", ""],
  };
  let current = null;
  let busy = false;
  let sender = false;

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

  function promptBlock(title, hint, text, chars, act, button) {
    return `<div class="bw-w-step"><b>${title}</b>
        <span class="hint">${hint} · ${chars.toLocaleString()} characters</span>
        <textarea class="bw-w-prompt" readonly rows="5">${esc(text)}</textarea>
        <button type="button" class="btn-secondary" data-k="copy">📋 Copy the prompt</button>
        <b>Paste its answer</b>
        <textarea class="bw-w-answer" rows="5" placeholder="Paste the whole answer here (the JSON)"></textarea>
        <button type="button" class="btn-primary" data-k="${act}">${button}</button></div>`;
  }

  function topicRow(t) {
    const [text, cls] = STEP[t.step] || [t.step, ""];
    const p = t.preview;
    const lists = [];
    if (t.problems.length) lists.push(`<ul>${t.problems.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`);
    if (t.flagged.length) lists.push(`<ul>${t.flagged.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`);
    const preview = p ? `<details><summary>${esc(p.title)} · ${p.words} words · ${p.scenes} scenes · ${p.claims} facts</summary>
        <div class="hint">${esc(p.narration)}</div></details>` : "";
    const sources = t.sources.map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">[${s.id}]</a>`).join(" ");
    const drop = ["write", "fix", "facts", "facts_fix", "ready", "rejected"].includes(t.step)
      ? `<button type="button" class="btn-ghost" data-k="drop" data-i="${t.i}" title="Leave this topic out of the week">✕</button>` : "";
    return `<div class="bw-k-topic ${esc(t.step)}">
      <span class="num">${t.i}</span>
      <span class="what"><span>${esc(t.question)} <small>${sources}</small></span>${preview}${lists.join("")}${t.sent ? `<small>queued as ${esc(t.sent.name || "")}</small>` : ""}</span>
      <span class="actions"><span class="pill ${cls}">${text}</span>${drop}</span>
    </div>`;
  }

  function render(w) {
    current = w;
    if (!w) {
      box.innerHTML = '<div class="empty">No week started yet. Pick how many topics and read their sources.</div>';
      return;
    }
    const prompts = [];
    if (w.to_write) {
      prompts.push(promptBlock(`1 · Write${w.topics.some((t) => ["fix", "facts_fix"].includes(t.step)) ? " / fix" : ""} (${w.to_write} topic${w.to_write > 1 ? "s" : ""})`,
        "Copy into any LLM; the answer is one JSON with every script", w.write_prompt, w.write_chars, "answer", "🔎 Check the scripts"));
    }
    if (w.to_check) {
      prompts.push(promptBlock(`2 · Fact check (${w.to_check} script${w.to_check > 1 ? "s" : ""})`,
        "Best in a second LLM, another one than the writer", w.facts_prompt, w.facts_chars, "facts", "🔎 Check the facts"));
    }
    let send = "";
    if (w.ready) {
      const where = sender ? "Linux" : "this machine's queue";
      send = `<div class="bw-w-step"><b>3 · ${w.ready} script${w.ready > 1 ? "s" : ""} passed both checks</b>
        <span class="hint">They go to ${where}, which makes one video per slot from them, oldest first: no Gemini needed.</span>
        <button type="button" class="btn-primary" data-k="send">📤 Send to ${esc(where)}</button></div>`;
    }
    const last = w.last_send ? `<div class="hint">Last send ${esc(w.last_send.at.slice(0, 16).replace("T", " "))}: ${w.last_send.queued.length} queued
      ${w.last_send.slots && w.last_send.slots.length ? `for ${w.last_send.slots.map((s) => `${s.date} ${s.slot}`).join(", ")}` : ""}
      ${w.last_send.skipped.length ? ` · ${w.last_send.skipped.length} skipped` : ""} · ${w.last_send.in_queue} in Linux's queue</div>` : "";
    box.innerHTML = `<div class="bw-w-head"><b>${w.topics.length} topics</b>
        <span class="pill">${w.to_write} to write</span><span class="pill">${w.to_check} to fact-check</span>
        <span class="pill good">${w.ready} ready</span><span class="pill good">${w.sent} on Linux</span>
        <span class="hint">started ${esc(w.created_at.slice(0, 16).replace("T", " "))}</span></div>
      ${last}
      <div class="bw-k-topics">${w.topics.map(topicRow).join("")}</div>
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
      const { body } = await api("/api/borderlinewhy/week");
      sender = body.sender;
      render(body.week);
      watchJob(body.job);
    } catch (err) {
      showError(err.message);
    }
  }

  function watchJob(job) {
    if (!job) return;
    if (job.running) {
      jobLine.textContent = `${job.message}…`;
      $("bwKStart").disabled = true;
      return setTimeout(async () => {
        try {
          const { body } = await api("/api/borderlinewhy/week");
          render(body.week);
          watchJob(body.job);
        } catch (err) {
          showError(err.message);
        }
      }, 3000);
    }
    $("bwKStart").disabled = false;
    jobLine.textContent = job.error ? `❌ ${job.error}` : "";
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
          render((await post(`/api/borderlinewhy/week/${what}`, { text })).body);
        } finally {
          btn.disabled = false;
        }
      });
    }
    if (what === "drop") {
      return act(async () => { render((await post(`/api/borderlinewhy/week/drop/${btn.dataset.i}`)).body); });
    }
    if (what === "send") {
      if (!confirm(`Send the ${current.ready} ready script(s)? Linux makes one video per slot from them, oldest first.`)) return;
      return act(async () => {
        btn.disabled = true;
        btn.textContent = "Sending…";
        try {
          render((await post("/api/borderlinewhy/week/send")).body);
        } finally {
          btn.disabled = false;
        }
      });
    }
  });

  $("bwKStart").addEventListener("click", () => act(async () => {
    if (current && current.topics.some((t) => !["sent", "dropped", "rejected"].includes(t.step))
        && !confirm("Start a new week? The current one, with its drafts, is replaced.")) return;
    const count = Math.max(1, Math.min(14, parseInt($("bwKCount").value, 10) || 7));
    const { body } = await post("/api/borderlinewhy/week", { count });
    box.innerHTML = '<div class="empty">Reading Wikipedia and the pages it cites for each topic (a minute or two)…</div>';
    watchJob(body);
  }));
  document.querySelector('[data-bw-view="week"]')?.addEventListener("click", load);
})();
