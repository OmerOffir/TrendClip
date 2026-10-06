(() => {
  "use strict";

  const T = window.TrendClip;
  const { api, esc } = T;
  const $ = (id) => document.getElementById(id);

  const els = {
    form: $("fForm"),
    query: $("fQuery"),
    all: $("fAll"),
    go: $("fGo"),
    quick: $("fQuick"),
    status: $("fStatus"),
    channels: $("fChannels"),
    results: $("fResults"),
    sources: $("fSources"),
    sourceList: $("fSourceList"),
  };

  const SAYS = { title: "says it in the title", description: "says it in the description", channel: "channel name says it" };
  const state = { data: null, sources: null, busy: false, seq: 0 };

  function setStatus(text, error) {
    els.status.hidden = !text;
    els.status.textContent = text || "";
    els.status.classList.toggle("over", !!error);
  }

  function renderQuick() {
    const top = T.getTopGames().slice(0, 6);
    els.quick.innerHTML = top.length
      ? `<span class="hint">Trending:</span>${top.map((g) => `<button type="button" class="chip-btn" data-q="${esc(g)}">${esc(g)}</button>`).join("")}`
      : "";
  }

  async function search(e) {
    if (e) e.preventDefault();
    const q = els.query.value.trim();
    const seq = ++state.seq;
    state.busy = true;
    els.go.disabled = true;
    els.go.textContent = "Searching…";
    setStatus(`Searching YouTube for "${q || "any game"}" no copyright gameplay…`);
    try {
      const { body } = await api(`/api/sources/search?q=${encodeURIComponent(q)}${els.all.checked ? "&all=true" : ""}`);
      if (seq !== state.seq) return;
      state.data = body;
      const n = body.results.length;
      setStatus(n ? `${n} videos${els.all.checked ? "" : " that say they're free to use"} · from ${body.channels.length} channels` : "Nothing found. Try other words, or tick Show all results.");
      render();
    } catch (err) {
      if (seq === state.seq) setStatus(`Search failed: ${err.message}`, true);
    } finally {
      if (seq === state.seq) {
        state.busy = false;
        els.go.disabled = false;
        els.go.textContent = "Search YouTube";
      }
    }
  }

  function channelChip(c) {
    const games = c.games.slice(0, 3).join(", ");
    return `
      <div class="find-channel${c.in_sources ? " added" : ""}">
        <div>
          <a href="${esc(c.url)}" target="_blank" rel="noopener"><b>${esc(c.title)}</b></a>
          <div class="dl-meta">${c.hits} matching video${c.hits === 1 ? "" : "s"}${games ? ` · ${esc(games)}` : ""}</div>
        </div>
        ${c.in_sources
          ? '<span class="pill done">In your sources</span>'
          : `<button type="button" class="dl-ready" data-add-channel="${esc(c.channel_id)}" data-title="${esc(c.title)}" data-handle="${esc(c.handle)}">+ Add as source</button>`}
      </div>`;
  }

  function resultCard(r) {
    const thumb = `https://i.ytimg.com/vi/${encodeURIComponent(r.video_id)}/mqdefault.jpg`;
    const badges = [
      r.says_free ? `<span class="pill good" title="${esc(SAYS[r.says_free])}">free to use</span>` : '<span class="pill warn">not stated</span>',
      r.vertical ? '<span class="pill">9:16</span>' : "",
      r.used ? '<span class="pill">already taken</span>' : "",
    ].join("");
    const meta = [r.views != null ? `${T.compact(r.views)} views` : "", r.games.join(", ")].filter(Boolean).map(esc).join(" · ");
    return `
      <div class="vcard find-card">
        <a class="thumb" href="${esc(r.url)}" target="_blank" rel="noopener">
          <img src="${thumb}" alt="" loading="lazy" />
          <span class="badges">${badges}</span>
          ${r.duration ? `<span class="dur">${esc(T.fmtDuration(Math.round(r.duration)))}</span>` : ""}
        </a>
        <div class="vbody">
          <a class="vtitle" href="${esc(r.url)}" target="_blank" rel="noopener" title="${esc(r.title)}">${esc(r.title)}</a>
          <div class="vchan">${esc(r.channel_title)}${r.in_sources ? " · ✓ source" : ""}</div>
          ${meta ? `<div class="dl-meta">${meta}</div>` : ""}
          <div class="find-actions">
            <button type="button" class="dl-create" data-take="${esc(r.video_id)}">Get clip</button>
            ${r.channel_id && !r.in_sources
              ? `<button type="button" class="dl-copy" data-add-channel="${esc(r.channel_id)}" data-title="${esc(r.channel_title)}" data-handle="${esc(r.channel_handle)}">+ Channel</button>`
              : ""}
          </div>
        </div>
      </div>`;
  }

  function render() {
    const d = state.data;
    if (!d) return;
    els.channels.hidden = !d.channels.length;
    els.channels.innerHTML = d.channels.length
      ? `<div class="find-channels-head"><b>Channels with free-to-use gameplay</b><span class="hint">Add one and <b>Get gameplay</b> also picks random clips from its videos.</span></div>
         <div class="find-channel-list">${d.channels.slice(0, 12).map(channelChip).join("")}</div>`
      : "";
    els.results.innerHTML = d.results.map(resultCard).join("");
  }

  async function loadSources() {
    try {
      state.sources = (await api("/api/sources/channels")).body;
    } catch (_) {
      return;
    }
    renderSources();
  }

  function renderSources() {
    const s = state.sources;
    if (!s) return;
    const lib = T.getLibrary();
    const counts = {};
    for (const c of (lib && lib.channels) || []) counts[c.id] = c.listed;
    els.sources.querySelector("summary").textContent = `Your gameplay channels (${s.env.length + s.added.length})`;
    const env = s.env.map((ref) => `<li><b>${esc(ref)}</b> <span class="hint">from .env (NCG_CHANNELS)</span></li>`).join("");
    const added = s.added.map((c) => `
      <li>
        <a href="${c.handle && c.handle.startsWith("@") ? `https://www.youtube.com/${esc(c.handle)}` : `https://www.youtube.com/channel/${esc(c.channel_id)}`}" target="_blank" rel="noopener"><b>${esc(c.title || c.channel_id)}</b></a>
        <span class="hint">added${counts[c.channel_id] != null ? ` · ${counts[c.channel_id]} videos` : ""}</span>
        <button type="button" class="dl-delete" data-remove-channel="${esc(c.channel_id)}">Remove</button>
      </li>`).join("");
    const typed = els.sourceList.querySelector("#fChannelLink");
    const keep = typed ? typed.value : "";
    els.sourceList.innerHTML = `
      <form class="channel-add" id="fChannelForm">
        <input id="fChannelLink" type="text" placeholder="Paste a channel link, e.g. https://www.youtube.com/@NoCopyrightGameplays or @handle" />
        <button type="submit" class="dl-ready" id="fChannelAdd">+ Add channel</button>
      </form>
      <ul class="source-list">${env}${added}</ul>
      ${s.added.length ? "" : '<div class="hint">Paste a channel above, or search and press <b>+ Add as source</b>.</div>'}`;
    els.sourceList.querySelector("#fChannelLink").value = keep;
  }

  async function addChannelLink(e) {
    e.preventDefault();
    const input = els.sourceList.querySelector("#fChannelLink");
    const btn = els.sourceList.querySelector("#fChannelAdd");
    const link = input.value.trim();
    if (!link) return input.focus();
    btn.disabled = true;
    btn.textContent = "Adding…";
    try {
      const body = (await api("/api/sources/channels/link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ link }),
      })).body;
      state.sources = body;
      input.value = "";
      markChannel(body.channel.channel_id, true);
      renderSources();
      setStatus(`Added ${body.channel.title} (${body.channel.video_count} videos). Listing its videos…`);
      await T.reloadLibrary();
      renderSources();
      setStatus(`Added ${body.channel.title} to your gameplay channels: Get gameplay now also picks clips from it.`);
    } catch (err) {
      setStatus(`Could not add the channel: ${err.message}`, true);
      btn.disabled = false;
      btn.textContent = "+ Add channel";
    }
  }

  async function addChannel(btn) {
    btn.disabled = true;
    try {
      state.sources = (await api("/api/sources/channels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ channel_id: btn.dataset.addChannel, title: btn.dataset.title, handle: btn.dataset.handle }),
      })).body;
      markChannel(btn.dataset.addChannel, true);
      renderSources();
      setStatus(`Added ${btn.dataset.title}. Its videos are listed now (uses a little YouTube API quota)…`);
      await T.reloadLibrary();
      renderSources();
      setStatus(`Added ${btn.dataset.title} to your sources.`);
    } catch (err) {
      btn.disabled = false;
      setStatus(`Could not add the channel: ${err.message}`, true);
    }
  }

  async function removeChannel(id) {
    try {
      state.sources = (await api(`/api/sources/channels/${encodeURIComponent(id)}`, { method: "DELETE" })).body;
      markChannel(id, false);
      renderSources();
      T.reloadLibrary().then(renderSources);
    } catch (err) {
      setStatus(`Could not remove the channel: ${err.message}`, true);
    }
  }

  function markChannel(id, on) {
    if (!state.data) return;
    for (const r of state.data.results) if (r.channel_id === id) r.in_sources = on;
    for (const c of state.data.channels) if (c.channel_id === id) c.in_sources = on;
    render();
  }

  async function take(btn) {
    const r = state.data && state.data.results.find((x) => x.video_id === btn.dataset.take);
    if (!r) return;
    btn.disabled = true;
    btn.textContent = "Downloading…";
    try {
      await T.submitLink(r.url, r.games[0] || "");
      r.used = true;
      btn.textContent = "Started ✓";
      $("downloadsCard").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      btn.disabled = false;
      btn.textContent = "Get clip";
      setStatus(`Download could not start: ${err.message}`, true);
    }
  }

  els.form.addEventListener("submit", search);
  els.sourceList.addEventListener("submit", (e) => {
    if (e.target.id === "fChannelForm") addChannelLink(e);
  });
  els.all.addEventListener("change", () => state.data && search());
  els.quick.addEventListener("click", (e) => {
    const b = e.target.closest("[data-q]");
    if (!b) return;
    els.query.value = b.dataset.q;
    search();
  });
  $("findCard").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.take) take(b);
    else if (b.dataset.addChannel) addChannel(b);
    else if (b.dataset.removeChannel) removeChannel(b.dataset.removeChannel);
  });
  document.addEventListener("trendclip:trends", renderQuick);
  document.addEventListener("trendclip:find", (e) => {
    els.query.value = e.detail.query || "";
    $("findCard").scrollIntoView({ behavior: "smooth", block: "start" });
    search();
  });
  renderQuick();
  loadSources();
})();
