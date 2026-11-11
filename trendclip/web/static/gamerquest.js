// SideQuestLogic "The long adventure": Can You Beat Minecraft?, drawn by the bot (trendclip/quest.py). One button
// makes the video as a render job; it then waits in Upload like any other Short.
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const make = $("gqMake");
  if (!make) return;
  const makeOne = $("gqMakeOne");
  const stop = $("gqStop");
  const makeTwice = $("gqMakeTwice");
  const foe = $("gqFoe");
  const makeFight = $("gqMakeFight");
  const makeWall = $("gqMakeWall");
  const makePet = $("gqMakePet");
  const boss = $("gqBoss");
  const block = $("gqBlock");
  const state = $("gqState");
  const list = $("gqVideos");
  const error = $("gwError");
  const STORE = "gamerQuestJob";
  const remember = (id) => { try { id ? localStorage.setItem(STORE, id) : localStorage.removeItem(STORE); } catch { /* private window */ } };
  const recalled = () => { try { return localStorage.getItem(STORE); } catch { return null; } };
  const showError = (msg) => { error.textContent = msg || ""; error.hidden = !msg; };

  async function load() {
    const q = (await api("/api/gamer/quest")).body;
    make.disabled = makeOne.disabled = makeTwice.disabled = makeFight.disabled = makeWall.disabled = makePet.disabled = q.missing.length > 0 || make.dataset.busy === "1";
    if (!boss.options.length) {
      boss.innerHTML = `<option value="">🎲 A random boss</option>` + q.bosses.map((b) => `<option value="${esc(b)}">${esc(b)}</option>`).join("");
    }
    if (!block.options.length) {
      block.innerHTML = `<option value="">🎲 A random wall</option>` + q.walls.map((b) => `<option value="${esc(b)}">${esc(b)}</option>`).join("");
    }
    if (!foe.options.length) {
      foe.innerHTML = `<option value="">🎲 A random mob</option>` + q.foes.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
    }
    if (!stop.options.length) {
      stop.innerHTML = q.solos.map((s) => `<option value="${s.stop}">${esc(s.name)}: ${esc(s.title)}</option>`).join("");
      stop.value = String(q.solos[q.solos.length - 1]?.stop ?? "");
    }
    if (make.dataset.busy !== "1") {
      state.textContent = q.missing.length
        ? `Pictures are missing (${q.missing.slice(0, 4).join(", ")}): make a pause Short first, it fetches them`
        : `${q.stops.join(" → ")} · the narrator ends with ${q.hearts} heart${q.hearts === 1 ? "" : "s"}`;
    }
    list.innerHTML = q.videos.map((v) => {
      const mins = v.seconds ? ` · ${Math.floor(v.seconds / 60)}:${String(Math.round(v.seconds % 60)).padStart(2, "0")}` : "";
      const where = v.uploaded ? "on YouTube" : v.planned ? `📅 planned for ${esc(v.planned)}` : "waiting in Upload, not planned";
      return `<li>🎬 <b>${esc(v.title || "")}</b> · ${esc(v.filename)}${mins} · ${where}</li>`;
    }).join("");
  }

  async function watch(id) {
    let job;
    try {
      job = (await api(`/api/create/jobs/${encodeURIComponent(id)}`)).body;
    } catch {  // the hub was restarted: the job is gone
      remember(null);
      make.dataset.busy = "";
      return load();
    }
    if (job.status === "queued" || job.status === "running") {
      make.dataset.busy = "1";
      make.disabled = makeOne.disabled = makeTwice.disabled = makeFight.disabled = makeWall.disabled = makePet.disabled = true;
      state.textContent = job.status === "queued" ? "Waiting for the video before it…"
        : `${job.message}${job.progress != null ? ` (${Math.round(job.progress * 100)}%)` : ""}…`;
      return setTimeout(() => watch(id), 3000);
    }
    remember(null);
    make.dataset.busy = "";
    if (job.status === "error") showError(`The adventure was not made: ${job.error}`);
    await load();
    if (job.status === "done") {  // which video: every button on this card reports here
      const made = job.result || {};
      state.textContent = made.filename ? `✅ Made "${made.title || made.filename}" (${made.filename}): it is in Upload and on the plan's next free slot (the list below says when)`
        : "✅ Made: it waits in Upload";
    }
  }

  async function start(only, twice) {
    showError("");
    make.disabled = makeOne.disabled = makeTwice.disabled = makeFight.disabled = makeWall.disabled = makePet.disabled = true;
    try {
      const id = (await api("/api/gamer/quest", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(twice ? { [twice]: true, foe: twice === "pet" ? boss.value : foe.value, block: block.value } : { stop: only }) })).body.job;
      remember(id);
      watch(id);
    } catch (err) {
      showError(err.message);
      make.disabled = makeOne.disabled = makeTwice.disabled = makeFight.disabled = makeWall.disabled = makePet.disabled = false;
    }
  }

  make.addEventListener("click", () => start(0));
  makeOne.addEventListener("click", () => start(Number(stop.value)));
  makeTwice.addEventListener("click", () => start(0, "twice"));
  makeFight.addEventListener("click", () => start(0, "fight"));
  makeWall.addEventListener("click", () => start(0, "wall"));
  makePet.addEventListener("click", () => start(0, "pet"));

  load().catch((err) => showError(err.message));
  if (recalled()) watch(recalled());
})();
