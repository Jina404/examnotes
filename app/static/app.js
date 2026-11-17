/*
 * ExamNotes front end - plain JavaScript, no build step.
 *
 * Model output is always inserted with textContent, never innerHTML, so a PDF
 * can never inject markup into the page.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const MAX_BYTES = 20 * 1024 * 1024;
  const HISTORY_KEY = "examnotes.history.v1";

  let selectedFile = null;
  let current = null;          // { document_id, filename, pages, result }

  // ---------------------------------------------------------------- upload UI

  const dropzone = $("dropzone");
  const fileInput = $("file-input");

  const pickFile = () => fileInput.click();
  $("attach-btn").addEventListener("click", (e) => { e.stopPropagation(); pickFile(); });
  dropzone.addEventListener("click", pickFile);
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pickFile(); }
  });

  ["dragenter", "dragover"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dropzone.addEventListener(ev, (e) => {
      e.preventDefault();
      dropzone.classList.remove("dragover");
    })
  );
  dropzone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files.length) setFile(fileInput.files[0]);
  });

  function setFile(file) {
    // A friendly check here; the server checks the real file signature too.
    const looksPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name);
    if (!looksPdf) return showError("Please choose a PDF file.");
    if (file.size > MAX_BYTES) {
      return showError(`That file is ${(file.size / 1048576).toFixed(1)} MB. The limit is 20 MB.`);
    }
    selectedFile = file;
    const label = $("file-name");
    label.textContent = `Selected: ${file.name} (${prettySize(file.size)})`;
    label.hidden = false;
    $("generate-btn").disabled = false;
    hideError();
  }

  // ------------------------------------------------------------------ helpers

  function prettySize(bytes) {
    return bytes < 1048576
      ? `${Math.max(1, Math.round(bytes / 1024))} KB`
      : `${(bytes / 1048576).toFixed(1)} MB`;
  }

  function showError(message) {
    const box = $("error");
    box.textContent = message;
    box.hidden = false;
    box.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  function hideError() { $("error").hidden = true; }

  function setLoading(on, text) {
    $("loading").hidden = !on;
    if (text) $("loading-text").textContent = text;
    $("generate-btn").disabled = on || !selectedFile;
  }

  async function postForm(url, formData) {
    const res = await fetch(url, { method: "POST", body: formData });
    const data = await res.json().catch(() => ({ error: "The server returned an unreadable response." }));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
    return data;
  }

  async function postJson(url, payload) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({ error: "The server returned an unreadable response." }));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
    return data;
  }

  // ----------------------------------------------------------------- generate

  $("generate-btn").addEventListener("click", async () => {
    if (!selectedFile) return;
    hideError();
    $("results").hidden = true;

    const types = [...document.querySelectorAll(".qtype:checked")].map((c) => c.value);
    if (!types.length) return showError("Please choose at least one question type.");

    const form = new FormData();
    form.append("file", selectedFile);
    form.append("question_count", $("question-count").value || "10");
    form.append("question_types", types.join(","));
    form.append("difficulty", $("difficulty").value);

    const steps = [
      "Reading your PDF…",
      "Extracting key points page by page…",
      "Grouping the notes by topic…",
      "Writing exam questions…",
      "Almost there…",
    ];
    let step = 0;
    setLoading(true, steps[0]);
    const ticker = setInterval(() => {
      step = Math.min(step + 1, steps.length - 1);
      $("loading-text").textContent = steps[step];
    }, 9000);

    try {
      const data = await postForm("/api/documents", form);
      current = data;
      render(data);
      saveToHistory(data);
    } catch (err) {
      showError(err.message);
    } finally {
      clearInterval(ticker);
      setLoading(false);
    }
  });

  // ------------------------------------------------------------------- render

  function render(data) {
    const result = data.result || { notes: [], questions: [] };
    $("result-title").textContent = data.filename ? `Revision pack: ${data.filename}` : "Your revision pack";
    const bits = [];
    if (data.pages) bits.push(`${data.pages} pages`);
    bits.push(`${(result.notes || []).length} topics`);
    bits.push(`${(result.questions || []).length} questions`);
    if (data.cached) bits.push("loaded from cache");
    $("result-meta").textContent = bits.join(" · ");

    renderNotes(result.notes || []);
    renderQuestions(result.questions || []);

    $("chat-log").replaceChildren();
    $("results").hidden = false;
    $("results").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderNotes(notes) {
    const host = $("notes");
    host.replaceChildren();
    if (!notes.length) {
      host.appendChild(el("p", "dz-hint", "No notes were produced for this document."));
      return;
    }
    notes.forEach((group) => {
      const box = el("div", "topic");
      box.appendChild(el("h4", null, group.topic || "Topic"));
      const ul = document.createElement("ul");
      (group.points || []).forEach((p) => ul.appendChild(el("li", null, p)));
      box.appendChild(ul);
      host.appendChild(box);
    });
  }

  function renderQuestions(questions) {
    const host = $("questions");
    host.replaceChildren();
    if (!questions.length) {
      host.appendChild(el("p", "dz-hint", "No questions were produced for this document."));
      return;
    }
    questions.forEach((q, i) => {
      const box = el("div", "question");

      const head = el("div", "q-head");
      head.appendChild(el("span", "q-num", `${i + 1}.`));
      head.appendChild(el("span", "q-text", q.question || ""));
      head.appendChild(el("span", "tag", `p. ${q.page ?? "?"}`));
      head.appendChild(el("span", "tag", prettyType(q.type)));
      if (q.difficulty) head.appendChild(el("span", "tag", q.difficulty));
      box.appendChild(head);

      if (q.type === "multiple_choice" && Array.isArray(q.options) && q.options.length) {
        const ol = document.createElement("ol");
        ol.className = "q-options";
        ol.type = "A";
        q.options.forEach((opt) => ol.appendChild(el("li", null, opt)));
        box.appendChild(ol);
      }

      const answer = el("div", "answer", q.answer || "");
      answer.hidden = true;
      const toggle = el("button", "btn btn-small", "Show answer");
      toggle.type = "button";
      toggle.addEventListener("click", () => {
        answer.hidden = !answer.hidden;
        toggle.textContent = answer.hidden ? "Show answer" : "Hide answer";
      });
      box.appendChild(el("div", "q-actions", "")).appendChild(toggle);
      box.appendChild(answer);

      host.appendChild(box);
    });
  }

  function prettyType(type) {
    return ({
      multiple_choice: "multiple choice",
      short_answer: "short answer",
      long_answer: "long answer",
    })[type] || type || "question";
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  // --------------------------------------------------------- copy / download

  function notesToMarkdown(result, filename) {
    const lines = [`# Revision notes${filename ? `: ${filename}` : ""}`, ""];
    (result.notes || []).forEach((g) => {
      lines.push(`## ${g.topic || "Topic"}`, "");
      (g.points || []).forEach((p) => lines.push(`- ${p}`));
      lines.push("");
    });
    return lines.join("\n");
  }

  function fullMarkdown(data) {
    const result = data.result || {};
    const lines = [notesToMarkdown(result, data.filename), "---", "", "# Exam questions", ""];
    (result.questions || []).forEach((q, i) => {
      lines.push(`### ${i + 1}. ${q.question || ""}`);
      lines.push(`*${prettyType(q.type)} · ${q.difficulty || ""} · page ${q.page ?? "?"}*`, "");
      if (q.type === "multiple_choice" && Array.isArray(q.options)) {
        q.options.forEach((opt, j) => lines.push(`${String.fromCharCode(65 + j)}. ${opt}`));
        lines.push("");
      }
      lines.push(`**Answer:** ${q.answer || ""}`, "");
    });
    return lines.join("\n");
  }

  $("copy-notes").addEventListener("click", async () => {
    if (!current) return;
    const text = notesToMarkdown(current.result || {}, current.filename);
    const btn = $("copy-notes");
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "Copied ✓";
    } catch {
      btn.textContent = "Copy failed";
    }
    setTimeout(() => { btn.textContent = "Copy notes"; }, 1800);
  });

  $("download-md").addEventListener("click", () => {
    if (!current) return;
    const blob = new Blob([fullMarkdown(current)], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const base = (current.filename || "examnotes").replace(/\.pdf$/i, "");
    a.href = url;
    a.download = `${base}-examnotes.md`;
    a.click();
    URL.revokeObjectURL(url);
  });

  // --------------------------------------------------------------------- chat

  $("chat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("chat-input");
    const message = input.value.trim();
    if (!message || !current) return;

    const log = $("chat-log");
    log.appendChild(el("div", "bubble me", message));
    input.value = "";
    const thinking = el("div", "bubble ai", "Thinking…");
    log.appendChild(thinking);
    $("chat-send").disabled = true;

    try {
      const data = await postJson(`/api/documents/${current.document_id}/chat`, { message });
      renderMarkdown(thinking, data.reply || "(no reply)");
    } catch (err) {
      thinking.textContent = `⚠️ ${err.message}`;
    } finally {
      $("chat-send").disabled = false;
      thinking.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  });

  /*
   * Minimal, safe Markdown rendering for chat replies: headings, bullets,
   * numbered lists, bold. Every node is built with createElement/textContent,
   * so model output can never become live markup.
   */
  function renderMarkdown(host, markdown) {
    host.replaceChildren();
    let list = null;

    const inline = (node, text) => {
      // Split on **bold** / *italic* and emit plain text either side.
      text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).forEach((part) => {
        if (!part) return;
        if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
          node.appendChild(el("strong", null, part.slice(2, -2)));
        } else if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
          node.appendChild(el("em", null, part.slice(1, -1)));
        } else {
          node.appendChild(document.createTextNode(part));
        }
      });
      return node;
    };

    (markdown || "").split("\n").forEach((rawLine) => {
      const line = rawLine.trimEnd();
      const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
      const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
      const heading = line.match(/^(#{1,6})\s+(.*)$/);

      if (bullet || numbered) {
        const wanted = bullet ? "ul" : "ol";
        if (!list || list.tagName.toLowerCase() !== wanted) {
          list = document.createElement(wanted);
          list.className = "md-list";
          host.appendChild(list);
        }
        list.appendChild(inline(document.createElement("li"), (bullet || numbered)[1]));
        return;
      }
      list = null;

      if (heading) {
        const level = Math.min(6, heading[1].length + 3);   // ## -> h5, keeps it small
        host.appendChild(inline(document.createElement(`h${level}`), heading[2]));
      } else if (line.trim()) {
        host.appendChild(inline(document.createElement("p"), line));
      }
    });

    if (!host.childNodes.length) host.textContent = markdown || "";
  }

  // ------------------------------------------------------------------ history

  function loadHistory() {
    try { return JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; }
    catch { return []; }
  }

  function saveToHistory(data) {
    const entry = {
      document_id: data.document_id,
      filename: data.filename || "document.pdf",
      pages: data.pages,
      at: new Date().toISOString(),
      result: data.result,
    };
    const history = [entry, ...loadHistory().filter((h) => h.document_id !== entry.document_id)].slice(0, 10);
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(history)); } catch { /* quota: ignore */ }
    renderHistory();
  }

  function renderHistory() {
    const history = loadHistory();
    const card = $("history-card");
    const list = $("history-list");
    list.replaceChildren();
    if (!history.length) { card.hidden = true; return; }

    history.forEach((entry) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.appendChild(el("span", null, entry.filename));
      const when = new Date(entry.at).toLocaleString();
      const count = (entry.result?.questions || []).length;
      btn.appendChild(el("span", "h-meta", `${when} · ${count} questions`));
      btn.addEventListener("click", () => {
        current = entry;
        render(entry);
      });
      li.appendChild(btn);
      list.appendChild(li);
    });
    card.hidden = false;
  }

  $("clear-history").addEventListener("click", () => {
    try { localStorage.removeItem(HISTORY_KEY); } catch { /* ignore */ }
    renderHistory();
  });

  renderHistory();
})();

