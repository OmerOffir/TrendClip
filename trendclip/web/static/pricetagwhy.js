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
  const state = { poll: null, status: null, section: "videos", format: { one: "why", week: "why" } };  // format: the Write tabs' pick (why | story)
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
  let topics = [];  // the topics still to write, in order (the Plan's empty slots show them)
  // the order the writing prompts take them (pw_write.next_topics): the top one, then the pillars in turn
  const inTurn = (left) => {
    const rest = left.slice(1);
    const out = left.slice(0, 1);
    while (rest.length) {
      const last = out[out.length - 1].pillar;
      const i = Math.max(0, rest.findIndex((t) => t.pillar !== last));
      out.push(rest.splice(i, 1)[0]);
    }
    return out;
  };

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

  // What can be put on a slot: the made samples (rule check passed) and every script still in the queue
  function placeOptions(vids, scripts) {
    const samples = vids.filter((v) => v.state === "sample" && v.has_video && !(v.check && v.check.length));
    const free = scripts.filter((s) => !s.used && (!s.check.length || s.rules_skipped));
    return (samples.length ? `<optgroup label="Made videos">${samples.map((v) => `<option value="video:${esc(v.key)}">${esc(v.title)} (${Math.round(v.duration_seconds || 0)} s, ${esc(fmtWhen(v.created_at))})</option>`).join("")}</optgroup>` : "")
      + (free.length ? `<optgroup label="Scripts (made 6 h before)">${free.map((s) => `<option value="script:${esc(s.file)}">${esc(s.title)}${s.removed ? " (removed)" : ""}</option>`).join("")}</optgroup>` : "");
  }

  function slotRow(day, r, connected, busy, options, topic) {
    const v = r.video;
    const yt = v && v.youtube;
    const what = v
      ? `<b>${esc(v.title)}</b> <span class="hint">${Math.round(v.duration_seconds || 0)} s${yt ? ` · <a href="${esc(yt.url)}" target="_blank" rel="noopener">${esc(yt.url)}</a>` : ""}</span>
         ${v.check && v.check.length ? `<span class="da-note">${esc(v.check.join("; "))}</span>` : ""}`
      : r.script ? `<span class="hint">made at ${esc(r.start.slice(11, 16))}${r.start.slice(0, 10) !== day ? ` (${esc(r.start.slice(5, 10))})` : ""} from</span> <b>${esc(r.script.title)}</b>${r.script.pinned ? ' <span class="pill">📌 chosen</span>' : ""}`
      : r.state === "no_script" && topic ? `<span class="hint">no script yet · next topic:</span> <b>${esc(topic.question)}</b>`
      : r.state === "no_script" ? '<span class="hint">empty: put a video or a script here, or ✍️ Write it myself / 📅 Write a week</span>'
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
    const open = !yt && r.state !== "missed" && r.state !== "held";
    if (open && v) actions.push(`<button type="button" class="btn-secondary" data-slot-remove="${esc(day)} ${esc(r.slot)}" title="The video is set aside and its script leaves the plan">✕ Remove</button>`);
    else if (open && r.script) actions.push(`<button type="button" class="btn-secondary" data-script-remove="${esc(r.script.file)}" title="The script leaves the plan (it stays in the Scripts list)">✕ Remove</button>`);
    if (r.state === "no_script" && topic) {
      actions.push(`<button type="button" class="btn-primary" data-topic-write="${esc(topic.question)}" title="Opens Write it myself with this question; the script goes to the first free slot">✍️ Write it now</button>`);
      if (topic === topics[0] && topics.length > 1) actions.push(`<select class="pw-place" data-topic-pick aria-label="Another topic"><option value="">🔁 Another topic…</option>${topics.slice(1).map((t) => `<option value="${esc(t.question)}">${esc(t.question)}</option>`).join("")}</select>`);
    }
    if (open && !v && options) {
      actions.push(`<select class="pw-place" data-place="${esc(day)} ${esc(r.slot)}"><option value="">📅 Put here…</option>${options}</select>`);
    }
    return `<div class="pw-slot"><span class="pw-time">${esc(r.slot)}</span>${pill(r.state)}<span class="pw-what">${what}</span>${actions.join("")}</div>`;
  }

  function scriptCard(s, busy) {
    const ok = !s.check.length || s.rules_skipped;
    return `<article class="da-vcard pw-script"><div class="da-vcard-body">
      <b>${esc(s.title)}</b>
      <div class="pills"><span class="pill ${ok ? "good" : "warn"}">${!s.check.length ? "✅ Rules OK" : s.rules_skipped ? "⏭ Rules skipped: " + esc(s.check.join("; ")) : "❌ " + esc(s.check.join("; "))}</span>
        <span class="pill">${s.used ? "made for a slot" : s.removed ? "removed from the plan" : s.pinned ? `📌 ${esc(s.pinned.replace("_", " "))}` : "queued"}</span>
        <span class="pill">${esc(s.words)} words</span><span class="pill">${s.sources.length} sources</span><span class="pill">${esc(s.file)}</span></div>
      <details><summary class="hint">Question, claims and sources</summary><div class="da-yt-text">
        <div class="da-yt-row"><span class="da-yt-label">Question</span><span>${esc(s.question)}</span></div>
        <div class="da-yt-row"><span class="da-yt-label">Claims</span><ul class="bw-sources">${claimList(s.claims)}</ul></div>
        <div class="da-yt-row"><span class="da-yt-label">Sources</span><ol class="bw-sources">${sourceList(s.sources)}</ol></div>
      </div></details>
      <div class="da-vcard-actions da-actions">
        <button type="button" class="btn-secondary" data-make="${esc(s.file)}" ${ok && !busy ? "" : "disabled"} title="A sample to watch; the script stays in the queue">💲 Make a sample</button>
        ${s.used ? "" : s.removed ? `<button type="button" class="btn-secondary" data-script-restore="${esc(s.file)}">↩ Back in the plan</button>`
          : `<button type="button" class="btn-secondary" data-script-remove="${esc(s.file)}">✕ Remove from the plan</button>`}
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
        ${v.thumbnail ? `<div class="pw-thumb"><a href="/api/pricetagwhy/videos/${encodeURIComponent(v.key)}/thumb.jpg?t=${encodeURIComponent(v.created_at || "")}" target="_blank" rel="noopener" title="Open the thumbnail full size"><img src="/api/pricetagwhy/videos/${encodeURIComponent(v.key)}/thumb.jpg?t=${encodeURIComponent(v.created_at || "")}" alt="Thumbnail" loading="lazy"></a>
          <span class="hint">Thumbnail${v.uploads && v.uploads.youtube && v.uploads.youtube.thumbnail ? ` · on YouTube: ${esc(v.uploads.youtube.thumbnail === "set" ? "set" : "not set (custom Shorts thumbnails need the YouTube Partner Program)")}` : ""}</span></div>` : ""}
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
      const [{ body: st }, { body: p }, { body: sc }, { body: vids }, tp] = await Promise.all([api("/api/pricetagwhy/status"),
        api("/api/pricetagwhy/plan"), api("/api/pricetagwhy/scripts"), api("/api/pricetagwhy/videos"),
        api("/api/pricetagwhy/topics").then((r) => r.body).catch(() => null)]);
      topics = tp ? inTurn(tp.topics.filter((t) => !t.used)) : [];
      let n = 0;  // each empty slot shows the topic its script would come from, in the list's order
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
      const options = placeOptions(vids.videos, sc.scripts);
      els.plan.innerHTML = p.days.map((d) => `<div class="pw-day"><h3>${esc(d.weekday)} ${esc(d.day)}${d.today ? " · today" : ""}</h3>
        ${d.slots.map((r) => slotRow(d.day, r, st.youtube.connected, busy, options, r.state === "no_script" ? topics[n++] : null)).join("")}<div class="pw-watch"></div></div>`).join("");
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
    const linux = body.source === "linux";  // the sending machine shows the Linux list, the one scripts are written from
    els.topicsCount.textContent = `· ${body.left} left${linux ? " · the Linux machine's list" : ""}${body.hub_error ? ` · ⚠️ Linux didn't answer, this machine's list: ${body.hub_error}` : ""}`;
    setCount(els.navTopics, body.left);
    // the ones still to write first, in the order the writing prompts take them; the written ones after
    const left = inTurn(body.topics.filter((t) => !t.used));
    const rows = [...left, ...body.topics.filter((t) => t.used)];
    els.topics.innerHTML = rows.length ? rows.map((t, i) => `<div class="pw-slot pw-topic${i === 0 && !t.used ? " next" : ""}">
        <span class="pw-n">${t.used ? "" : i + 1}</span>
        ${t.used ? '<span class="pill good">✅ written</span>' : i === 0 ? '<span class="pill warn">▶ next</span>' : ""}
        <span class="pw-what">${esc(t.question)}</span>${t.format === "story" ? '<span class="pill good" title="A money story: 60-115 s">📖 story</span>' : ""}<span class="pill">${esc(t.pillar || "—")}</span>
        ${t.used ? "" : `${i && !linux ? `<button type="button" class="btn-secondary" data-topic-first="${esc(t.question)}" title="The next script written (Write it myself or Write a week) takes this one">⬆ Write next</button>` : ""}
          <button type="button" class="btn-primary" data-topic-write="${esc(t.question)}" title="Opens Write it myself with this question; the script goes to the first free slot">✍️ Write it now</button>
          ${linux ? "" : `<button type="button" class="btn-secondary" data-topic-remove="${esc(t.question)}">Remove</button>`}`}
      </div>`).join("") : '<div class="empty">No topics</div>';
  }

  // ---- writing with any LLM
  const STEP_TEXT = {
    write: "1. Copy the prompt into any LLM (one that can browse is best), then paste its JSON answer below.",
    fix: "The answer broke some rules: copy this fix prompt into the same chat and paste the new answer below.",
    flagged: "The fact checker flagged the lines in red above. Either copy this fix prompt into the writing chat (it cuts or corrects only those) and paste the new answer below, or, if you checked them and they're fine, accept the script as it is.",
    facts: "2. The rules pass ✅. Now the fact check: copy this prompt into a second LLM that can open links (a new chat with web search), paste its JSON answer below and press Check it. Or skip it.",
  };

  async function loadWrite(kind) {
    try {
      const { body } = await api(`/api/pricetagwhy/write/${kind}`);
      state.sender = !!body.sender;  // a sending machine sends the checked scripts to the Linux hub's queue
      renderWrite(kind, body);
    } catch (err) {
      els.write[kind].innerHTML = `<div class="da-note">${esc(err.message)}</div>`;
    }
  }

  // No topics left to write: straight to the Topics tab's LLM prompt, already filled in
  const noTopics = () => `<div class="da-note">No topics left to write. Get new ones from your LLM first.</div>
    <div class="pw-add"><button type="button" class="btn-primary" data-get-topics>🧠 Get new topics: open the prompt</button></div>`;
  async function getTopics() {
    showSection("topics");
    const card = $("pwLlmTopics");
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    card.querySelector('[data-l="copy"]').click();  // fills the prompt in (and copies it)
  }

  const FORMAT_TEXT = { why: "❓ why · 35 s", story: "📖 story · 60-115 s" };
  const formatPick = (kind) => `<select id="pwFormat-${kind}" title="A 35 s why, or a money story: 60-115 s, a cast of drawn people, one move each with its math, the bars, which one are you?">
      <option value="why"${state.format[kind] === "story" ? "" : " selected"}>❓ why (35 s)</option><option value="story"${state.format[kind] === "story" ? " selected" : ""}>📖 story (60-115 s)</option></select>`;
  const topicItems = (list) => (list || []).map((t) => `<li>${esc(t.question)}${t.pillar ? ` <span class="hint">(${esc(t.pillar)})</span>` : ""}</li>`).join("");

  function renderWrite(kind, v) {
    const box = els.write[kind];
    const topicList = topicItems(v.topics);
    if (v.step === "none") {
      const story = state.format[kind] === "story";
      const next = story ? v.stories || [] : v.topics || [];
      const list = topicItems(next);
      box.innerHTML = kind === "one"
        ? `<div class="pw-add">${formatPick(kind)}<input type="text" id="pwOneQuestion" placeholder="${story ? "A story idea" : "A question"}, or leave empty for the next one: ${esc((next[0] || {}).question || "none left")}" maxlength="160">
           <button type="button" class="btn-primary" data-write-start="one">Start</button></div>
           ${!next.length && story ? `<div class="da-note">No story ideas yet: get some in the Topics tab (New topics from your own LLM → 📖 money stories), or type one above.</div>` : ""}`
        : `<div class="pw-add">${formatPick(kind)}<label class="hint">Scripts <input type="number" id="pwWeekCount" min="1" max="14" value="7" style="width:70px"></label>
           <button type="button" class="btn-primary" data-write-start="week">Start</button></div>
           ${list ? `<span class="hint">The next ${story ? "story ideas" : "topics, pillars in turn"}:</span><ol>${list}</ol>` : noTopics()}`;
      return;
    }
    const drafts = (v.drafts || []).map((d, i) => `<div class="pw-slot">
        <span class="pw-time">${i + 1}</span>${d.problems.length ? '<span class="pill warn">❌ rules</span>' : v.skipped_rules ? '<span class="pill warn">⏭ rules skipped</span>' : '<span class="pill good">✅ rules</span>'}
        <span class="pw-what"><b>${esc(d.title)}</b> <span class="hint">${d.words} words · ${d.sources} sources</span>
          ${d.problems.length ? `<span class="da-note">${esc(d.problems.join("; "))}</span>` : ""}</span>
        ${kind === "week" ? `<button type="button" class="btn-secondary" data-write-drop="${i + 1}">Leave out</button>` : ""}
      </div>`).join("");
    const pasteTo = v.step === "facts" ? "facts" : "answer";
    box.innerHTML = `<p class="hint"><span class="pill">${FORMAT_TEXT[v.format] || FORMAT_TEXT.why}</span></p><ol class="hint">${topicList}</ol>
      ${(v.general || []).length ? `<span class="da-note">${esc(v.general.join("; "))}</span>` : ""}
      ${drafts}
      ${v.step === "ready"
        ? `<p>✅ Both checks passed.</p><button type="button" class="btn-primary" data-write-queue="${kind}">${state.sender
          ? "📤 Send to Linux</button> <span class=\"hint\">they go to the Linux machine's queue through Discord and fill its free slots</span>"
          : "➕ Add to the queue</button>"}`
        : `<p class="hint">${esc((v.step === "facts" && v.skipped_rules ? STEP_TEXT.facts.replace("The rules pass ✅", "You skipped the rules check ⏭") : STEP_TEXT[v.step === "fix" && v.after_facts ? "flagged" : v.step]) || "")}</p>
           ${v.can_accept ? `<div class="pw-add"><button type="button" class="btn-primary" data-write-accept="${kind}">✅ Accept as it is</button>
             <span class="hint">the flags are kept with the script</span></div>` : ""}
           <div class="pw-add"><button type="button" class="btn-secondary" data-copy="${kind}">📋 Copy the prompt</button>
             <span class="hint">${(v.prompt || "").length.toLocaleString()} characters</span></div>
           <textarea class="pw-prompt" readonly rows="5" id="pwPrompt-${kind}">${esc(v.prompt || "")}</textarea>
           <textarea class="pw-paste" rows="8" id="pwPaste-${kind}" placeholder="Paste the LLM's JSON answer here"></textarea>
           <button type="button" class="btn-primary" data-write-paste="${kind}" data-to="${pasteTo}">Check it</button>
           ${v.step === "fix" && !v.after_facts && (v.drafts || []).length ? `<button type="button" class="btn-secondary" data-write-skip-rules="${kind}" title="Keep the scripts as they are; the fact check comes next">⏭ Skip the rules check</button>` : ""}
           ${v.step === "facts" ? `<button type="button" class="btn-secondary" data-write-skip="${kind}" title="The rules check already wants 2 sources for every claim">⏭ Skip the fact check</button>` : ""}`}
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
      box.insertAdjacentHTML("afterbegin", /no topics left/i.test(err.message) ? noTopics() : `<div class="da-note">${esc(err.message)}</div>`);
    }
  }

  document.addEventListener("change", (e) => {
    const pick = e.target.closest("select[data-topic-pick]");
    if (pick && pick.value && document.body.dataset.workspace === "pricetagwhy") {
      pick.disabled = true;
      return act(() => post("/api/pricetagwhy/topics/first", { question: pick.value }));
    }
    const sel = e.target.closest("select[data-place]");
    if (!sel || !sel.value || document.body.dataset.workspace !== "pricetagwhy") return;
    const [day, slot] = sel.dataset.place.split(" ");
    const [what, ...rest] = sel.value.split(":");
    sel.disabled = true;
    act(() => post("/api/pricetagwhy/plan/place", { day, slot, [what]: rest.join(":") }));
  });

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
    const fromWriting = ["write", "week"].includes((t.closest("[data-pw-panel]") || { dataset: {} }).dataset.pwPanel);
    if (d.make) { t.disabled = true; return act(() => post("/api/pricetagwhy/make", { file: d.make }), fromWriting ? () => showSection("videos") : load); }
    if (d.makeslot) {
      const [day, slot] = d.makeslot.split(" ");
      t.disabled = true;
      return act(() => post("/api/pricetagwhy/make-slot", { day, slot }), fromWriting ? () => showSection("plan") : load);
    }
    if (d.slotRemove) {
      const [day, slot] = d.slotRemove.split(" ");
      if (!window.confirm(`Remove the video from ${day} ${slot}? Its script leaves the plan too (it stays in the Scripts list).`)) return;
      return act(() => post("/api/pricetagwhy/plan/remove", { day, slot }));
    }
    if (d.scriptRemove) return act(() => post("/api/pricetagwhy/scripts/remove", { file: d.scriptRemove }));
    if (d.scriptRestore) return act(() => post("/api/pricetagwhy/scripts/restore", { file: d.scriptRestore }));
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
    if (d.topicWrite) {
      try {
        const { body: now } = await api("/api/pricetagwhy/write/one");
        if (now.step !== "none" && !window.confirm("Write it myself already has a script in progress. Drop it and start this one?")) return;
        await post("/api/pricetagwhy/write/one", { question: d.topicWrite });
      } catch (err) {
        return showError(err.message);
      }
      return showSection("write");
    }
    if (t.hasAttribute("data-get-topics")) return getTopics();
    if (d.topicFirst) return act(() => post("/api/pricetagwhy/topics/first", { question: d.topicFirst }), loadTopics);
    if (d.topicRemove) return act(() => post("/api/pricetagwhy/topics/remove", { question: d.topicRemove }), loadTopics);
    if (d.writeStart) {
      const kind = d.writeStart;
      if (d.restart && !window.confirm("Start over? The pasted answers of this session are dropped.")) return;
      const body = kind === "one" ? { question: ($("pwOneQuestion") || {}).value || "" } : { count: Number(($("pwWeekCount") || {}).value || 7) };
      body.format = ($(`pwFormat-${kind}`) || {}).value || state.format[kind] || "why";
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
      if (!text.trim()) {
        els.write[kind].insertAdjacentHTML("afterbegin", `<div class="da-note">Paste the LLM's answer in the box first${d.to === "facts" ? " (the fact checker's JSON), or press ⏭ Skip the fact check" : ""}.</div>`);
        return;
      }
      return writeAct(kind, () => post(`/api/pricetagwhy/write/${kind}/${d.to}`, { text }));
    }
    if (d.writeAccept) {
      if (!window.confirm("Keep the script as it is, with what the fact checker flagged? Only if you checked those lines yourself.")) return;
      return writeAct(d.writeAccept, () => post(`/api/pricetagwhy/write/${d.writeAccept}/accept`));
    }
    if (d.writeSkipRules) {
      if (!window.confirm("Skip the rules check and keep the script(s) as they are? A script that breaks what the drawing needs can fail when it is made.")) return;
      return writeAct(d.writeSkipRules, () => post(`/api/pricetagwhy/write/${d.writeSkipRules}/skip-rules`));
    }
    if (d.writeSkip) {
      if (!window.confirm("Skip the fact check and keep the script as it is?")) return;
      return writeAct(d.writeSkip, () => post(`/api/pricetagwhy/write/${d.writeSkip}/skip-facts`));
    }
    if (d.writeDrop) return writeAct("week", () => post(`/api/pricetagwhy/write/week/drop/${d.writeDrop}`));
    if (d.writeQueue) {
      const kind = d.writeQueue;
      t.disabled = true;
      try {
        const { body } = await post(`/api/pricetagwhy/write/${kind}/queue`);
        const lands = body.queued.map((name) => {
          const at = (body.slots || {})[name];
          return `<li>${esc(name)}: ${at ? `goes out <b>${esc(at.day)} ${esc(at.slot)}</b>, made at ${esc(at.start.slice(11, 16))}${at.start.slice(0, 10) !== at.day ? ` (${esc(at.start.slice(5, 10))})` : ""}` : "waits for a free slot"}</li>`;
        }).join("");
        const linux = body.where === "linux";
        const first = body.queued.length === 1 && !linux ? (body.slots || {})[body.queued[0]] : null;
        const skipped = (body.skipped || []).map((x) => `<li>${esc(x.title)}: not taken, ${esc(x.why)}</li>`).join("");
        els.write[kind].innerHTML = `<p>✅ ${linux ? "Sent to the Linux machine's queue" : "Added to the queue"}. ${body.waiting} script(s) wait for their slots${linux ? " there" : ""} (Plan).</p><ul>${lands}${skipped}</ul>
          <div class="pw-add">${body.queued.length === 1 && !linux ? `<button type="button" class="btn-primary" data-make="${esc(body.queued[0])}" title="A sample to watch now; the script keeps its slot">🎬 Make the video now</button>` : ""}
          ${first ? `<button type="button" class="btn-secondary" data-makeslot="${esc(first.day)} ${esc(first.slot)}" title="Made now for that slot, waiting for Approve">📅 Make it for ${esc(first.day)} ${esc(first.slot)} now</button>` : ""}
          <button type="button" class="btn-secondary" data-write-start="${kind}">Write ${kind === "one" ? "another" : "the next week"}</button></div>`;
      } catch (err) {
        t.disabled = false;
        els.write[kind].insertAdjacentHTML("afterbegin", `<div class="da-note">${esc(err.message)}</div>`);
      }
    }
  });
  document.addEventListener("change", (e) => {
    const m = /^pwFormat-(one|week)$/.exec(e.target.id || "");
    if (m) { state.format[m[1]] = e.target.value; loadWrite(m[1]); }
  });
  $("pwTopicsRefresh").addEventListener("click", loadTopics);  // the LLM topics card refreshes the list with it
  $("pwAddTopic").addEventListener("submit", (e) => {
    e.preventDefault();
    act(async () => {
      const question = $("pwAddQuestion").value;
      const { body } = await post("/api/pricetagwhy/topics", { question, pillar: $("pwAddPillar").value, format: $("pwAddFormat").value });
      renderTopics(body);
      $("pwAddStatus").textContent = body.sent ? `✅ Sent to the Linux machine's list: ${question}` : `✅ Added: ${question}`;
      $("pwAddQuestion").value = "";
    }, () => {});
  });
  window.addEventListener("message", (e) => { if (e.data === "trendclip:youtube" && document.body.dataset.workspace === "pricetagwhy") load(); });
  const onShow = () => { if (document.body.dataset.workspace === "pricetagwhy") showSection(state.section); };
  new MutationObserver(onShow).observe(document.body, { attributes: true, attributeFilter: ["data-workspace"] });
  onShow();
})();
