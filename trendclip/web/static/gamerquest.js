// SideQuestLogic "The long adventure": Can You Beat Minecraft?, drawn by the bot (trendclip/quest.py). One button
// makes the video as a render job; it then waits in Upload like any other Short.
(() => {
  const { api, esc } = window.TrendClip;
  const $ = (id) => document.getElementById(id);
  const make = $("gqMake");
  if (!make) return;
  const state = $("gqState");
  const list = $("gqVideos");
  const error = $("gwError");
  const STORE = "gamerQuestJob";
  const remember = (id) => { try { id ? localStorage.setItem(STORE, id) : localStorage.removeItem(STORE); } catch { /* private window */ } };
  const recalled = () => { try { return localStorage.getItem(STORE); } catch { return null; } };
  const showError = (msg) => { error.textContent = msg || ""; error.hidden = !msg; };

  async function load() {
    const q = (await api("/api/gamer/quest")).body;
    make.disabled = q.missing.length > 0 || make.dataset.busy === "1";
    if (make.dataset.busy !== "1") {
      state.textContent = q.missing.length
        ? `Pictures are missing (${q.missing.slice(0, 4).join(", ")}): make a pause Short first, it fetches them`
        : `${q.stops.join(" → ")} · the narrator ends with ${q.hearts} heart${q.hearts === 1 ? "" : "s"}`;
    }
    list.innerHTML = q.videos.map((v) => {
      const mins = v.seconds ? ` · ${Math.floor(v.seconds / 60)}:${String(Math.round(v.seconds % 60)).padStart(2, "0")}` : "";
      return `<li>🎬 ${esc(v.filename)}${mins} · ${v.uploaded ? "on YouTube" : "waiting in Upload"}</li>`;
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
      make.disabled = true;
      state.textContent = job.status === "queued" ? "Waiting for the video before it…"
        : `${job.message}${job.progress != null ? ` (${Math.round(job.progress * 100)}%)` : ""}…`;
      return setTimeout(() => watch(id), 3000);
    }
    remember(null);
    make.dataset.busy = "";
    if (job.status === "error") showError(`The adventure was not made: ${job.error}`);
    await load();
    if (job.status === "done") state.textContent = "✅ Made: it waits in Upload";
  }

  make.addEventListener("click", async () => {
    showError("");
    make.disabled = true;
    try {
      const id = (await api("/api/gamer/quest", { method: "POST" })).body.job;
      remember(id);
      watch(id);
    } catch (err) {
      showError(err.message);
      make.disabled = false;
    }
  });

  load().catch((err) => showError(err.message));
  if (recalled()) watch(recalled());
})();
