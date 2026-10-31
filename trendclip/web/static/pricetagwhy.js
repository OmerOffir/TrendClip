// Price Tag Why tab, with Borderline Why's sections: Videos (the YouTube login and the made videos), Plan (each slot's
// video or the script it will be made from, with Approve / Don't post / Upload now / Make now, and every script),
// Topics, Write it myself and Write a week (paste-in LLM flows into the script queue) and Long video (from week 2).
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const els = { error: $("pwError"), hint: $("pwHint"), yt: $("pwYouTube"), plan: $("pwPlan"), scripts: $("pwScripts"),
    videos: $("pwVideos"), count: $("pwVideosCount"), topics: $("pwTopics"), topicsCount: $("pwTopicsCount"),
    navWaiting: $("pwNavWaiting"), navQueued: $("pwNavQueued"), navTopics: $("pwNavTopics"), long: $("pwLong"),
    write: { one: $("pwWriteOne"), week: $("pwWriteWeek") } };
  const state = { poll: null, status: null, section: "videos" };
  const showError = (msg) => { els.error.textContent = msg || ""; els.error.hidden = !msg; };
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const fmtWhen = (iso) => (iso || "").replace("T", " ").slice(0, 16);
  const sourceList = (sources) => (sources || []).map((s) => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a></li>`).join("");
  const claimList = (claims) => (claims || []).map((c) => `<li>${esc(c.claim)} <span class="hint">[${(c.sources || []).map(esc).join(", ")}]</span></li>`).join("");
  const STATES = {
    posted: ["good", "▶️ On YouTube"], approved: ["good", "✅ Approved"], waiting: ["warn", "⏸ Waiting for Approve"],
    vetoed: ["", "🚫 Won't be posted"], blocked: ["warn", "❌ Failed its check"], due: ["", "⏳ Being made"],
    later: ["", "🕒 Made later"], held: ["", "⏸ On hold"], missed: ["", "Passed, nothing went out"], no_script: ["warn", "✍️ No script left"], sample: ["", "Sample"],
  };
  const pill = (st) => { const [cls, label] = STATES[st] || ["", st]; return `<span class="pill ${cls}">${label}</span>`; };
  const videoSrc = (key, t) => `/api/pricetagwhy/videos/${encodeURIComponent(key)}/video.mp4?t=${encodeURIComponent(t || "")}`;
  const setCount = (el, n) => { el.textContent = n; el.hidden = !n; };

  // ---- sections
  function showSection(name) {
    state.section = name;
    document.querySelectorAll("#pwNav [data-pw-view]").forEach((b) => b.classList.toggle("active", b.dataset.pwView === name));
    document.querySelectorAll("[data-pw-panel]").forEach((p) => { p.hidden = document.body.dataset.workspace !== "pricetagwhy" || p.dataset.pwPanel !== name; });
    if (name === "topics") loadTopics();
    else if (name === "write") loadWrite("one");
    else if (name === "week") loadWrite("week");
    else if (name === "long") renderLong();
    else load();
  }

  // ---- videos and plan
  function renderYouTube(st) {
    const yt = st.youtube;
    const who = yt.channel ? `${esc(yt.channel.title)}${yt.handle ? ` (${esc(yt.handle)})` : ""}` : esc(yt.handle || "Price Tag Why");
    els.yt.innerHTML = `<b>YouTube</b>
      ${yt.connected
        ? `<span class="pill good">Connected: ${who}</span><button type="button" class="btn-secondary" id="pwYtLogout">Disconnect</button>`
        : `<span class="pill">Not connected</span><button type="button" class="btn-primary" id="pwYtConnect" ${yt.client_secret ? "" : "disabled"}>Connect YouTube</button>
           <span class="hint">Google opens in a new window: pick the <b>Price Tag Why</b> channel there. Until then nothing can be uploaded</span>`}
      ${!yt.client_secret ? '<span class="da-note">client_secret.json is missing</span>' : ""}
      ${yt.error ? `<span class="da-note">${esc(yt.error)}</span>` : ""}`;
  }

  function slotRow(day, r, connected, busy) {
    const v = r.video;
    const yt = v && v.youtube;
    const what = v
      ? `<b>${esc(v.title)}</b> <span class="hint">${Math.round(v.duration_seconds || 0)} s${yt ? ` · <a href="${esc(yt.url)}" target="_blank" rel="noopener">${esc(yt.url)}</a>` : ""}</span>
         ${v.check && v.check.length ? `<span class="da-note">${esc(v.check.join("; "))}</span>` : ""}`
      : r.script ? `<span class="hint">made at ${esc(r.start.slice(11, 16))}${r.start.slice(0, 10) !== day ? ` (${esc(r.start.slice(5, 10))})` : ""} from</span> <b>${esc(r.script.title)}</b>`
      : r.state === "no_script" ? '<span class="hint">no script left: ✍️ Write it myself or 📅 Write a week</span>'
      : r.state === "held" ? '<span class="hint">nothing is made or uploaded until the YouTube channel is renamed</span>' : "";
    const actions = [];
    if (v) {
      actions.push(`<button type="button" class="btn-secondary" data-watch="${esc(v.key)}" data-t="${esc(v.created_at)}">▶ Watch</button>`);
      if (r.state === "waiting") actions.push(`<button type="button" class="btn-primary" data-approve="${esc(v.key)}">✅ Approve</button>`);
      if (r.state !== "vetoed") actions.push(`<button type="button" class="btn-secondary" data-veto="${esc(v.key)}">🚫 ${yt ? "Make it private" : "Don't post this"}</button>`);
      if (!yt && r.state !== "blocked" && r.state !== "vetoed") actions.push(`<button type="button" class="btn-secondary" data-upload="${esc(v.key)}" ${connected ? "" : "disabled"}>⬆️ Upload now</button>`);
    }
    if (!yt && r.state !== "missed" && r.state !== "held" && (r.script || v) && !busy) {
      actions.push(`<button type="button" class="btn-secondary" data-makeslot="${esc(day)} ${esc(r.slot)}">💲 ${v ? "Make another" : "Make now"}</button>`);
    }
    return `<div class="pw-slot"><span class="pw-time">${esc(r.slot)}</span>${pill(r.state)}<span class="pw-what">${what}</span>${actions.join("")}</div>`;
  }

  function scriptCard(s, busy) {
    const ok = !s.check.length;
    return `<article class="da-vcard pw-script"><div class="da-vcard-body">
      <b>${esc(s.title)}</b>
      <div class="pills"><span class="pill ${ok ? "good" : "warn"}">${ok ? "✅ Rules OK" : "❌ " + esc(s.check.join("; "))}</span>
        <span class="pill">${s.used ? "made for a slot" : "queued"}</span>
        <span class="pill">${esc(s.words)} words</span><span class="pill">${s.sources.length} sources</span><span class="pill">${esc(s.file)}</span></div>
      <details><summary class="hint">Question, claims and sources</summary><div class="da-yt-text">
        <div class="da-yt-row"><span class="da-yt-label">Question</span><span>${esc(s.question)}</span></div>
        <div class="da-yt-row"><span class="da-yt-label">Claims</span><ul class="bw-sources">${claimList(s.claims)}</ul></div>
        <div class="da-yt-row"><span class="da-yt-label">Sources</span><ol class="bw-sources">${sourceList(s.sources)}</ol></div>
      </div></details>
      <div class="da-vcard-actions da-actions">
        <button type="button" class="btn-secondary" data-make="${esc(s.file)}" ${ok && !busy ? "" : "disabled"} title="A sample to watch; the script stays in the queue">💲 Make a sample</button>
      </div>
    </div></article>`;
  }

  function videoCard(v) {
    const src = videoSrc(v.key, v.created_at);
    return `<article class="da-vcard">
      <div class="da-vcard-player">${v.has_video ? `<video controls preload="metadata" src="${src}"></video>
        <a class="hint" href="${src}" download="${esc(v.key)}.mp4">Download</a>` : '<span class="hint">no video file</span>'}</div>
      <div class="da-vcard-body">
        <b>${esc(v.title)}</b>
        <div class="pills">${pill(v.state)}<span class="pill">${Math.round(v.duration_seconds || 0)}s</span><span class="pill">${esc(v.words)} words</span>
          <span class="pill">🎙️ ${esc(v.voice || "")}</span>${v.music ? `<span class="pill">🎵 ${esc(v.music)}</span>` : ""}
          ${v.job ? `<span class="pill">📅 ${esc(fmtWhen(v.job.post_at))}</span>` : ""}</div>
        <span class="hint">made ${esc(fmtWhen(v.created_at))} · ${esc(v.key)}</span>
        ${v.check && v.check.length ? `<span class="da-note">${esc(v.check.join("; "))}</span>` : ""}
        <div class="da-yt-text">
          <div class="da-yt-row"><span class="da-yt-label">Sources</span><ol class="bw-sources">${sourceList(v.sources)}</ol></div>
          <div class="da-yt-row"><span class="da-yt-label">Description</span><pre class="da-desc">${esc(v.description || "")}</pre></div>
        </div>
        ${v.job && v.state !== "sample" ? `<div class="da-vcard-actions da-actions">
          ${v.state === "waiting" ? `<button type="button" class="btn-primary" data-approve="${esc(v.key)}">✅ Approve</button>` : ""}
          ${v.state !== "vetoed" ? `<button type="button" class="btn-secondary" data-veto="${esc(v.key)}">🚫 ${v.state === "posted" ? "Make it private" : "Don't post this"}</button>` : ""}
          ${v.state !== "posted" && v.state !== "blocked" && v.state !== "vetoed" ? `<button type="button" class="btn-secondary" data-upload="${esc(v.key)}" ${state.status && state.status.youtube.connected ? "" : "disabled"}>⬆️ Upload now</button>` : ""}
        </div>` : ""}
      </div>
    </article>`;
  }

  async function load() {
    try {
      const [{ body: st }, { body: p }, { body: sc }, { body: vids }] = await Promise.all([api("/api/pricetagwhy/status"),
        api("/api/pricetagwhy/plan"), api("/api/pricetagwhy/scripts"), api("/api/pricetagwhy/videos")]);
      state.status = st;
      const busy = st.making.length > 0 || st.upload.running;
      els.hint.innerHTML = (st.hold_until ? `<b>⏸ On hold until ${esc(st.hold_until)}</b> (the YouTube channel's new name): no slot videos, no uploads; samples still work · ` : "")
        + `${esc(st.handle)} · one video a day at <b>${esc(st.slots.join(", "))}</b> (${esc(st.timezone)}), made ${st.lead_hours} h before`
        + ` · ${st.require_approval ? "goes out only after <b>Approve</b>" : "goes out unless vetoed"} as ${esc(st.privacy)}`
        + ` · <b>${st.queued}</b> script(s) queued · voice Kokoro ${esc(st.voice)}`
        + (st.auto_make ? "" : " · <b>automatic making is off</b> (PW_AUTO_MAKE)")
        + (st.discord ? " · posted in Discord" : " · <b>no Discord channel</b> (channels/pricetagwhy/channel.env)")
        + (st.making.length ? ` · <b>making ${esc(st.making.join(", "))}…</b>` : "")
        + (st.upload.running ? ` · <b>uploading: ${esc(st.upload.message || "")}</b>` : st.upload.error ? ` · ❌ upload: ${esc(st.upload.error)}` : "");
      renderYouTube(st);
      els.plan.innerHTML = p.days.map((d) => `<div class="pw-day"><h3>${esc(d.weekday)} ${esc(d.day)}${d.today ? " · today" : ""}</h3>
        ${d.slots.map((r) => slotRow(d.day, r, st.youtube.connected, busy)).join("")}<div class="pw-watch"></div></div>`).join("");
      els.scripts.innerHTML = sc.scripts.length ? sc.scripts.map((s) => scriptCard(s, busy)).join("") : '<div class="empty">No scripts yet</div>';
      els.count.textContent = `· ${vids.videos.length}`;
      els.videos.innerHTML = vids.videos.length ? vids.videos.map(videoCard).join("") : '<div class="empty">No videos yet</div>';
      setCount(els.navWaiting, vids.videos.filter((v) => v.state === "waiting").length);
      setCount(els.navQueued, st.queued);
      showError("");
      clearTimeout(state.poll);
      if (busy) state.poll = setTimeout(load, 5000);
    } catch (err) {
      showError(err.message);
    }
  }

  // ---- topics
  async function loadTopics() {
    try {
      const { body } = await api("/api/pricetagwhy/topics");
      renderTopics(body);
    } catch (err) {
      els.topics.innerHTML = `<div class="da-note">${esc(err.message)}</div>`;
    }
  }

  function renderTopics(body) {
    const sel = $("pwAddPillar");
    if (sel.options.length <= 1) sel.insertAdjacentHTML("beforeend", body.pillars.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join(""));
    els.topicsCount.textContent = `· ${body.left} left`;
    setCount(els.navTopics, body.left);
    els.topics.innerHTML = body.topics.length ? body.topics.map((t) => `<div class="pw-slot">
        ${t.used ? '<span class="pill good">written</span>' : '<span class="pill">next</span>'}
        <span class="pw-what">${esc(t.question)}</span><span class="pill">${esc(t.pillar || "—")}</span>
        ${t.used ? "" : `<button type="button" class="btn-secondary" data-topic-remove="${esc(t.question)}">Remove</button>`}
      </div>`).join("") : '<div class="empty">No topics</div>';
  }

  // ---- writing with any LLM
  const STEP_TEXT = {
    write: "1. Copy the prompt into any LLM (one that can browse is best), then paste its JSON answer below.",
    fix: "The answer broke some rules: copy this fix prompt into the same chat and paste the new answer below.",
    facts: "2. The rules pass. Copy this fact-check prompt into a second LLM that can open links, and paste its JSON answer below.",
  };

  async function loadWrite(kind) {
    try {
      renderWrite(kind, (await api(`/api/pricetagwhy/write/${kind}`)).body);
    } catch (err) {
      els.write[kind].innerHTML = `<div class="da-note">${esc(err.message)}</div>`;
    }
  }

  function renderWrite(kind, v) {
    const box = els.write[kind];
    const topicList = (v.topics || []).map((t, i) => `<li>${esc(t.question)}${t.pillar ? ` <span class="hint">(${esc(t.pillar)})</span>` : ""}</li>`).join("");
    if (v.step === "none") {
      box.innerHTML = kind === "one"
        ? `<div class="pw-add"><input type="text" id="pwOneQuestion" placeholder="A question, or leave empty for the next topic: ${esc((v.topics[0] || {}).question || "none left")}" maxlength="160">
           <button type="button" class="btn-primary" data-write-start="one">Start</button></div>`
        : `<div class="pw-add"><label class="hint">Scripts <input type="number" id="pwWeekCount" min="1" max="14" value="7" style="width:70px"></label>
           <button type="button" class="btn-primary" data-write-start="week">Start</button></div>
           <span class="hint">The next topics, pillars in turn:</span><ol>${topicList || "<li>none left: add some in Topics</li>"}</ol>`;
      return;
    }
    const drafts = (v.drafts || []).map((d, i) => `<div class="pw-slot">
        <span class="pw-time">${i + 1}</span>${d.problems.length ? '<span class="pill warn">❌ rules</span>' : '<span class="pill good">✅ rules</span>'}
        <span class="pw-what"><b>${esc(d.title)}</b> <span class="hint">${d.words} words · ${d.sources} sources</span>
          ${d.problems.length ? `<span class="da-note">${esc(d.problems.join("; "))}</span>` : ""}</span>
        ${kind === "week" ? `<button type="button" class="btn-secondary" data-write-drop="${i + 1}">Leave out</button>` : ""}
      </div>`).join("");
    const pasteTo = v.step === "facts" ? "facts" : "answer";
    box.innerHTML = `<ol class="hint">${topicList}</ol>
      ${(v.general || []).length ? `<span class="da-note">${esc(v.general.join("; "))}</span>` : ""}
      ${drafts}
      ${v.step === "ready"
        ? `<p>✅ Both checks passed.</p><button type="button" class="btn-primary" data-write-queue="${kind}">➕ Add to the queue</button>`
        : `<p class="hint">${esc(STEP_TEXT[v.step] || "")}</p>
           <div class="pw-add"><button type="button" class="btn-secondary" data-copy="${kind}">📋 Copy the prompt</button>
             <span class="hint">${(v.prompt || "").length.toLocaleString()} characters</span></div>
           <textarea class="pw-prompt" readonly rows="5" id="pwPrompt-${kind}">${esc(v.prompt || "")}</textarea>
           <textarea class="pw-paste" rows="8" id="pwPaste-${kind}" placeholder="Paste the LLM's JSON answer here"></textarea>
           <button type="button" class="btn-primary" data-write-paste="${kind}" data-to="${pasteTo}">Check it</button>`}
      <button type="button" class="btn-secondary" data-write-start="${kind}" data-restart="1">Start over</button>`;
  }

  // ---- long video
  function renderLong() {
    els.long.innerHTML = `<p>One long video a week starts in <b>week 2</b>, deep-diving the topic of the best Shorts: a hook, the question,
      3-5 chapters, a surprising ending and a tease for the next video (8-12 minutes, landscape).</p>
      <p>The first one is planned: <b>Why Can't Countries Just Print Money?</b> (Weimar Germany, Zimbabwe, Venezuela).</p>
      <p class="hint">The long-video maker isn't built yet: it will reuse Borderline Why's chapters (research prompt → outline → one chapter at a time
      with its fact check → landscape render), in this channel's colours.</p>`;
  }

  // ---- actions
  async function act(fn, after = load) {
    try { await fn(); } catch (err) { showError(err.message); }
    after();
  }

  async function connectYouTube() {
    let popup = window.open("", "trendclip-youtube", "width=520,height=720");
    try {
      const { body } = await post("/api/pricetagwhy/youtube/connect");
      if (popup) popup.location.href = body.auth_url;
      else popup = window.open(body.auth_url, "_blank");
    } catch (err) {
      if (popup) popup.close();
      return showError(`Could not start the Google login: ${err.message}`);
    }
    const timer = setInterval(() => { if (!popup || popup.closed) { clearInterval(timer); load(); } }, 1000);
  }

  async function writeAct(kind, fn) {
    try {
      renderWrite(kind, (await fn()).body);
    } catch (err) {
      const box = els.write[kind];
      box.insertAdjacentHTML("afterbegin", `<div class="da-note">${esc(err.message)}</div>`);
    }
  }

  document.addEventListener("click", async (e) => {
    if (document.body.dataset.workspace !== "pricetagwhy") return;
    const nav = e.target.closest("#pwNav [data-pw-view]");
    if (nav) return showSection(nav.dataset.pwView);
    const t = e.target.closest("button");
    if (!t) return;
    const d = t.dataset;
    if (t.id === "pwRefresh") return load();
    if (t.id === "pwYtConnect") return connectYouTube();
    if (t.id === "pwYtLogout") return act(() => post("/api/pricetagwhy/youtube/logout"));
    if (d.make) { t.disabled = true; return act(() => post("/api/pricetagwhy/make", { file: d.make })); }
    if (d.makeslot) {
      const [day, slot] = d.makeslot.split(" ");
      t.disabled = true;
      return act(() => post("/api/pricetagwhy/make-slot", { day, slot }));
    }
    if (d.approve) return act(() => post(`/api/pricetagwhy/videos/${encodeURIComponent(d.approve)}/approve`));
    if (d.veto) return act(() => post(`/api/pricetagwhy/videos/${encodeURIComponent(d.veto)}/veto`));
    if (d.upload) {
      if (!window.confirm(`Upload this video to Price Tag Why now, as ${state.status ? state.status.privacy : "public"}?`)) return;
      return act(() => post(`/api/pricetagwhy/videos/${encodeURIComponent(d.upload)}/upload`, {}));
    }
    if (d.watch) {
      const box = t.closest(".pw-day").querySelector(".pw-watch");
      const same = box.dataset.key === d.watch;
      box.innerHTML = same ? "" : `<video controls autoplay style="max-width:260px;aspect-ratio:9/16;border-radius:10px" src="${videoSrc(d.watch, d.t)}"></video>`;
      box.dataset.key = same ? "" : d.watch;
      return;
    }
    if (d.topicRemove) return act(() => post("/api/pricetagwhy/topics/remove", { question: d.topicRemove }), loadTopics);
    if (d.writeStart) {
      const kind = d.writeStart;
      if (d.restart && !window.confirm("Start over? The pasted answers of this session are dropped.")) return;
      const body = kind === "one" ? { question: ($("pwOneQuestion") || {}).value || "" } : { count: Number(($("pwWeekCount") || {}).value || 7) };
      if (d.restart) Object.assign(body, kind === "one" ? { question: "" } : { count: 7 });
      return writeAct(kind, () => post(`/api/pricetagwhy/write/${kind}`, body));
    }
    if (d.copy) {
      const area = $(`pwPrompt-${d.copy}`);
      try { await navigator.clipboard.writeText(area.value); t.textContent = "✅ Copied"; } catch { area.select(); document.execCommand("copy"); t.textContent = "✅ Copied"; }
      return;
    }
    if (d.writePaste) {
      const kind = d.writePaste;
      const text = $(`pwPaste-${kind}`).value;
      if (!text.trim()) return;
      return writeAct(kind, () => post(`/api/pricetagwhy/write/${kind}/${d.to}`, { text }));
    }
    if (d.writeDrop) return writeAct("week", () => post(`/api/pricetagwhy/write/week/drop/${d.writeDrop}`));
    if (d.writeQueue) {
      const kind = d.writeQueue;
      try {
        const { body } = await post(`/api/pricetagwhy/write/${kind}/queue`);
        els.write[kind].innerHTML = `<p>✅ Added to the queue: ${body.queued.map(esc).join(", ")}. ${body.waiting} script(s) wait for their slots (Plan).</p>
          <button type="button" class="btn-primary" data-write-start="${kind}">Write ${kind === "one" ? "another" : "the next week"}</button>`;
      } catch (err) {
        els.write[kind].insertAdjacentHTML("afterbegin", `<div class="da-note">${esc(err.message)}</div>`);
      }
    }
  });
  $("pwAddTopic").addEventListener("submit", (e) => {
    e.preventDefault();
    act(async () => {
      renderTopics((await post("/api/pricetagwhy/topics", { question: $("pwAddQuestion").value, pillar: $("pwAddPillar").value })).body);
      $("pwAddQuestion").value = "";
    }, () => {});
  });
  window.addEventListener("message", (e) => { if (e.data === "trendclip:youtube" && document.body.dataset.workspace === "pricetagwhy") load(); });
  const onShow = () => { if (document.body.dataset.workspace === "pricetagwhy") showSection(state.section); };
  new MutationObserver(onShow).observe(document.body, { attributes: true, attributeFilter: ["data-workspace"] });
  onShow();
})();
