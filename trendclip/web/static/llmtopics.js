// "New topics/stories from your own LLM" (Dark Outliers Stories, Borderline Why Topics): the channel's Gemini
// prompt to copy into any LLM, and its pasted answer added to the list (modules/fetcher.py, modules/bw_topics.py):
// the hub's own list, through Discord, when this dashboard runs on the sending machine (channel_hub.add_topics).
(() => {
  const { api, esc } = window.TrendClip;
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  document.querySelectorAll("[data-llm-topics]").forEach((card) => {
    const url = card.dataset.llmTopics;
    const el = (name) => card.querySelector(`[data-l="${name}"]`);
    const status = el("status");
    const skippedWhy = card.dataset.skipped || "already there or no Wikipedia article";

    // Fetched on each copy: the list of cases or topics to avoid grows. The clipboard API needs https or
    // localhost; the hub's dashboard is plain http on the LAN, so select-and-copy too.
    el("copy").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      const count = el("count");
      const format = el("format");  // a channel with more than one format (Price Tag Why: why | story)
      const q = [count ? `count=${encodeURIComponent(count.value)}` : "", format ? `format=${encodeURIComponent(format.value)}` : ""].filter(Boolean).join("&");
      try {
        const { body } = await api(q ? `${url}?${q}` : url);
        const box = el("prompt");
        box.value = body.prompt;
        try {
          await navigator.clipboard.writeText(box.value);
        } catch {
          box.select();
          document.execCommand("copy");
        }
        btn.textContent = "✅ Copied";
        setTimeout(() => { btn.textContent = "📋 Copy the prompt"; }, 2000);
      } catch (err) {
        status.textContent = `❌ ${err.message}`;
      }
    });

    el("add").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      const text = el("answer").value;
      if (!text.trim()) return;
      btn.disabled = true;
      status.textContent = card.dataset.checking || "Checking Wikipedia (through Discord on Linux when this isn't the hub)…";
      try {
        const { body } = await post(url, { text });
        const where = body.where === "linux" ? " on Linux" : "";
        status.innerHTML = body.added.length
          ? `✅ Added ${body.added.length}${where}${body.skipped ? ` · ${body.skipped} skipped (${esc(skippedWhy)})` : ""}: ${body.added.map(esc).join(" · ")}`
          : `Nothing new${where}: all ${body.skipped} were ${esc(skippedWhy)}`;
        if (body.added.length) el("answer").value = "";
        const refresh = document.getElementById(card.dataset.refresh);
        refresh?.dispatchEvent(new Event(refresh.tagName === "SELECT" ? "change" : "click"));
      } catch (err) {
        status.textContent = `❌ ${err.message}`;
      } finally {
        btn.disabled = false;
      }
    });
  });
})();
