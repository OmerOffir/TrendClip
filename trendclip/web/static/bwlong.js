// Borderline Why "Long video": 8-15 minute landscape videos in chapters, written with any LLM by hand. The code reads
// the sources and checks every answer; you carry the prompts (modules/bw_long.py).
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const box = $("bwLSession");
  if (!box) return;
  const error = $("bwLError");
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const base = (id) => `/api/borderlinewhy/long/${encodeURIComponent(id)}`;
  const STORE = "bwLongSession";
  const STEPS = [["research", "1 · Research"], ["outline", "2 · Outline"], ["chapter", "3 · Chapters"], ["ready", "4 · Make"], ["made", "5 · Upload"]];
  const NOW = { research: "research", outline: "outline", rejected: "outline", chapter: "chapter", ready: "ready", made: "made" };
  const CH_STEP = { write: ["to write", ""], fix: ["fix the rules", "warn"], facts: ["fact check", "warn"], facts_fix: ["fix the facts", "warn"], done: ["✅ done", "good"] };
  let current = null;
  let busy = false;
  let timer = null;

  const showError = (msg) => { error.textContent = msg || ""; error.hidden = !msg; };
  const remember = (id) => { try { id ? localStorage.setItem(STORE, id) : localStorage.removeItem(STORE); } catch { /* private window */ } };
  const recalled = () => { try { return localStorage.getItem(STORE); } catch { return null; } };
  const fresh = () => `?t=${Date.now()}`;

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

  const promptBlock = (s, where) => `<div class="bw-w-step"><b>${where}</b>
      <span class="hint">${s.prompt.length.toLocaleString()} characters · each prompt is whole, so a new chat works too</span>
      <textarea class="bw-w-prompt" readonly rows="6">${esc(s.prompt)}</textarea>
      <button type="button" class="btn-secondary" data-l="copy">📋 Copy the prompt</button></div>`;

  const pasteBlock = (label, act, button, extra = "") => `<div class="bw-w-step"><b>${label}</b>
      <textarea class="bw-w-answer" rows="6" placeholder="Paste the whole answer here"></textarea>
      <div class="bw-w-make"><button type="button" class="btn-primary" data-l="${act}">${button}</button>${extra}</div></div>`;

  const note = (title, items) => `<div class="da-note"><b>${title}</b><ul>${items.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>`;

  function chapterRows(s) {
    if (!s.chapters.length) return "";
    return `<div class="bw-l-chapters">${s.chapters.map((c) => {
      const [label, tone] = CH_STEP[c.step] || [c.step, ""];
      const stats = c.words ? `<small class="hint">${c.words} words · ${c.scenes} scenes · ${c.claims} facts</small>` : "";
      const tools = c.words ? `<button type="button" class="btn-ghost" data-l="preview" data-n="${c.n}">🖼 Storyboard</button>` : "";
      const again = c.step === "done" && s.step !== "made" ? `<button type="button" class="btn-ghost" data-l="reopen" data-n="${c.n}">↺ Write again</button>` : "";
      const img = c.preview ? `<img class="bw-l-board" alt="Storyboard of chapter ${c.n}" src="${base(s.id)}/file/preview_${c.n}.jpg${fresh()}" hidden>` : "";
      return `<div class="bw-l-row" data-n="${c.n}"><b>${c.n}. ${esc(c.title)}</b> <span class="pill ${tone}">${label}</span> ${stats}
        <span class="spacer"></span>${tools}${again}${img}</div>`;
    }).join("")}</div>`;
  }

  function render(s) {
    current = s;
    remember(s.id);
    clearTimeout(timer);
    const at = NOW[s.step] || "research";
    const pills = STEPS.map(([key, label]) => `<span class="pill${key === at ? " good" : ""}">${label}</span>`).join(" ");
    const sources = s.sources.map((src) => `<a href="${esc(src.url)}" target="_blank" rel="noopener">[${src.id}] ${esc(src.title)}</a>`).join(" · ");
    let body = "";
    if (s.step === "research") {
      body += promptBlock(s, "Copy this research prompt into Gemini Deep Research (or any LLM that can browse)");
      body += pasteBlock("Paste its answer: the facts with their links", "research", "🔗 Read the links",
        '<button type="button" class="btn-ghost" data-l="skip">Skip: Wikipedia is enough</button>');
    }
    if (s.step === "outline" || s.step === "rejected") {
      if (s.research) {
        const wiki = s.research.wikipedia ? ` and ${s.research.wikipedia} more Wikipedia article${s.research.wikipedia === 1 ? "" : "s"}` : "";
        const few = s.research.read < 3 ? " Few outside pages: Deep Research (or a search-enabled LLM) usually finds more." : "";
        body += `<div class="hint">🔗 ${s.research.read} of the ${s.research.links} outside links could be read${wiki}; all became sources.${few}</div>`;
      }
      if (s.step === "rejected") body += note("The LLM said the sources can't carry a long video:", s.problems);
      else if (s.problems.length) body += note("The outline missed:", s.problems);
      body += promptBlock(s, "Copy this outline prompt into your LLM");
      body += pasteBlock("Paste its answer (the JSON)", "outline", "🔎 Check the outline");
    }
    if (s.outline) body += `<div class="bw-w-step"><b>${esc(s.outline.title)}</b><span class="hint">thumbnail: “${esc(s.outline.cover_text)}” · ${s.words} words so far ≈ ${s.minutes} min</span>${chapterRows(s)}</div>`;
    if (s.step === "chapter" && s.current != null) {
      const c = s.chapters[s.current];
      if (c.step === "fix") body += note(`Chapter ${c.n} missed the rules (the prompt below asks for a fix):`, c.problems);
      if (c.step === "facts_fix") body += note(`The fact checker flagged in chapter ${c.n}:`, c.flagged);
      if (s.purpose === "facts") {
        body += `<div class="hint">✅ Chapter ${c.n} passed the rules and every quote is in its source.</div>`;
        body += promptBlock(s, `Copy chapter ${c.n}'s fact-check prompt into a second LLM (another one than the writer is best)`);
        body += pasteBlock("Paste the fact checker's answer", "facts", "🔎 Check the facts");
      } else {
        body += promptBlock(s, `Copy chapter ${c.n}'s prompt into your LLM (${esc(c.title)})`);
        body += pasteBlock(`Paste chapter ${c.n} (the JSON)`, "chapter", "🔎 Check the chapter");
      }
    }
    if (s.step === "ready") {
      body += `<div class="bw-w-step"><b>✅ Every chapter passed both checks · ${s.words} words ≈ ${s.minutes} minutes</b>
        <span class="hint">The video is made on this machine in landscape: voice, maps, chapter banners, the thumbnail and the chapter times
          for the description. It takes about an hour; you can close the page.</span>
        <div class="bw-w-make"><button type="button" class="btn-primary" data-l="make">🎬 Make the video</button></div></div>`;
    }
    const job = s.job || {};
    if (job.running || job.error) {
      body += `<div class="bw-w-step"><b>${job.running ? "🎬 Making the video…" : "❌ The make failed"}</b>
        <span class="hint">${esc(job.error || job.message || "")}${job.running && job.fraction != null ? ` (${Math.round(job.fraction * 100)}%)` : ""}</span>
        ${job.error ? '<div class="bw-w-make"><button type="button" class="btn-secondary" data-l="make">Try again</button></div>' : ""}</div>`;
    }
    if (s.video && s.info) {
      const up = s.info.upload;
      const chapters = (s.info.chapters || []).map(([t, title]) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")} ${title}`).join(" · ");
      body += `<div class="bw-w-step"><b>🎬 ${esc(s.info.title)}</b>
        <span class="hint">${Math.round(s.info.duration_seconds / 60 * 10) / 10} minutes · ${esc(chapters)}</span>
        <video class="bw-l-video" controls preload="metadata" src="${base(s.id)}/file/video.mp4${fresh()}"></video>
        ${s.thumbnail ? `<img class="bw-l-thumb" alt="Thumbnail" src="${base(s.id)}/file/thumbnail.jpg${fresh()}">` : ""}
        <details><summary>Description</summary><pre class="bw-l-desc">${esc(s.info.description)}</pre></details>
        ${up ? `<span class="pill good">✅ On YouTube (${esc(up.privacy || "")})</span> <a href="${esc(up.url)}" target="_blank" rel="noopener">${esc(up.url)}</a>
               ${up.thumbnail_error ? `<span class="hint">Thumbnail not set: ${esc(up.thumbnail_error)}. Set it in YouTube Studio.</span>` : ""}`
          : `<div class="bw-w-make"><select data-l="privacy" aria-label="Privacy"><option value="private">Private (check it on YouTube first)</option>
              <option value="unlisted">Unlisted</option><option value="public">Public</option></select>
              <button type="button" class="btn-primary" data-l="upload">📤 Upload to YouTube</button><span class="hint" data-l="upstatus"></span></div>`}</div>`;
    }
    box.innerHTML = `<div class="bw-w-head"><b>${esc(s.question)}</b><span class="hint">Wikipedia: ${esc(s.article)}</span>${pills}</div>
      <details class="hint bw-w-sources"><summary>${s.sources.length} sources</summary>${sources}</details>${body}`;
    if (job.running) timer = setTimeout(() => open(s.id), 8000);
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

  async function watchUpload(status) {
    const { body: job } = await api("/api/borderlinewhy/long-upload-job");
    status.textContent = job.running ? `${job.message}${job.fraction != null ? ` (${Math.round(job.fraction * 100)}%)` : ""}…` : job.error ? `❌ ${job.error}` : "Done";
    if (job.running) return setTimeout(() => watchUpload(status), 3000);
    if (!job.error) open(current.id);
  }

  // A button that waits on a request: disabled with `label` meanwhile, then the answer is shown.
  function call(btn, label, path, body) {
    return act(async () => {
      const was = btn.textContent;
      btn.disabled = true;
      btn.textContent = label;
      try {
        render((await post(path, body)).body);
      } finally {
        btn.disabled = false;
        btn.textContent = was;
      }
    });
  }

  box.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-l]");
    if (!btn || !current) return;
    const what = btn.dataset.l;
    const id = current.id;
    const text = () => btn.closest(".bw-w-step")?.querySelector(".bw-w-answer")?.value || "";
    if (what === "copy") return copy(btn.parentElement.querySelector("textarea"), btn);
    if (what === "research" && text().trim()) return call(btn, "Reading the links…", `${base(id)}/research`, { text: text() });
    if (what === "skip") return call(btn, "…", `${base(id)}/skip-research`);
    if (what === "outline" && text().trim()) return call(btn, "Checking…", `${base(id)}/outline`, { text: text() });
    const n = current.current != null ? current.current + 1 : null;
    if (what === "chapter" && text().trim()) return call(btn, "Checking…", `${base(id)}/chapter/${n}`, { text: text() });
    if (what === "facts" && text().trim()) return call(btn, "Checking…", `${base(id)}/facts/${n}`, { text: text() });
    if (what === "reopen") {
      if (!confirm(`Write chapter ${btn.dataset.n} again? Its draft is replaced by the next one you paste.`)) return;
      return call(btn, "…", `${base(id)}/reopen/${btn.dataset.n}`);
    }
    if (what === "preview") {
      const row = btn.closest(".bw-l-row");
      const img = row.querySelector("img");
      if (img && !img.hidden) { img.hidden = true; return; }
      if (img) { img.hidden = false; return; }
      return call(btn, "Drawing (½ minute)…", `${base(id)}/preview/${btn.dataset.n}`).then(() => {
        const shown = box.querySelector(`.bw-l-row[data-n="${btn.dataset.n}"] img`);
        if (shown) shown.hidden = false;
      });
    }
    if (what === "make") {
      if (!confirm("Make the long video now? It takes about an hour on this machine.")) return;
      return call(btn, "Starting…", `${base(id)}/make`);
    }
    if (what === "upload") {
      const privacy = box.querySelector('select[data-l="privacy"]').value;
      if (!confirm(`Upload "${current.info.title}" to YouTube as ${privacy}?`)) return;
      return act(async () => {
        btn.disabled = true;
        await post(`${base(id)}/upload`, { privacy });
        watchUpload(box.querySelector('[data-l="upstatus"]'));
      });
    }
  });

  async function open(id) {
    try {
      render((await api(`${base(id)}${fresh()}`)).body);
    } catch {
      remember(null);
      box.innerHTML = "";
    }
  }

  async function load() {
    try {
      const { body } = await api("/api/borderlinewhy/long");
      $("bwLTopics").innerHTML = body.topics.map((t) => `<option value="${esc(t.article)}">${esc(t.question)}</option>`).join("");
      const sessions = $("bwLSessions");
      sessions.innerHTML = body.sessions.map((s) => `<option value="${esc(s.id)}">${esc(s.question)} · ${esc(s.step)}</option>`).join("");
      sessions.hidden = $("bwLOpen").hidden = !body.sessions.length;
    } catch (err) {
      showError(err.message);
    }
    const last = recalled();
    if (last && (!current || current.id !== last)) open(last);
  }

  $("bwLStart").addEventListener("click", (e) => act(async () => {
    const article = $("bwLArticle").value.trim();
    if (!article) return showError("Name the Wikipedia article the video is about");
    const topic = [...$("bwLTopics").options].find((o) => o.value === article);
    const question = $("bwLQuestion").value.trim() || (topic ? topic.textContent : "");
    const more = $("bwLMore").value.split(",").map((m) => m.trim()).filter(Boolean);
    e.target.disabled = true;
    box.innerHTML = '<div class="empty">Reading Wikipedia and the pages it cites (a minute or two)…</div>';
    try {
      render((await post("/api/borderlinewhy/long", { question, article, more })).body);
      load();
    } catch (err) {
      box.innerHTML = "";
      throw err;
    } finally {
      e.target.disabled = false;
    }
  }));
  $("bwLOpen").addEventListener("click", () => open($("bwLSessions").value));
  document.querySelector('[data-bw-view="long"]')?.addEventListener("click", load);
})();
