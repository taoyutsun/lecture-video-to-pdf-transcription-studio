const state = {
  csrfToken: "",
  profiles: [],
  activeProfile: null,
  secureStorageAvailable: false,
  profileBusy: false,
  activeTab: "slides",
  activeJobId: null,
  queuedFiles: [],
  jobs: new Map(),
  slides: [],
  pollTimer: null,
  fasterWhisperInstall: null,
  cudaRuntimeInstall: null,
  cudaDiagnostics: null,
  installPollTimer: null,
  cudaInstallPollTimer: null,
  media: {
    videoExtensions: [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v"],
    audioExtensions: [".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"],
  },
};

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (!["GET", "HEAD"].includes((options.method || "GET").toUpperCase())) {
    headers.set("X-Studio-Token", state.csrfToken);
  }
  return window.fetch(url, { ...options, headers, credentials: "same-origin", cache: "no-store" });
}

async function requestJson(url, body) {
  const response = await apiFetch(url, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`);
  return data;
}

async function profileAction(action) {
  if (state.profileBusy) throw new Error("服務設定處理中，請稍候。");
  state.profileBusy = true;
  const ids = ["endpointProfile", "profileName", "endpointBaseUrl", "endpointModel", "customModel", "apiKey", "saveApiKey", "saveProfileBtn", "deleteKeyBtn", "testEndpointBtn", "asrLanguage", "responseFormat", "endpointUploadStrategy", "endpointMaxChunkMb", "endpointChunkMinutes"];
  const disabled = ids.map((id) => $(id).disabled);
  ids.forEach((id) => { $(id).disabled = true; });
  try { return await action(); }
  finally {
    ids.forEach((id, index) => { $(id).disabled = disabled[index]; });
    state.profileBusy = false;
    $("endpointModel").disabled = Boolean(state.activeProfile?.server_managed_model || state.activeProfile?.provider === "qwen");
    updateKeyStatus();
    updateUploadStrategy();
  }
}

function endpointModel() {
  return $("endpointModel").value === "__custom" ? $("customModel").value.trim() : $("endpointModel").value;
}

function renderProfiles(selected) {
  $("endpointProfile").replaceChildren(...state.profiles.map((profile) => new Option(profile.name, profile.id)), new Option("新增自訂端點", "__new"));
  $("endpointProfile").value = selected || "qwen";
}

function renderEndpointModels(profile, current = profile.model) {
  const names = [...new Set(profile.models || [])];
  if (current && !names.includes(current)) names.push(current);
  $("endpointModel").replaceChildren(...names.map((model) => new Option(model === "default" ? "default（由端點配置）" : model, model)));
  if (profile.provider === "custom" && !profile.server_managed_model) $("endpointModel").add(new Option("手動輸入模型 ID", "__custom"));
  $("endpointModel").value = current || names[0] || "__custom";
  $("endpointModel").disabled = profile.provider === "qwen" || profile.server_managed_model;
  setVisible("customModelLabel", $("endpointModel").value === "__custom");
  renderModelStatus();
}

function renderModelStatus() {
  const profile = state.activeProfile;
  $("modelStatus").textContent = profile.models_checked_at
    ? `模型清單更新：${new Date(profile.models_checked_at).toLocaleString()}`
    : (profile.provider === "qwen" ? "使用服務端已載入模型" : "預設模型清單，尚未測試此帳號連線");
}

function updateKeyStatus() {
  const profile = state.activeProfile;
  const bound = profile?.base_url === $("endpointBaseUrl").value.trim().replace(/\/+$/, "");
  const hasKey = Boolean(bound && profile?.has_key);
  $("apiKey").placeholder = hasKey ? "已安全保存；留空即可使用" : "僅本次使用，除非勾選保存";
  $("keyStatus").textContent = !state.secureStorageAvailable ? "此環境不支援安全保存，金鑰僅本次使用"
    : (!bound && profile?.has_key ? "網址已變更，原金鑰不會用於新端點" : profile?.key_status || "未保存金鑰");
  $("deleteKeyBtn").disabled = !hasKey;
  $("saveApiKey").disabled = !state.secureStorageAvailable;
}

function applyProfile(profileId) {
  const profile = state.profiles.find((item) => item.id === profileId) || {
    id: null, provider: "custom", name: "自訂端點", base_url: "", model: "", models: [],
    language: "", response_format: "srt", upload_strategy: "auto", max_chunk_mb: 20, chunk_minutes: 10,
  };
  state.activeProfile = profile;
  $("apiKey").value = "";
  $("saveApiKey").checked = false;
  $("customModel").value = "";
  $("endpointStatus").textContent = "";
  $("profileName").value = profile.name;
  $("endpointBaseUrl").value = profile.base_url;
  $("endpointBaseUrl").readOnly = profile.provider !== "custom";
  setVisible("profileNameLabel", profile.provider === "custom");
  $("asrLanguage").value = profile.language || "";
  $("responseFormat").value = profile.response_format;
  $("endpointUploadStrategy").value = profile.upload_strategy;
  $("endpointMaxChunkMb").value = String(profile.max_chunk_mb);
  $("endpointChunkMinutes").value = String(profile.chunk_minutes);
  renderEndpointModels(profile);
  setResponseFormatOptions();
  updateKeyStatus();
  updateUploadStrategy();
}

async function saveProfile(explicit = false) {
  const profile = state.activeProfile;
  const saveKey = explicit && $("saveApiKey").checked;
  const data = await requestJson("/api/asr/profiles", {
    id: profile.id, provider: profile.provider, name: $("profileName").value,
    base_url: $("endpointBaseUrl").value.trim(), model: endpointModel(), language: getSelectedLanguage(),
    response_format: $("responseFormat").value, upload_strategy: $("endpointUploadStrategy").value,
    max_chunk_mb: Number($("endpointMaxChunkMb").value), chunk_minutes: Number($("endpointChunkMinutes").value),
    api_key: saveKey ? $("apiKey").value : "", save_key: saveKey,
  });
  state.activeProfile = data;
  $("endpointBaseUrl").value = data.base_url;
  const pos = state.profiles.findIndex((item) => item.id === data.id);
  if (pos < 0) state.profiles.push(data); else state.profiles[pos] = data;
  renderProfiles(data.id);
  if (saveKey) $("apiKey").value = "";
  $("saveApiKey").checked = false;
  updateKeyStatus();
  return data;
}

function updateUploadStrategy() {
  const direct = $("endpointUploadStrategy").value === "direct";
  $("endpointMaxChunkMb").disabled = direct;
  $("endpointChunkMinutes").disabled = direct;
}

function fileName(path) {
  return String(path || "").split(/[\\/]/).pop();
}

function setProgress(value, text) {
  $("progressBar").style.width = `${Math.round(value * 100)}%`;
  $("progressText").textContent = text || "";
}

function setVisible(id, visible) {
  $(id).classList.toggle("hidden", !visible);
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function cudaRuntimeInstalled(status = state.cudaRuntimeInstall) {
  const details = status?.details || state.cudaDiagnostics || {};
  return Boolean(status?.installed || details.runtime_ready);
}

function cudaRuntimeUnavailable(status = state.cudaRuntimeInstall) {
  return status?.status === "unavailable";
}

function currentTask() {
  return state.activeTab === "transcription" ? "transcription" : $("taskMode").value;
}

function taskNeedsAsr() {
  return currentTask() !== "slides";
}

function activeAcceptList() {
  const videos = state.media.videoExtensions.join(",");
  if (state.activeTab === "transcription") {
    return [videos, state.media.audioExtensions.join(","), "video/*", "audio/*"].join(",");
  }
  return [videos, "video/*"].join(",");
}

function ensureAsrEngineDefault() {
  if (taskNeedsAsr() && $("asrEngine").value === "none") {
    $("asrEngine").value = "faster-whisper";
  }
}

function updateModeUi() {
  const transcriptionTab = state.activeTab === "transcription";
  $("slidesTab").classList.toggle("active", !transcriptionTab);
  $("transcriptionTab").classList.toggle("active", transcriptionTab);
  $("settingsTitle").textContent = transcriptionTab ? "轉錄設定" : "轉換設定";
  $("sourceLabel").textContent = transcriptionTab ? "媒體路徑（支援影片或音檔）" : "影片路徑（大檔案建議用此方式）";
  $("videoPath").placeholder = transcriptionTab ? "C:\\path\\lecture.mp4 或 C:\\path\\audio.mp3" : "C:\\path\\lecture.mp4";
  $("dropTitle").textContent = transcriptionTab ? "選取或拖曳影片/音檔" : "選取或拖曳影片";
  $("dropHint").textContent = transcriptionTab
    ? "可多選；支援常見影片與音訊格式，檔案會先複製到 uploads 資料夾"
    : "可多選；檔案會先複製到本機 uploads 資料夾再處理";
  $("filePicker").setAttribute("accept", activeAcceptList());
  setVisible("pdfOptions", !transcriptionTab);

  const needsAsr = taskNeedsAsr();
  setVisible("asrSection", needsAsr);
  if (needsAsr) ensureAsrEngineDefault();
  updateAsrVisibility();
  $("startBtn").textContent = transcriptionTab ? "開始語音轉文字" : (needsAsr ? "開始擷取 PDF 與轉錄" : "開始擷取 PDF");
}

function updateAsrVisibility() {
  const needsAsr = taskNeedsAsr();
  const engine = needsAsr ? $("asrEngine").value : "none";
  setVisible("asrCommonOptions", needsAsr && engine !== "none");
  setVisible("fasterWhisperOptions", needsAsr && engine === "faster-whisper");
  setVisible("endpointOptions", needsAsr && engine === "openai-compatible");
  setResponseFormatOptions();
  if (needsAsr && engine === "faster-whisper") {
    refreshFasterWhisperInstallStatus().catch((err) => {
      $("fasterWhisperInstallStatus").textContent = err.message;
    });
    refreshCudaRuntimeInstallStatus().catch((err) => {
      $("cudaRuntimeInstallStatus").textContent = err.message;
    });
  }
}

function renderFileQueue() {
  $("fileQueue").innerHTML = state.queuedFiles
    .map((file, idx) => `
      <div class="file-chip">
        <span title="${escapeHtml(file.path)}">${idx + 1}. ${escapeHtml(file.name)}</span>
        <button type="button" data-action="remove-file" data-pos="${idx}">移除</button>
      </div>
    `)
    .join("");
}

function renderBatchJobs() {
  const jobs = [...state.jobs.values()];
  $("batchJobs").innerHTML = jobs
    .map((job, idx) => {
      const name = fileName(job.media_path || job.video_path || "");
      const status = job.error ? `失敗：${job.error}` : `${job.status} ${Math.round((job.progress || 0) * 100)}%`;
      return `
        <div class="job-row" data-job-id="${job.id}">
          <span title="${escapeHtml(job.media_path || job.video_path)}">${idx + 1}. ${escapeHtml(name)}</span>
          <small>${escapeHtml(status)}</small>
        </div>
      `;
    })
    .join("");
}

function renderArtifacts(result) {
  if (!result) return;
  const rows = [
    ["PDF", result.pdf_path],
    ["metadata", result.metadata_path],
    ["投影片圖片資料夾", result.slides_dir],
    ["逐字稿 TXT", result.transcript_path],
    ["字幕 SRT", result.transcript_srt_path],
    ["雲端分段紀錄", result.transcription_manifest_path],
    ["投影片逐字稿對照", result.slide_map_path],
  ].filter(([, value]) => value);
  $("artifactPaths").innerHTML = rows
    .map(([label, value]) => `<div><strong>${label}</strong>: ${escapeHtml(value)}</div>`)
    .join("");
}

function renderThumbs(slides) {
  state.slides = (slides || []).map((slide) => ({ ...slide }));
  const hasSlides = state.slides.length > 0;
  setVisible("reviewSection", hasSlides);
  $("applyReviewBtn").disabled = !hasSlides || !state.activeJobId;
  if (!hasSlides) {
    $("thumbGrid").innerHTML = "";
    return;
  }
  $("thumbGrid").innerHTML = state.slides
    .map((slide, idx) => {
      const removed = !slide.kept;
      const off = removed ? " off" : "";
      const thumb = `/api/jobs/${state.activeJobId}/thumbs/${encodeURIComponent(fileName(slide.thumb_path))}`;
      return `
        <div class="thumb-item${off}" data-index="${slide.index}">
          <img src="${thumb}" alt="slide ${idx + 1}">
          <div class="thumb-toolbar">
            <span>${idx + 1}. ${Number(slide.time_seconds).toFixed(2)}s</span>
            <button type="button" data-action="up" data-pos="${idx}">上移</button>
            <button type="button" data-action="down" data-pos="${idx}">下移</button>
            <button type="button" class="toggle${off}" data-action="toggle" data-pos="${idx}">
              ${removed ? "保留" : "刪除"}
            </button>
          </div>
        </div>`;
    })
    .join("");
}

function getSelectedLanguage() {
  return $("asrLanguage").value || null;
}

function setResponseFormatOptions() {
  const current = $("responseFormat").value;
  let host = "";
  try { host = new URL($("endpointBaseUrl").value).hostname; } catch { /* Incomplete custom URL. */ }
  const textOnly = $("asrEngine").value === "openai-compatible"
    && host === "api.openai.com"
    && ["gpt-4o-transcribe", "gpt-4o-mini-transcribe"].includes(endpointModel());
  const options = textOnly ? ["text", "json"] : ["srt", "verbose_json", "json", "text"];
  $("responseFormat").innerHTML = options.map((format) => `<option value="${format}">${format}</option>`).join("");
  $("responseFormat").value = options.includes(current) ? current : options[0];
  if (textOnly) $("modelStatus").textContent = "此模型不提供字幕時間戳，僅輸出逐字稿 TXT";
}

function renderFasterWhisperInstallStatus(data) {
  state.fasterWhisperInstall = data || {
    installed: false,
    running: false,
    status: "idle",
    message: "faster-whisper 尚未安裝。",
    log: [],
  };
  const status = state.fasterWhisperInstall;
  const installed = Boolean(status.installed);
  const running = Boolean(status.running);
  const consent = $("autoInstallFasterWhisper").checked;
  const cuda = state.cudaDiagnostics || {};
  const cudaNote = cuda.gpu_available
    ? (cuda.runtime_ready
      ? "CUDA GPU 與 CUDA 12 runtime 可使用。"
      : `偵測到 CUDA GPU，但 CUDA runtime 尚未完整可載入：${(cuda.missing_runtime_dlls || []).join(", ") || "未知"}`)
    : "未偵測到 CUDA GPU，會使用 CPU。";
  const note = installed
    ? `已安裝，可直接使用 faster-whisper。${cudaNote}`
    : `${status.message || "尚未安裝 faster-whisper。"} 可勾選確認後立即安裝，或在開始轉換前自動安裝。${cudaNote}`;

  $("fasterWhisperInstallStatus").textContent = running ? `${status.message} 這可能需要幾分鐘。` : note;
  $("installFasterWhisperBtn").disabled = installed || running || !consent;
  $("installFasterWhisperBtn").textContent = running ? "安裝中..." : installed ? "已安裝" : "立即安裝 ASR 依賴";

  const log = Array.isArray(status.log) ? status.log.join("\n") : "";
  $("fasterWhisperInstallLog").textContent = log;
  $("fasterWhisperInstallLog").classList.toggle("hidden", !log && !running && status.status !== "failed");

  if (state.installPollTimer) {
    clearTimeout(state.installPollTimer);
    state.installPollTimer = null;
  }
  if (running) {
    state.installPollTimer = setTimeout(() => refreshFasterWhisperInstallStatus().catch(() => {}), 1400);
  }
}

function renderCudaRuntimeInstallStatus(data) {
  state.cudaRuntimeInstall = data || {
    installed: false,
    running: false,
    status: "idle",
    message: "CUDA runtime 尚未檢查。",
    details: state.cudaDiagnostics || {},
    log: [],
  };
  const status = state.cudaRuntimeInstall;
  const details = status.details || state.cudaDiagnostics || {};
  state.cudaDiagnostics = details;

  const installed = cudaRuntimeInstalled(status);
  const unavailable = cudaRuntimeUnavailable(status);
  const running = Boolean(status.running);
  const hasGpu = Boolean(details.gpu_available);
  const consent = $("autoInstallCudaRuntime").checked;
  const missing = (details.missing_runtime_dlls || []).join(", ");

  let note = "";
  if (!hasGpu) {
    note = "未偵測到可用的 NVIDIA/CUDA GPU，不需要安裝 CUDA runtime。";
  } else if (installed) {
    note = `CUDA GPU 可使用：${details.torch_device || "NVIDIA GPU"}。`;
  } else if (unavailable) {
    note = `${status.message || "目前版本無法在執行時安裝 CUDA runtime。"} 裝置使用 auto 時會改用 CPU/int8。`;
  } else {
    note = `偵測到 CUDA GPU，但 CUDA 12 runtime 尚未完整可載入：${missing || "未知"}。可安裝 GPU 加速依賴，套件可能超過 1GB。`;
  }

  $("cudaRuntimeInstallStatus").textContent = running ? `${status.message} 這可能需要幾分鐘。` : note;
  $("installCudaRuntimeBtn").disabled = !hasGpu || installed || unavailable || running || !consent;
  $("installCudaRuntimeBtn").textContent = running ? "安裝中..." : installed ? "CUDA 可使用" : "立即安裝 CUDA GPU 依賴";

  const log = Array.isArray(status.log) ? status.log.join("\n") : "";
  $("cudaRuntimeInstallLog").textContent = log;
  $("cudaRuntimeInstallLog").classList.toggle("hidden", !log && !running && status.status !== "failed");

  if (state.cudaInstallPollTimer) {
    clearTimeout(state.cudaInstallPollTimer);
    state.cudaInstallPollTimer = null;
  }
  if (running) {
    state.cudaInstallPollTimer = setTimeout(() => refreshCudaRuntimeInstallStatus().catch(() => {}), 1500);
  }
}

async function refreshFasterWhisperInstallStatus() {
  const response = await apiFetch("/api/asr/faster-whisper/install");
  if (!response.ok) throw new Error(response.statusText);
  const data = await response.json();
  renderFasterWhisperInstallStatus(data);
  return data;
}

async function refreshCudaRuntimeInstallStatus() {
  const response = await apiFetch("/api/asr/cuda-runtime/install");
  if (!response.ok) throw new Error(response.statusText);
  const data = await response.json();
  renderCudaRuntimeInstallStatus(data);
  return data;
}

async function startFasterWhisperInstall() {
  const response = await apiFetch("/api/asr/faster-whisper/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: $("autoInstallFasterWhisper").checked }),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || response.statusText);
  }
  const data = await response.json();
  renderFasterWhisperInstallStatus(data);
  return data;
}

async function startCudaRuntimeInstall() {
  const response = await apiFetch("/api/asr/cuda-runtime/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: $("autoInstallCudaRuntime").checked }),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || response.statusText);
  }
  const data = await response.json();
  renderCudaRuntimeInstallStatus(data);
  return data;
}

async function waitForFasterWhisperInstall() {
  let status = await startFasterWhisperInstall();
  while (status.running) {
    setProgress(0.05, "正在安裝 faster-whisper ASR 依賴");
    await delay(1500);
    status = await refreshFasterWhisperInstallStatus();
  }
  if (!status.installed) {
    throw new Error(status.message || "faster-whisper 依賴安裝失敗。");
  }
  $("envStatus").textContent = "ASR 依賴已安裝";
  return status;
}

async function waitForCudaRuntimeInstall() {
  let status = await startCudaRuntimeInstall();
  while (status.running) {
    setProgress(0.05, "正在安裝 CUDA GPU 加速依賴");
    await delay(1500);
    status = await refreshCudaRuntimeInstallStatus();
  }
  if (!status.installed) {
    throw new Error(status.message || "CUDA GPU 加速依賴安裝失敗。");
  }
  $("envStatus").textContent = "CUDA GPU 可使用";
  return status;
}

async function ensureCudaRuntimeReady() {
  if (!taskNeedsAsr() || $("asrEngine").value !== "faster-whisper") return;
  const selectedDevice = $("asrDevice").value;
  if (selectedDevice === "cpu") return;

  const status = state.cudaRuntimeInstall || await refreshCudaRuntimeInstallStatus();
  const details = status.details || state.cudaDiagnostics || {};
  const installed = cudaRuntimeInstalled(status);
  if (!details.gpu_available || installed) return;

  if (selectedDevice === "auto") {
    if (cudaRuntimeUnavailable(status)) {
      setProgress(0.03, "CUDA runtime 不完整，auto 模式將使用 CPU/int8");
      return;
    }
    if ($("autoInstallCudaRuntime").checked) {
      await waitForCudaRuntimeInstall();
    }
    return;
  }

  if ($("autoInstallCudaRuntime").checked && !cudaRuntimeUnavailable(status)) {
    await waitForCudaRuntimeInstall();
  }

  const refreshed = state.cudaRuntimeInstall || status;
  if (!cudaRuntimeInstalled(refreshed)) {
    throw new Error("你選擇了 cuda，但 CUDA runtime 尚未完整可載入。請改選 auto/cpu，或先提供可載入的 CUDA 12 runtime。");
  }
}

async function ensureFasterWhisperReady() {
  if (!taskNeedsAsr() || $("asrEngine").value !== "faster-whisper") return;
  const status = state.fasterWhisperInstall || await refreshFasterWhisperInstallStatus();
  if (status.installed) return;
  if (!$("autoInstallFasterWhisper").checked) {
    throw new Error("faster-whisper 尚未安裝。請勾選允許自動安裝，或按「立即安裝 ASR 依賴」。");
  }
  await waitForFasterWhisperInstall();
}

function collectRequest(videoPath) {
  const task = currentTask();
  const needsAsr = task !== "slides";
  const engine = needsAsr ? $("asrEngine").value : "none";
  const model = engine === "openai-compatible"
    ? endpointModel()
    : ($("asrModel").value || "base");
  const selectedDevice = $("asrDevice").value;
  const details = state.cudaRuntimeInstall?.details || state.cudaDiagnostics || {};
  const device = engine === "faster-whisper"
    && selectedDevice === "auto"
    && details.gpu_available
    && !cudaRuntimeInstalled()
      ? "cpu"
      : selectedDevice;
  return {
    video_path: videoPath,
    output_dir: $("outputDir").value.trim() || null,
    task,
    mode: $("mode").value,
    crop: $("crop").value.trim() || "auto",
    asr: {
      engine,
      model,
      language: getSelectedLanguage(),
      device,
      compute_type: $("computeType").value,
      endpoint_base_url: $("endpointBaseUrl").value.trim(),
      api_key: $("apiKey").value,
      profile_id: engine === "openai-compatible" ? state.activeProfile?.id : null,
      use_saved_key: engine === "openai-compatible" && Boolean(state.activeProfile?.has_key) && !$("apiKey").value,
      response_format: $("responseFormat").value,
      endpoint_upload_strategy: $("endpointUploadStrategy").value,
      endpoint_max_chunk_mb: Number($("endpointMaxChunkMb").value),
      endpoint_chunk_minutes: Number($("endpointChunkMinutes").value),
    },
  };
}

function targetsFromUi() {
  const targets = state.queuedFiles.map((file) => file.path);
  const manual = $("videoPath").value.trim();
  if (manual) targets.unshift(manual);
  return [...new Set(targets)];
}

async function createJob(mediaPath, template) {
  const response = await apiFetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...template, video_path: mediaPath }),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || response.statusText);
  }
  const job = await response.json();
  state.jobs.set(job.id, job);
  if (!state.activeJobId) state.activeJobId = job.id;
  $("jobId").textContent = job.id;
  return job;
}

async function startJobs() {
  const targets = targetsFromUi();
  if (!targets.length) {
    throw new Error("請輸入媒體路徑，或選取/拖曳檔案。");
  }
  if (taskNeedsAsr() && $("asrEngine").value === "none") {
    throw new Error("語音轉文字需要選擇 ASR 引擎。");
  }

  $("startBtn").disabled = true;
  await ensureFasterWhisperReady();
  await ensureCudaRuntimeReady();
  if (taskNeedsAsr() && $("asrEngine").value === "openai-compatible") {
    await profileAction(() => saveProfile(false));
  }
  $("artifactPaths").innerHTML = "";
  $("thumbGrid").innerHTML = "";
  setVisible("reviewSection", false);
  state.jobs.clear();
  state.activeJobId = null;
  setProgress(0, `建立 ${targets.length} 個工作`);
  const template = collectRequest(targets[0]);
  for (const target of targets) {
    await createJob(target, template);
  }
  renderBatchJobs();
  pollJobs();
}

async function pollJobs() {
  const ids = [...state.jobs.keys()];
  if (!ids.length) return;
  const updates = await Promise.all(ids.map(async (id) => {
    const response = await apiFetch(`/api/jobs/${id}`);
    return response.json();
  }));
  for (const job of updates) state.jobs.set(job.id, job);
  renderBatchJobs();

  const completed = updates.filter((job) => job.status === "completed");
  const failed = updates.filter((job) => job.status === "failed");
  const running = updates.find((job) => job.status === "running") || updates.find((job) => job.status === "queued");
  const average = updates.reduce((sum, job) => sum + (job.progress || 0), 0) / updates.length;
  const label = running ? `${fileName(running.media_path || running.video_path)}：${running.message}` : `${completed.length} 完成，${failed.length} 失敗`;
  setProgress(average, label);

  const active = state.jobs.get(state.activeJobId);
  const displayJob = active?.result ? active : completed[completed.length - 1];
  if (displayJob?.result) {
    state.activeJobId = displayJob.id;
    $("jobId").textContent = displayJob.id;
    renderArtifacts(displayJob.result);
    renderThumbs(displayJob.result.slides || []);
    if (displayJob.result.warnings && displayJob.result.warnings.length) {
      $("progressText").textContent = `${displayJob.message}: ${displayJob.result.warnings.join(" / ")}`;
    }
  }

  if (completed.length + failed.length === updates.length) {
    $("startBtn").disabled = false;
    return;
  }
  state.pollTimer = setTimeout(pollJobs, 900);
}

async function applyReview() {
  if (!state.activeJobId || !state.slides.length) return;
  const kept = {};
  const order = state.slides.map((slide) => slide.index);
  for (const slide of state.slides) kept[String(slide.index)] = Boolean(slide.kept);
  const response = await apiFetch(`/api/jobs/${state.activeJobId}/slides/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order, kept }),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || response.statusText);
  }
  const data = await response.json();
  $("artifactPaths").insertAdjacentHTML("afterbegin", `<div><strong>reviewed PDF</strong>: ${escapeHtml(data.pdf_path)}</div>`);
  renderThumbs(data.slides || []);
}

