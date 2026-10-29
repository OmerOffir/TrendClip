// Dark Outliers "Write a week": the code picks the best stories of the bank (and the candidate groups for the rankings),
// you carry ONE prompt to any LLM and paste its answer back; the rules check runs on every script, the fact check on the
// stories; the ready ones go to Linux, whose production run uses them before asking Gemini (modules/da_week.py).
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const box = $("daKSession");
  if (!box) return;
  const error = $("daKError");
  const info = $("daKInfo");
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const STEP = {
    write: ["to write", ""], fix: ["missed the rules → fix", "warn"], facts: ["waits for the fact check", ""],
    facts_fix: ["flagged by the fact checker → fix", "warn"], ready: ["✅ ready", "good"], sent: ["📤 on Linux", "good"],
    rejected: ["the LLM said it can't be told", "warn"], dropped: ["left out", ""],
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

  const DROPPABLE = ["write", "fix", "facts", "facts_fix", "ready", "rejected"];

  function lists(t) {
    const out = [];
    if (t.problems.length) out.push(`<ul>${t.problems.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`);
    if (t.flagged && t.flagged.length) out.push(`<ul>${t.flagged.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`);
    return out.join("");
  }

  function storyRow(t) {
    const [text, cls] = STEP[t.step] || [t.step, ""];
    const p = t.preview;
    const preview = p ? `<details><summary>${esc(p.title)} · ${p.words} words · ${p.scenes} scenes · <i>${esc(p.cover_text)}</i></summary>
        <div class="hint">${esc(p.narration)}</div></details>` : "";
    const source = t.url ? `<a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(t.source)}</a>` : esc(t.source);
    const drop = DROPPABLE.includes(t.step)
      ? `<button type="button" class="btn-ghost" data-k="drop" data-kind="story" data-i="${t.id}" title="Leave this story out of the week">✕</button>` : "";
    return `<div class="bw-k-topic ${esc(t.step)}">
      <span class="num" title="story ${t.id}">${t.id}</span>
      <span class="what"><span>${esc(t.title)} <small>${source}${t.pillar ? ` · ${esc(t.pillar)}` : ""} · ${t.score}</small></span>${preview}${lists(t)}${t.sent ? `<small>on Linux as story ${esc(String(t.sent.hub_id ?? ""))}</small>` : ""}</span>
      <span class="actions"><span class="pill ${cls}">${text}</span>${drop}</span>
    </div>`;
  }

  function rankingRow(g) {
    const [text, cls] = STEP[g.step] || [g.step, ""];
    const p = g.preview;
    const preview = p ? `<details><summary>${esc(p.title)} · ${p.words} words · <i>${esc(p.cover_text)}</i></summary>
        <ol>${p.entries.map((e) => `<li><b>${esc(e.label)}</b> <span class="hint">${esc(e.title)}</span></li>`).join("")}</ol></details>` : "";
    const pool = `<details><summary>${g.pool.length} candidates, ${g.n} make the ranking</summary>
        <div class="hint">${g.pool.map((s) => `[${s.id}] ${esc(s.title)}`).join(" · ")}</div></details>`;
    const drop = DROPPABLE.includes(g.step)
      ? `<button type="button" class="btn-ghost" data-k="drop" data-kind="ranking" data-i="${g.k}" title="Leave this ranking out of the week">✕</button>` : "";
    return `<div class="bw-k-topic ${esc(g.step)}">
      <span class="num">R${g.k}</span>
      <span class="what"><span>Ranking ${g.k} <small>${g.n} of ${g.pool.length} stories, counted down</small></span>${p ? preview : pool}${lists(g)}${g.sent ? `<small>on Linux as story ${esc(String(g.sent.hub_id ?? ""))}</small>` : ""}</span>
      <span class="actions"><span class="pill ${cls}">${text}</span>${drop}</span>
    </div>`;
  }

  function render(w) {
    current = w;
    if (!w) {
      box.innerHTML = '<div class="empty">No week started yet. Pick how many stories and rankings to write.</div>';
      return;
    }
    const prompts = [];
    const fixing = [...w.stories, ...w.groups].some((t) => ["fix", "facts_fix"].includes(t.step));
    if (w.to_write) {
      prompts.push(promptBlock(`1 · Write${fixing ? " / fix" : ""} (${w.to_write} script${w.to_write > 1 ? "s" : ""})`,
        "Copy into any LLM; the answer is one JSON with every script", w.write_prompt, w.write_chars, "answer", "🔎 Check the scripts"));
    }
    if (w.to_check) {
      prompts.push(promptBlock(`2 · Fact check (${w.to_check} stor${w.to_check > 1 ? "ies" : "y"})`,
        "Best in a second LLM, another one than the writer", w.facts_prompt, w.facts_chars, "facts", "🔎 Check the facts"));
    }
    let send = "";
    if (w.ready) {
      const where = sender ? "Linux" : "this machine's bank";
      send = `<div class="bw-w-step"><b>3 · ${w.ready} script${w.ready > 1 ? "s" : ""} passed the checks</b>
        <span class="hint">They go to ${where}: the production run makes its videos from them before asking Gemini, oldest first.</span>
        <button type="button" class="btn-primary" data-k="send">📤 Send to ${esc(where)}</button></div>`;
    }
    const ls = w.last_send;
    const last = ls ? `<div class="hint">Last send ${esc(ls.at.slice(0, 16).replace("T", " "))}: ${ls.queued.length} taken${ls.skipped.length ? `, ${ls.skipped.length} skipped` : ""}
      ${ls.waiting ? ` · waiting on Linux: ${ls.waiting.stories} stories, ${ls.waiting.rankings} rankings` : ""}</div>` : "";
    box.innerHTML = `<div class="bw-w-head"><b>${w.stories.length} stories, ${w.groups.length} rankings</b>
        <span class="pill">${w.to_write} to write</span><span class="pill">${w.to_check} to fact-check</span>
        <span class="pill good">${w.ready} ready</span><span class="pill good">${w.sent} on Linux</span>
        <span class="hint">started ${esc(w.created_at.slice(0, 16).replace("T", " "))}</span></div>
      ${last}
      <div class="bw-k-topics">${w.stories.map(storyRow).join("")}${w.groups.map(rankingRow).join("")}</div>
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

  let defaultsSet = false;

  async function load() {
    try {
      const { body } = await api("/api/darkarchives/week");
      sender = body.sender;
      if (!defaultsSet) {
        $("daKRankings").value = body.default_rankings;
        defaultsSet = true;
      }
      info.textContent = `${body.new} NEW stories in the bank` + (body.waiting && (body.waiting.stories || body.waiting.rankings)
        ? ` · written for later here: ${body.waiting.stories} stories, ${body.waiting.rankings} rankings` : "");
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
          render((await post(`/api/darkarchives/week/${what}`, { text })).body);
        } finally {
          btn.disabled = false;
        }
      });
    }
    if (what === "drop") {
      return act(async () => { render((await post(`/api/darkarchives/week/drop/${btn.dataset.kind}/${btn.dataset.i}`)).body); });
    }
    if (what === "send") {
      if (!confirm(`Send the ${current.ready} ready script(s)? Linux makes their videos before asking Gemini, oldest first.`)) return;
      return act(async () => {
        btn.disabled = true;
        btn.textContent = "Sending…";
        try {
          render((await post("/api/darkarchives/week/send")).body);
        } finally {
          btn.disabled = false;
        }
      });
    }
  });

  $("daKStart").addEventListener("click", () => act(async () => {
    if (current && [...current.stories, ...current.groups].some((t) => !["sent", "dropped", "rejected"].includes(t.step))
        && !confirm("Start a new week? The current one, with its drafts, is replaced.")) return;
    const count = Math.max(0, Math.min(21, parseInt($("daKCount").value, 10) || 0));
    const rankings = Math.max(0, Math.min(7, parseInt($("daKRankings").value, 10) || 0));
    render((await post("/api/darkarchives/week", { count, rankings })).body);
  }));
  document.querySelector('[data-da-view="week"]')?.addEventListener("click", load);
})();
