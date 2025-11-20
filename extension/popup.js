const BACKEND_BASE = "http://127.0.0.1:5001";

const els = {};
const jobsState = {
  jobs: {},
  pollers: {},
  lastInfo: null
};

document.addEventListener("DOMContentLoaded", () => {
  cacheEls();
  setupEvents();
  prefillUrlFromActiveTab();
  loadBackendConfig();
  refreshQueue();
  setInterval(refreshQueue, 5000);
});

function cacheEls() {
  els.urlInput = document.getElementById("urlInput");
  els.fetchInfoBtn = document.getElementById("fetchInfoBtn");
  els.backendStatus = document.getElementById("backendStatus");

  els.videoInfoSection = document.getElementById("videoInfoSection");
  els.videoThumbnail = document.getElementById("videoThumbnail");
  els.videoTitle = document.getElementById("videoTitle");
  els.videoChannel = document.getElementById("videoChannel");
  els.videoStats = document.getElementById("videoStats");

  els.playlistSection = document.getElementById("playlistSection");
  els.playlistCount = document.getElementById("playlistCount");
  els.playlistItemsContainer = document.getElementById("playlistItemsContainer");
  els.rangeStart = document.getElementById("rangeStart");
  els.rangeEnd = document.getElementById("rangeEnd");

  els.settingsSection = document.getElementById("settingsSection");
  els.formatSelect = document.getElementById("formatSelect");
  els.customFormatRow = document.getElementById("customFormatRow");
  els.customFormatInput = document.getElementById("customFormatInput");
  els.qualitySelect = document.getElementById("qualitySelect");
  els.audioBitrateSelect = document.getElementById("audioBitrateSelect");
  els.subtitlesCheckbox = document.getElementById("subtitlesCheckbox");
  els.embedSubsCheckbox = document.getElementById("embedSubsCheckbox");
  els.subsLanguagesInput = document.getElementById("subsLanguagesInput");
  els.thumbEmbedCheckbox = document.getElementById("thumbEmbedCheckbox");
  els.outputFolderPreview = document.getElementById("outputFolderPreview");
  els.startDownloadBtn = document.getElementById("startDownloadBtn");
  els.errorArea = document.getElementById("errorArea");

  els.currentJobs = document.getElementById("currentJobs");
  els.queueJobs = document.getElementById("queueJobs");
}

function setupEvents() {
  els.fetchInfoBtn.addEventListener("click", fetchInfo);
  els.startDownloadBtn.addEventListener("click", startDownload);

  els.formatSelect.addEventListener("change", () => {
    const val = els.formatSelect.value;
    els.customFormatRow.style.display = val === "custom" ? "flex" : "none";
  });

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "YTDL_SET_URL" && message.url) {
      els.urlInput.value = message.url;
      sendResponse && sendResponse({ ok: true });
    }
  });
}

function prefillUrlFromActiveTab() {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    const tab = tabs[0];
    if (!tab || !tab.url) return;
    const url = tab.url;
    if (url.includes("youtube.com") || url.includes("youtu.be")) {
      els.urlInput.value = url;
    }
  });
}

async function loadBackendConfig() {
  try {
    const res = await fetch(`${BACKEND_BASE}/api/config`);
    if (!res.ok) throw new Error("Failed");
    const cfg = await res.json();
    els.backendStatus.textContent = `Backend connected. Download dir: ${cfg.download_dir}`;
    els.outputFolderPreview.textContent = cfg.download_dir;
    els.thumbEmbedCheckbox.checked = !!cfg.embed_thumbnail_default;
  } catch (e) {
    els.backendStatus.textContent = "Backend not reachable at http://127.0.0.1:5001. Start server.py.";
  }
}

async function fetchInfo() {
  const url = els.urlInput.value.trim();
  els.errorArea.textContent = "";
  if (!url) {
    els.errorArea.textContent = "Paste a YouTube URL first.";
    return;
  }

  els.fetchInfoBtn.disabled = true;
  els.fetchInfoBtn.textContent = "Fetching…";

  try {
    const res = await fetch(`${BACKEND_BASE}/api/info`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "Failed to fetch info");
    }
    jobsState.lastInfo = data;
    renderInfo(data);
    els.settingsSection.classList.remove("hidden");
  } catch (e) {
    els.errorArea.textContent = e.message;
  } finally {
    els.fetchInfoBtn.disabled = false;
    els.fetchInfoBtn.textContent = "Fetch Info";
  }
}