async function uploadFiles(files) {
  const list = [...files].filter(Boolean);
  if (!list.length) return;
  $("progressText").textContent = `正在上傳 ${list.length} 個檔案到 uploads 資料夾`;
  const form = new FormData();
  for (const file of list) form.append("files", file);
  const response = await apiFetch("/api/uploads", { method: "POST", body: form });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || response.statusText);
  }
  const data = await response.json();
  for (const file of data.files || []) {
    state.queuedFiles.push({ name: file.name, path: file.path });
  }
  renderFileQueue();
  $("progressText").textContent = `已加入 ${state.queuedFiles.length} 個待處理檔案`;
}

async function testEndpoint() {
  setResponseFormatOptions();
  await saveProfile(false);
  const selectedId = state.activeProfile.id;
  const selectedModel = endpointModel();
  $("endpointStatus").textContent = "測試中";
  const data = await requestJson("/api/test-openai-endpoint", {
    base_url: $("endpointBaseUrl").value.trim(), api_key: $("apiKey").value,
    profile_id: selectedId, use_saved_key: Boolean(state.activeProfile.has_key) && !$("apiKey").value,
  });
  if (state.activeProfile.id !== selectedId) return;
  const managedModelNote = data.server_managed_model ? "；此端點使用服務端已載入模型，模型選單固定為 default" : "";
  $("endpointStatus").textContent = `${data.ok ? "可連線" : "連線失敗"}: ${data.message}${managedModelNote}`;
  if (data.ok && data.models?.length) {
    const snapshot = await requestJson("/api/asr/profiles");
    state.profiles = snapshot.profiles;
    state.activeProfile = state.profiles.find((profile) => profile.id === selectedId);
    const current = data.models.includes(selectedModel) ? selectedModel : data.models[0];
    renderEndpointModels(state.activeProfile, current);
    setResponseFormatOptions();
    await saveProfile(false);
  } else if (data.ok) {
    $("modelStatus").textContent = "端點可連線，但未提供可辨識的轉錄模型；可使用預設值或手動輸入";
  }
}

