/* ── Config ──────────────────────────────────────────────────────── */
const BASE =
  globalThis.location.protocol === "file:"
    ? "http://127.0.0.1:8081"
    : "";

const UPLOAD_URL = `${BASE}/api/upload`;
const STREAM_URL = (jobId) => `${BASE}/api/stream/${jobId}`;
const OLLAMA_HEALTH_URL = `${BASE}/api/health/ollama`;
const BACKEND_HEALTH_URL = `${BASE}/health`;

/* ── DOM Refs ────────────────────────────────────────────────────── */
const analyzeBtn    = document.getElementById("analyzeBtn");
const copyMermaidBtn= document.getElementById("copyMermaidBtn");
const fileInput     = document.getElementById("fileInput");
const folderInput   = document.getElementById("folderInput");
const codeInput     = document.getElementById("codeInput");
const statusMsg     = document.getElementById("statusMsg");
const progressWrap  = document.getElementById("progressWrap");
const progressBar   = document.getElementById("progressBar");
const resultBadge   = document.getElementById("resultBadge");
const fileList      = document.getElementById("fileList");
const fileArea      = document.getElementById("fileArea");
const pasteArea     = document.getElementById("pasteArea");
const dropZone      = document.getElementById("dropZone");
const dropModeLabel = document.getElementById("dropModeLabel");
const dropHint      = document.getElementById("dropHint");
const skeletonWrap  = document.getElementById("skeletonWrap");
const fileProgressEl = document.getElementById("fileProgress");
const ollamaGate   = document.getElementById("ollamaGate");
const backendGate  = document.getElementById("backendGate");
const analysisTracker = document.getElementById("analysisTracker");
const analysisRows = document.getElementById("analysisRows");
const analysisCount = document.getElementById("analysisCount");
const analysisOverall = document.getElementById("analysisOverall");

const panes = {
  explanation: {
    pane:    document.getElementById("pane-explanation"),
    empty:   document.getElementById("emptyExplanation"),
    content: document.getElementById("explanation"),
  },
  steps: {
    pane:    document.getElementById("pane-steps"),
    empty:   document.getElementById("emptySteps"),
    content: document.getElementById("steps"),
  },
  diagram: {
    pane:    document.getElementById("pane-diagram"),
    empty:   document.getElementById("emptyDiagram"),
    content: document.getElementById("diagramWrap"),
  },
};

let currentMode    = "file";
let selectedFiles  = [];
let lastMermaidText = "";
let _fpTimer       = null;
let _health = { ollama: false, backend: false };

/* ── File progress tracker ───────────────────────────────────────── */
function initFileProgress(files) {
  fileProgressEl.innerHTML = "";
  fileProgressEl.classList.add("hidden");

  analysisRows.innerHTML = "";
  analysisTracker.classList.toggle("hidden", !files || files.length === 0);
  const total = files?.length || 0;
  analysisCount.textContent = `Total Completed: 0 / ${total}`;
  analysisOverall.textContent = "Overall Progress: 0%";

  if (!files || files.length === 0) return;
  files.forEach((f, i) => {
    const row = document.createElement("div");
    row.className = "analysis-row";
    row.id = `ar-${i}`;
    const name = f.name.length > 58 ? `${f.name.slice(0, 56)}...` : f.name;
    row.innerHTML = `<span class="analysis-file">${name}</span><span class="analysis-pct">0%</span>`;
    analysisRows.appendChild(row);
  });
}

function startFileProgressAnim(_files) {
  // No-op: progress is now driven by real SSE events from the backend
}

function finishFileProgress(files) {
  if (_fpTimer) { clearInterval(_fpTimer); _fpTimer = null; }
  if (!files || files.length === 0) return;
  files.forEach((_, i) => setAnalysisRowProgress(i, 100));
  setAnalysisFooter(files.length, files.length);
}

function setAnalysisRowProgress(index, percent) {
  const row = document.getElementById(`ar-${index}`);
  if (!row) return;
  const pct = Math.max(0, Math.min(100, Math.round(percent)));
  row.querySelector(".analysis-pct").textContent = `${pct}%`;
  row.classList.toggle("done", pct >= 100);
}

function setAnalysisFooter(completed, total) {
  const safeTotal = Math.max(total || 0, 1);
  const pct = Math.round((completed / safeTotal) * 100);
  analysisCount.textContent = `Total Completed: ${completed} / ${total}`;
  analysisOverall.textContent = `Overall Progress: ${pct}%`;
}