function renderInfo(info) {
  els.videoInfoSection.classList.remove("hidden");
  els.videoThumbnail.src = info.thumbnail || "";
  els.videoTitle.textContent = info.title || "(no title)";
  els.videoChannel.textContent = info.channel || "";
  const duration = info.duration_text || "";
  const views = info.view_count_text ? `${info.view_count_text} views` : "";
  els.videoStats.textContent = [duration, views].filter(Boolean).join(" • ");

  if (info.is_playlist && Array.isArray(info.entries)) {
    els.playlistSection.classList.remove("hidden");
    els.playlistCount.textContent = `Playlist items: ${info.entries.length}`;
    els.playlistItemsContainer.innerHTML = "";

    info.entries.forEach((entry) => {
      const row = document.createElement("label");
      row.className = "playlist-item";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "playlist-item-checkbox";
      checkbox.dataset.index = entry.index;

      const img = document.createElement("img");
      img.src = entry.thumbnail || "";
      const title = document.createElement("div");
      title.className = "playlist-item-title";
      title.textContent = `${entry.index}. ${entry.title || "(no title)"}`;

      row.appendChild(checkbox);
      row.appendChild(img);
      row.appendChild(title);
      els.playlistItemsContainer.appendChild(row);
    });
  } else {
    els.playlistSection.classList.add("hidden");
  }
}

function getPlaylistOptions() {
  if (!jobsState.lastInfo || !jobsState.lastInfo.is_playlist) {
    return { mode: "auto" };
  }
  const modeEl = document.querySelector('input[name="playlistMode"]:checked');
  const mode = modeEl ? modeEl.value : "all";

  if (mode === "all") {
    return { mode: "all" };
  }
  if (mode === "range") {
    const start = parseInt(els.rangeStart.value, 10);
    const end = parseInt(els.rangeEnd.value, 10);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start <= 0 || end < start) {
      return { mode: "all" };
    }
    return { mode: "range", range_start: start, range_end: end };
  }
  if (mode === "selection") {
    const checked = Array.from(
      els.playlistItemsContainer.querySelectorAll(".playlist-item-checkbox:checked")
    );
    const items = checked.map((c) => parseInt(c.dataset.index, 10)).filter((n) => Number.isFinite(n));
    if (!items.length) {
      return { mode: "all" };
    }
    return { mode: "selection", items };
  }
  return { mode: "auto" };
}

async function startDownload() {
  const url = els.urlInput.value.trim();
  if (!url) {
    els.errorArea.textContent = "Paste a YouTube URL first.";
    return;
  }

  const format = els.formatSelect.value;
  const quality = els.qualitySelect.value;
  let audioBitrate = els.audioBitrateSelect.value;
  if (audioBitrate === "best") {
    audioBitrate = "0";
  }

  const subsEnabled = els.subtitlesCheckbox.checked;
  const embedSubs = els.embedSubsCheckbox.checked;
  const subsLanguages = els.subsLanguagesInput.value.trim();

  const embedThumb = els.thumbEmbedCheckbox.checked;
  const playlistOptions = getPlaylistOptions();

  const payload = {
    url,
    format,
    quality,
    audio_bitrate: audioBitrate,
    subtitles: {
      enabled: subsEnabled,
      embed: embedSubs,
      languages: subsLanguages
    },
    embed_thumbnail: embedThumb,
    playlist: playlistOptions
  };

  if (format === "custom") {
    const cf = els.customFormatInput.value.trim();
    if (!cf) {
      els.errorArea.textContent = "Custom format cannot be empty.";
      return;
    }
    payload.custom_format = cf;
  }

  els.errorArea.textContent = "";
  els.startDownloadBtn.disabled = true;
  els.startDownloadBtn.textContent = "Queuing…";

  try {
    const res = await fetch(`${BACKEND_BASE}/api/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || "Failed to queue job");
    }
    const jobId = data.job_id;
    addJobToUI(jobId, { title: jobsState.lastInfo?.title || url });
    startPollingJob(jobId);
  } catch (e) {
    els.errorArea.textContent = e.message;
  } finally {
    els.startDownloadBtn.disabled = false;
    els.startDownloadBtn.textContent = "Start Download";
  }
}

function addJobToUI(jobId, info) {
  if (!jobsState.jobs[jobId]) {
    jobsState.jobs[jobId] = {
      id: jobId,
      state: "QUEUED",
      progress: 0,
      title: info.title || "(untitled)"
    };
  }
  renderJobs();
}

async function refreshQueue() {
  try {
    const res = await fetch(`${BACKEND_BASE}/api/queue`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Queue error");
    const list = data.jobs || [];

    list.forEach((job) => {
      jobsState.jobs[job.id] = job;
      if (["QUEUED", "DOWNLOADING", "MERGING", "EMBEDDING_THUMBNAIL", "CONVERTING"].includes(job.state)) {
        if (!jobsState.pollers[job.id]) {
          startPollingJob(job.id);
        }
      }
    });
    renderJobs();
  } catch (e) {
    // ignore
  }
}

function startPollingJob(jobId) {
  if (jobsState.pollers[jobId]) return;

  const poll = async () => {
    try {
      const res = await fetch(`${BACKEND_BASE}/api/status/${jobId}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "status error");
      jobsState.jobs[jobId] = data;
      renderJobs();

      if (["COMPLETED", "ERROR", "CANCELLED"].includes(data.state)) {
        clearInterval(jobsState.pollers[jobId]);
        delete jobsState.pollers[jobId];
        if (data.state === "COMPLETED") {
          chrome.notifications.create(`ytdl-${jobId}`, {
            type: "basic",
            iconUrl: "icons/icon48.png",
            title: "Download complete",
            message: data.output_files && data.output_files.length
              ? data.output_files[0]
              : "Download finished."
          });
        }
      }
    } catch (e) {
      clearInterval(jobsState.pollers[jobId]);
      delete jobsState.pollers[jobId];
    }
  };

  jobsState.pollers[jobId] = setInterval(poll, 1000);
  poll();
}