function bindEvents() {
  $("slidesTab").addEventListener("click", () => {
    state.activeTab = "slides";
    updateModeUi();
  });
  $("transcriptionTab").addEventListener("click", () => {
    state.activeTab = "transcription";
    updateModeUi();
  });
  $("taskMode").addEventListener("change", updateModeUi);
  $("asrEngine").addEventListener("change", updateAsrVisibility);
  $("endpointUploadStrategy").addEventListener("change", updateUploadStrategy);
  $("endpointProfile").addEventListener("change", () => applyProfile($("endpointProfile").value));
  $("endpointBaseUrl").addEventListener("input", () => {
    $("apiKey").value = "";
    $("saveApiKey").checked = false;
    updateKeyStatus();
    setResponseFormatOptions();
  });
  $("endpointModel").addEventListener("change", () => {
    setVisible("customModelLabel", $("endpointModel").value === "__custom");
    renderModelStatus();
    setResponseFormatOptions();
  });
  $("saveProfileBtn").addEventListener("click", () => profileAction(() => saveProfile(true))
    .then(() => { $("endpointStatus").textContent = "設定已保存"; })
    .catch((err) => { $("endpointStatus").textContent = err.message; }));
  $("deleteKeyBtn").addEventListener("click", async () => {
    if (!window.confirm("移除此服務已保存的金鑰？")) return;
    try {
      await profileAction(async () => {
        const updated = await requestJson(`/api/asr/profiles/${state.activeProfile.id}/delete-key`, {});
        state.activeProfile = updated;
        state.profiles = state.profiles.map((item) => item.id === updated.id ? updated : item);
        $("apiKey").value = "";
        updateKeyStatus();
      });
    } catch (err) { $("endpointStatus").textContent = err.message; }
  });
  $("autoInstallFasterWhisper").addEventListener("change", () => {
    renderFasterWhisperInstallStatus(state.fasterWhisperInstall);
  });
  $("autoInstallCudaRuntime").addEventListener("change", () => {
    renderCudaRuntimeInstallStatus(state.cudaRuntimeInstall);
  });
  $("installFasterWhisperBtn").addEventListener("click", () => waitForFasterWhisperInstall()
    .then(() => {
      $("progressText").textContent = "faster-whisper ASR 依賴已安裝完成。";
    })
    .catch((err) => {
      $("progressText").textContent = err.message;
    }));
  $("installCudaRuntimeBtn").addEventListener("click", () => waitForCudaRuntimeInstall()
    .then(() => {
      $("progressText").textContent = "CUDA GPU 加速依賴已安裝完成。";
    })
    .catch((err) => {
      $("progressText").textContent = err.message;
    }));
  $("startBtn").addEventListener("click", () => startJobs().catch((err) => {
    $("startBtn").disabled = false;
    $("progressText").textContent = err.message;
  }));
  $("testEndpointBtn").addEventListener("click", () => profileAction(testEndpoint).catch((err) => {
    $("endpointStatus").textContent = err.message;
  }));
  $("applyReviewBtn").addEventListener("click", () => applyReview().catch((err) => {
    $("progressText").textContent = err.message;
  }));
  $("dropZone").addEventListener("click", () => $("filePicker").click());
  $("filePicker").addEventListener("change", (event) => uploadFiles(event.target.files).catch((err) => {
    $("progressText").textContent = err.message;
  }));
  $("dropZone").addEventListener("dragover", (event) => {
    event.preventDefault();
    $("dropZone").classList.add("dragover");
  });
  $("dropZone").addEventListener("dragleave", () => $("dropZone").classList.remove("dragover"));
  $("dropZone").addEventListener("drop", (event) => {
    event.preventDefault();
    $("dropZone").classList.remove("dragover");
    uploadFiles(event.dataTransfer.files).catch((err) => {
      $("progressText").textContent = err.message;
    });
  });
  $("fileQueue").addEventListener("click", (event) => {
    const btn = event.target.closest("button");
    if (!btn || btn.dataset.action !== "remove-file") return;
    state.queuedFiles.splice(Number(btn.dataset.pos), 1);
    renderFileQueue();
  });
  $("batchJobs").addEventListener("click", (event) => {
    const row = event.target.closest(".job-row");
    if (!row) return;
    const job = state.jobs.get(row.dataset.jobId);
    if (job?.result) {
      state.activeJobId = job.id;
      $("jobId").textContent = job.id;
      renderArtifacts(job.result);
      renderThumbs(job.result.slides || []);
    }
  });
  $("thumbGrid").addEventListener("click", (event) => {
    const btn = event.target.closest("button");
    if (!btn) return;
    const pos = Number(btn.dataset.pos);
    if (btn.dataset.action === "toggle") state.slides[pos].kept = !state.slides[pos].kept;
    if (btn.dataset.action === "up" && pos > 0) {
      [state.slides[pos - 1], state.slides[pos]] = [state.slides[pos], state.slides[pos - 1]];
    }
    if (btn.dataset.action === "down" && pos < state.slides.length - 1) {
      [state.slides[pos + 1], state.slides[pos]] = [state.slides[pos], state.slides[pos + 1]];
    }
    renderThumbs(state.slides);
  });
}