async function runHealthChecks() {
  let ollamaOk = false;
  let backendOk = false;

  try {
    const br = await fetch(BACKEND_HEALTH_URL);
    backendOk = br.ok;
  } catch {
    backendOk = false;
  }

  try {
    const or = await fetch(OLLAMA_HEALTH_URL);
    if (or.ok) {
      const data = await or.json();
      ollamaOk = Boolean(data.ok);
    }
  } catch {
    ollamaOk = false;
  }

  _health = { ollama: ollamaOk, backend: backendOk };
  renderGatePill(backendGate, "Backend", backendOk);
  renderGatePill(ollamaGate, "Ollama", ollamaOk);
  updateBtn();
}

function renderGatePill(el, label, ok) {
  el.className = `gate-pill ${ok ? "ok" : "fail"}`;
  el.textContent = `${label}: ${ok ? "Green" : "Blocked"}`;
}

/* ── Mermaid init ────────────────────────────────────────────────── */
mermaid.initialize({ startOnLoad: false, securityLevel: "loose", theme: "default" });

/* ── Mode tabs ───────────────────────────────────────────────────── */
document.querySelectorAll(".mode-tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".mode-tab").forEach(t => {
      t.classList.remove("active");
      t.setAttribute("aria-selected", "false");
    });
    tab.classList.add("active");
    tab.setAttribute("aria-selected", "true");
    currentMode = tab.dataset.mode;
    applyMode(currentMode);
  });
});

function applyMode(mode) {
  if (mode === "paste") {
    fileArea.classList.add("hidden");
    pasteArea.classList.remove("hidden");
    dropModeLabel.textContent = "code";
  } else {
    fileArea.classList.remove("hidden");
    pasteArea.classList.add("hidden");
    dropModeLabel.textContent = mode === "folder" ? "folder" : "file";
    dropHint.textContent = mode === "folder"
      ? "Select a folder — all recognised code files will be analyzed"
      : "Supports .py .js .ts .java .cs .cpp .go and more";
    selectedFiles = [];
    renderFileList([]);
  }
  updateBtn();
}

/* ── File / Folder selection ─────────────────────────────────────── */
fileInput.addEventListener("change", () => {
  selectedFiles = Array.from(fileInput.files);
  renderFileList(selectedFiles);
  updateBtn();
});

folderInput.addEventListener("change", () => {
  selectedFiles = Array.from(folderInput.files).filter(f => isCodeFile(f.name));
  renderFileList(selectedFiles);
  updateBtn();
});

/* Browse button click — triggers the correct hidden input */
document.getElementById("browseBtn").addEventListener("click", () => {
  if (currentMode === "folder") folderInput.click();
  else fileInput.click();
});

/* Drop-zone click — same logic */
dropZone.addEventListener("click", (e) => {
  if (e.target === fileInput || e.target === folderInput) return;
  if (currentMode === "folder") folderInput.click();
  else fileInput.click();
});

/* The hidden inputs should not intercept pointer events so the
   drop-zone click handler fires reliably */
fileInput.style.pointerEvents   = "none";
folderInput.style.pointerEvents = "none";

/* ── Drag and drop ───────────────────────────────────────────────── */
dropZone.addEventListener("dragover",  (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
dropZone.addEventListener("dragleave", ()  =>  dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const dropped = Array.from(e.dataTransfer.files).filter(f => isCodeFile(f.name));
  if (dropped.length) {
    selectedFiles = dropped;
    renderFileList(selectedFiles);
    updateBtn();
  }
});

/* ── Paste area ──────────────────────────────────────────────────── */
codeInput.addEventListener("input", updateBtn);

document.getElementById("clearCodeBtn").addEventListener("click", () => {
  codeInput.value = "";
  updateBtn();
});

/* ── Helpers ─────────────────────────────────────────────────────── */
function isCodeFile(name) {
  const ext = (name.split(".").pop() || "").toLowerCase();
  const supported = new Set([
    "py", "js", "ts", "jsx", "tsx", "java", "cs", "cpp", "c", "go", "rb",
    "php", "swift", "kt", "rs", "txt", "html", "css", "scss", "json", "yaml",
    "yml", "md", "sh", "bash", "vue"
  ]);
  return supported.has(ext);
}

function formatBytes(bytes) {
  if (bytes < 1024)            return bytes + " B";
  if (bytes < 1024 * 1024)     return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function extIcon(name) {
  return (name.split(".").pop() || "?").slice(0, 3).toUpperCase();
}

function renderFileList(files) {
  fileList.innerHTML = "";
  if (!files.length) return;

  if (files.length > 1) {
    const summary = document.createElement("div");
    summary.className = "file-list-summary";
    summary.textContent = `${files.length} files selected`;
    fileList.appendChild(summary);
  }

  files.slice(0, 20).forEach(f => {
    const chip = document.createElement("div");
    chip.className = "file-chip";
    chip.innerHTML = `
      <div class="file-chip-icon">${extIcon(f.name)}</div>
      <span class="file-chip-name">${f.name}</span>
      <span class="file-chip-size">${formatBytes(f.size)}</span>`;
    fileList.appendChild(chip);
  });

  if (files.length > 20) {
    const more = document.createElement("div");
    more.className = "file-list-summary";
    more.textContent = `+ ${files.length - 20} more files`;
    fileList.appendChild(more);
  }
}

function updateBtn() {
  const hasInput = currentMode === "paste"
    ? codeInput.value.trim().length > 0
    : selectedFiles.length > 0;
  const ready = hasInput && _health.ollama && _health.backend;
  analyzeBtn.disabled = !ready;
}

/* ── Result tabs ─────────────────────────────────────────────────── */
document.querySelectorAll(".rtab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".rtab").forEach(t => t.classList.remove("active"));
    tab.classList.add("active");
    const target = tab.dataset.pane;
    Object.keys(panes).forEach(key =>
      panes[key].pane.classList.toggle("hidden", key !== target)
    );
  });
});