/*
 * UI shell: theme toggle, result tabs, and the engine/limits badge.
 * Kept separate from the app logic above; it only touches presentation.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------------- theme

  const THEME_KEY = "examnotes.theme";
  const root = document.documentElement;
  const stored = (() => {
    try { return localStorage.getItem(THEME_KEY); } catch { return null; }
  })();
  const prefersDark =
    window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;

  setTheme(stored || root.dataset.theme || (prefersDark ? "dark" : "light"));

  function setTheme(theme) {
    root.dataset.theme = theme;
    const toggle = $("theme-toggle");
    if (toggle) {
      toggle.querySelector(".theme-icon").textContent = theme === "light" ? "Night" : "Paper";
      toggle.title = theme === "light" ? "Switch to the dark theme" : "Switch to the paper theme";
    }
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* ignore */ }
  }

  $("theme-toggle")?.addEventListener("click", () => {
    setTheme(root.dataset.theme === "light" ? "dark" : "light");
  });

  // -------------------------------------------------------------------- tabs

  const tabs = [...document.querySelectorAll(".tab")];
  const panels = [...document.querySelectorAll(".panel")];

  function showPanel(id) {
    tabs.forEach((t) => {
      const on = t.dataset.panel === id;
      t.classList.toggle("is-active", on);
      t.setAttribute("aria-selected", String(on));
    });
    panels.forEach((p) => p.classList.toggle("is-active", p.id === id));
  }

  tabs.forEach((tab) => tab.addEventListener("click", () => showPanel(tab.dataset.panel)));

  // Whenever a new result is rendered, go back to Notes and refresh the counts.
  const results = $("results");
  if (results) {
    let wasHidden = results.hidden;
    new MutationObserver(() => {
      if (!results.hidden && wasHidden) {
        showPanel("panel-notes");
        labelTabs();
      }
      wasHidden = results.hidden;
    }).observe(results, { attributes: true, attributeFilter: ["hidden"] });
  }

  function labelTabs() {
    const counts = {
      "panel-notes": document.querySelectorAll("#notes .topic").length,
      "panel-questions": document.querySelectorAll("#questions .question").length,
    };
    tabs.forEach((tab) => {
      const n = counts[tab.dataset.panel];
      const base = tab.textContent.replace(/\s*\(\d+\)$/, "");
      tab.textContent = n ? `${base} (${n})` : base;
    });
  }

  // ------------------------------------------------------- engine + limits

  const ENGINE_LABELS = {
    gemini: "Gemini",
    claude_cli: "Claude CLI",
    remote: "Claude (remote)",
    fake: "demo mode",
  };

  fetch("/api/health")
    .then((res) => (res.ok ? res.json() : null))
    .then((health) => {
      if (!health) return;

      const pill = $("engine-pill");
      if (pill && health.provider) {
        $("engine-name").textContent = ENGINE_LABELS[health.provider] || health.provider;
        pill.hidden = false;
      }

      const limits = health.limits || {};
      const hint = $("limits-hint");
      if (hint && limits.max_upload_mb && limits.max_pages) {
        hint.textContent =
          `PDF only · max ${limits.max_upload_mb} MB · max ${limits.max_pages} pages · ` +
          "text PDFs only (no scans)";
      }

      const count = $("question-count");
      if (count && limits.max_questions) count.max = limits.max_questions;
    })
    .catch(() => { /* badge is optional */ });
})();