async function init() {
  bindEvents();
  const response = await apiFetch("/api/meta");
  const meta = await response.json();
  if (!response.ok) throw new Error("請重新開啟本機工具頁面以建立安全工作階段。");
  state.csrfToken = meta.csrf_token;
  $("versionText").textContent = `${meta.app.name} ${meta.app.version}`;
  $("authorDescription").textContent = meta.author.description;
  $("blogLink").href = meta.author.blog;
  $("facebookLink").href = meta.author.facebook;
  $("sourceLink").href = meta.author.source_repo;

  state.media.videoExtensions = meta.media?.video_extensions || state.media.videoExtensions;
  state.media.audioExtensions = meta.media?.audio_extensions || state.media.audioExtensions;
  state.cudaDiagnostics = meta.asr.cuda_diagnostics || null;
  if (state.cudaDiagnostics?.gpu_available) {
    $("envStatus").textContent = state.cudaDiagnostics.runtime_ready ? "CUDA GPU 可使用" : "CUDA GPU 已偵測";
  } else {
    $("envStatus").textContent = "CPU 模式";
  }

  $("asrModel").innerHTML = meta.asr.models.map((model) => `<option value="${model}">${model}</option>`).join("");
  $("asrModel").value = meta.asr.cuda_available ? "turbo" : "base";
  $("asrLanguage").innerHTML = meta.asr.languages
    .map((lang) => `<option value="${lang.value}">${lang.label_zh}</option>`)
    .join("");
  $("asrLanguage").value = "";

  const snapshot = await requestJson("/api/asr/profiles");
  state.profiles = snapshot.profiles;
  state.secureStorageAvailable = snapshot.secure_storage_available;
  renderProfiles(snapshot.active_profile);
  if (snapshot.configured) $("asrEngine").value = "openai-compatible";
  applyProfile(snapshot.active_profile);
  renderFasterWhisperInstallStatus(meta.asr.faster_whisper_install);
  renderCudaRuntimeInstallStatus(meta.asr.cuda_runtime_install);
  setResponseFormatOptions();
  updateModeUi();
  setVisible("reviewSection", false);
}

init().catch((err) => {
  $("envStatus").textContent = err.message;
});