/* ── Analyze ─────────────────────────────────────────────────────── */
analyzeBtn.addEventListener("click", runAnalysis);

async function runAnalysis() {
  await runHealthChecks();
  if (!_health.ollama || !_health.backend) {
    setStatus("Health checks failed. Ensure backend and Ollama are both Green.", true);
    updateResultBadge("Blocked", "error");
    return;
  }

  setLoading(true);

  const form = new FormData();
  let filesToTrack = [];

  if (currentMode === "paste") {
    const code = codeInput.value.trim();
    if (!code) { setStatus("Please paste some code first.", true); setLoading(false); return; }
    form.append("code_text", code);
    initFileProgress([]);
  } else {
    if (!selectedFiles.length) { setStatus("Please select a file or folder.", true); setLoading(false); return; }
    for (const f of selectedFiles) form.append("files", f);
    filesToTrack = [...selectedFiles];
    initFileProgress(filesToTrack);
  }

  try {
    // Step 1 — Upload files, get a job_id
    setStatus("Uploading files…", false, true);
    const upRes = await fetch(UPLOAD_URL, { method: "POST", body: form });
    if (!upRes.ok) {
      const d = await upRes.json().catch(() => ({}));
      throw new Error(d.detail || "Upload failed.");
    }
    const { job_id } = await upRes.json();

    // Step 2 — Open SSE stream and consume events
    await consumeStream(job_id, filesToTrack);

  } catch (err) {
    setStatus(String(err.message || err), true);
    updateResultBadge("Error", "error");
    finishFileProgress(filesToTrack);
    setLoading(false);
  }
}

/* ── SSE stream consumer ─────────────────────────────────────────── */
async function consumeStream(jobId, files) {
  return new Promise((resolve, reject) => {
    const es = new EventSource(STREAM_URL(jobId));
    const total = files.length;

    es.onmessage = (ev) => {
      if (ev.data === "[DONE]") {
        es.close();
        setLoading(false);
        resolve();
        return;
      }

      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }

      if (msg.type === "progress") {
        const { current, total: tot, filename } = msg;
        // Live progress bar (0–80%)
        const pct = Math.round((current / tot) * 80);
        progressBar.style.width = pct + "%";
        progressBar.classList.remove("indeterminate");
        setStatus(`Checking: ${filename} [${current}/${tot}]`, false, true);

        // Update row tracker + footer
        if (current > 1) setAnalysisRowProgress(current - 2, 100);
        setAnalysisRowProgress(current - 1, 60);
        setAnalysisFooter(current - 1, tot);

      } else if (msg.type === "analyzing") {
        progressBar.style.width = "85%";
        setStatus("Running AI analysis…", false, true);
        if (total > 0) {
          setAnalysisRowProgress(total - 1, 100);
          setAnalysisFooter(total, total);
        }

      } else if (msg.type === "result") {
        progressBar.style.width = "100%";
        renderResults(msg);
        const label = total > 1 ? ` — ${total} files` : "";
        setStatus("Analysis complete" + label, false, false, true);
        updateResultBadge("Ready", "ready");
        finishFileProgress(files);
        setTimeout(() => setLoading(false), 300);
        es.close();
        resolve();

      } else if (msg.type === "error") {
        es.close();
        const isOllama = /ollama|11434|connect/i.test(msg.message || "");
        const text = isOllama
          ? "Cannot reach Ollama. Run: ollama run llama3.2:3b"
          : (msg.message || "Analysis failed.");
        setStatus(text, true);
        updateResultBadge("Error", "error");
        finishFileProgress(files);
        setLoading(false);
        reject(new Error(text));
      }
    };

    es.onerror = () => {
      es.close();
      reject(new Error("Stream connection lost."));
    };
  });
}