function renderJobs() {
  els.currentJobs.innerHTML = "";
  els.queueJobs.innerHTML = "";

  const jobsArr = Object.values(jobsState.jobs);
  jobsArr.sort((a, b) => (a.created_at || 0) - (b.created_at || 0));

  jobsArr.forEach((job) => {
    const card = document.createElement("div");
    card.className = "job-card";

    const header = document.createElement("div");
    header.className = "job-header";

    const title = document.createElement("div");
    title.className = "job-title";
    title.textContent = job.url || job.id;

    const status = document.createElement("div");
    status.className = "job-status";
    status.textContent = job.state || "UNKNOWN";
    if (job.state === "COMPLETED") status.classList.add("completed");
    if (job.state === "ERROR") status.classList.add("error");

    header.appendChild(title);
    header.appendChild(status);

    const progressBar = document.createElement("div");
    progressBar.className = "progress-bar";
    const fill = document.createElement("div");
    fill.className = "progress-fill";
    const pct = typeof job.progress === "number" ? job.progress : 0;
    fill.style.width = `${Math.min(100, Math.max(0, pct))}%`;
    progressBar.appendChild(fill);

    const footer = document.createElement("div");
    footer.className = "job-footer";

    const left = document.createElement("div");
    const etaText = job.eta ? `ETA: ${job.eta}s` : "";
    const speedText = job.speed ? `${(job.speed / 1024 / 1024).toFixed(2)} MB/s` : "";
    const progressText = `${pct.toFixed ? pct.toFixed(0) : pct}%`;
    left.textContent = [progressText, speedText, etaText].filter(Boolean).join(" • ");

    const actions = document.createElement("div");
    actions.className = "job-actions";

    if (["DOWNLOADING", "QUEUED"].includes(job.state)) {
      const cancelBtn = document.createElement("button");
      cancelBtn.textContent = "Cancel";
      cancelBtn.addEventListener("click", () => cancelJob(job.id));
      actions.appendChild(cancelBtn);
    }

    if (job.state === "COMPLETED" && job.output_files && job.output_files.length) {
      const openBtn = document.createElement("button");
      openBtn.textContent = "Copy path";
      openBtn.addEventListener("click", () => {
        navigator.clipboard.writeText(job.output_files[0]).catch(() => {});
      });
      actions.appendChild(openBtn);
    }

    if (job.state === "ERROR" && job.error) {
      left.textContent += left.textContent ? ` – ${job.error}` : job.error;
    }

    footer.appendChild(left);
    footer.appendChild(actions);

    card.appendChild(header);
    card.appendChild(progressBar);
    card.appendChild(footer);

    if (["DOWNLOADING", "MERGING", "EMBEDDING_THUMBNAIL", "CONVERTING"].includes(job.state)) {
      els.currentJobs.appendChild(card);
    } else {
      els.queueJobs.appendChild(card);
    }
  });
}

async function cancelJob(jobId) {
  try {
    await fetch(`${BACKEND_BASE}/api/cancel/${jobId}`, {
      method: "POST"
    });
  } catch (e) {}
}