// Initial gate check on load
runHealthChecks().catch(() => {
  setStatus("Health check failed. Verify backend and Ollama.", true);
});

/* ── Render results ──────────────────────────────────────────────── */
function renderResults({ explanation, steps, mermaid: mermaidText }) {
  /* explanation */
  const expEl = document.getElementById("explanation");
  expEl.textContent = explanation || "No explanation returned.";
  panes.explanation.empty.classList.add("hidden");
  expEl.classList.remove("hidden");

  /* steps */
  const stepsEl = document.getElementById("steps");
  stepsEl.innerHTML = "";
  (Array.isArray(steps) ? steps : []).forEach(step => {
    const li = document.createElement("li");
    li.textContent = step;
    stepsEl.appendChild(li);
  });
  panes.steps.empty.classList.add("hidden");
  stepsEl.classList.remove("hidden");

  /* diagram */
  lastMermaidText = mermaidText || "";
  const diagramWrap = document.getElementById("diagramWrap");
  const diagramEl   = document.getElementById("diagram");
  if (lastMermaidText) {
    renderMermaid(lastMermaidText, diagramEl).then(() => {
      panes.diagram.empty.classList.add("hidden");
      diagramWrap.classList.remove("hidden");
      copyMermaidBtn.disabled = false;
    });
  }

  /* switch to explanation tab */
  document.querySelector(".rtab[data-pane='explanation']").click();
}

async function renderMermaid(raw, container) {
  const id = `mermaid-${Date.now()}`;
  // Strip markdown fences and trim
  let code = raw.trim()
    .replace(/^```(?:mermaid)?\s*/i, "")
    .replace(/\s*```$/, "")
    .trim();
  // Ensure starts with a recognised diagram type
  if (!/^(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt)/i.test(code)) {
    code = "flowchart TD\n" + code;
  }
  // Try rendering
  try {
    const { svg } = await mermaid.render(id, code);
    container.innerHTML = svg;
  } catch {
    // One auto-fix pass: collapse inline semicolons between node declarations
    try {
      const fixed = code
        .replaceAll(/;(\s+)([A-Za-z])/g, "\n$2")  // semicolons as newlines
        .replaceAll(/;\s*$/gm, "");                // trailing semicolons
      const { svg } = await mermaid.render(id + "b", fixed);
      container.innerHTML = svg;
    } catch {
      container.innerHTML = `<pre style="font:.78rem/1.55 monospace;white-space:pre-wrap;color:#475569;padding:14px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0">${code}</pre>`;
    }
  }
}

/* ── Copy Mermaid ────────────────────────────────────────────────── */
copyMermaidBtn.addEventListener("click", async () => {
  if (!lastMermaidText) return;
  try {
    await navigator.clipboard.writeText(lastMermaidText);
    setStatus("Mermaid code copied to clipboard.", false, false, true);
  } catch {
    setStatus("Clipboard copy failed.", true);
  }
});

/* ── Loading helpers ─────────────────────────────────────────────── */
function setLoading(on) {
  analyzeBtn.disabled = on;
  progressWrap.classList.toggle("hidden", !on);
  skeletonWrap.classList.toggle("hidden", !on);

  if (on) {
    progressBar.classList.add("indeterminate");
    setStatus("Analyzing with Ollama", false, true);
    Object.values(panes).forEach(({ empty, content }) => {
      if (content) content.classList.add("hidden");
      if (empty)   empty.classList.add("hidden");
    });
  } else {
    progressBar.classList.remove("indeterminate");
    updateBtn();
  }
}

function setStatus(msg, isError = false, isLoading = false, isSuccess = false) {
  statusMsg.textContent = msg;
  statusMsg.className   = "status-msg";
  if (isError)   statusMsg.classList.add("error");
  else if (isLoading)  statusMsg.classList.add("loading");
  else if (isSuccess)  statusMsg.classList.add("success");
}

function updateResultBadge(text, cls) {
  resultBadge.textContent = text;
  resultBadge.className   = `result-badge ${cls}`;
}
