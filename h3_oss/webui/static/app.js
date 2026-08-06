const $ = (selector) => document.querySelector(selector);
const localMutationHeaders = Object.freeze({ "X-H3-Studio-Request": "1" });
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const ui = {
  modelDot: $("#model-dot"),
  modelStatus: $("#model-status"),
  gpuStatus: $("#gpu-status"),
  ramStatus: $("#ram-status"),
  modeGrid: $("#mode-grid"),
  modeDescription: $("#mode-description"),
  omniLock: $("#omni-lock"),
  attachmentSection: $("#attachment-section"),
  frameUploads: $("#frame-uploads"),
  omniUpload: $("#omni-upload"),
  lastUploadCard: $("#last-upload-card"),
  lastImageHint: $("#last-image-hint"),
  firstInput: $("#first-image"),
  lastInput: $("#last-image"),
  firstPreview: $("#first-preview"),
  lastPreview: $("#last-preview"),
  removeFirst: $("#remove-first"),
  removeLast: $("#remove-last"),
  referenceInput: $("#reference-files"),
  referenceDropZone: $("#reference-drop-zone"),
  referenceList: $("#reference-list"),
  refImageSize: $("#ref-image-size"),
  refImageSizeNote: $("#ref-image-size-note"),
  referenceAudioPolicyField: $("#reference-audio-policy-field"),
  standaloneAudioPolicy: $("#standalone-audio-policy"),
  standaloneAudioPolicyNote: $("#standalone-audio-policy-note"),
  styleStep: $("#style-step"),
  promptStep: $("#prompt-step"),
  settingsStep: $("#settings-step"),
  prompt: $("#prompt"),
  promptCount: $("#prompt-count"),
  promptHistory: $("#prompt-history"),
  reusePrompt: $("#reuse-prompt"),
  soundSection: $("#sound-section"),
  soundStatus: $("#sound-status"),
  soundPreset: $("#sound-preset"),
  musicPolicy: $("#music-policy"),
  dialogue: $("#dialogue"),
  dialogueCount: $("#dialogue-count"),
  soundscape: $("#soundscape"),
  soundscapeCount: $("#soundscape-count"),
  audioGain: $("#audio-gain"),
  quality: $("#quality"),
  aspectRatio: $("#aspect-ratio"),
  resolutionQuality: $("#resolution-quality"),
  duration: $("#duration"),
  seed: $("#seed"),
  acceleration: $("#acceleration"),
  accelerationNote: $("#acceleration-note"),
  promptProcessingMode: $("#prompt-processing-mode"),
  promptProcessingNote: $("#prompt-processing-note"),
  weightWarning: $("#weight-warning"),
  weightWarningTitle: $("#weight-warning-title"),
  weightWarningText: $("#weight-warning-text"),
  quickPreview: $("#quick-preview"),
  generate: $("#generate"),
  generateSummary: $("#generate-summary"),
  plannerWarning: $("#planner-warning"),
  formError: $("#form-error"),
  queueBadge: $("#queue-badge"),
  emptyStage: $("#empty-stage"),
  progressStage: $("#progress-stage"),
  progressRing: $("#progress-ring"),
  progressNumber: $("#progress-number"),
  progressCaption: $("#progress-caption"),
  progressPhase: $("#progress-phase"),
  progressMessage: $("#progress-message"),
  progressElapsed: $("#progress-elapsed"),
  progressStep: $("#progress-step"),
  cancelJob: $("#cancel-job"),
  resultStage: $("#result-stage"),
  resultVideo: $("#result-video"),
  resultTitle: $("#result-title"),
  resultMeta: $("#result-meta"),
  downloadResult: $("#download-result"),
  reuseJob: $("#reuse-job"),
  reuseErrorPrompt: $("#reuse-error-prompt"),
  errorStage: $("#error-stage"),
  errorTitle: $("#error-title"),
  errorMessage: $("#error-message"),
  showLog: $("#show-log"),
  logView: $("#log-view"),
  jobDetails: $("#job-details"),
  jobDetailsDisclosure: $("#job-details-disclosure"),
  jobDetailsCaption: $("#job-details-caption"),
  detailOriginalGroup: $("#detail-original-group"),
  detailOriginal: $("#detail-original"),
  detailEffectiveGroup: $("#detail-effective-group"),
  detailEffective: $("#detail-effective"),
  detailAdjustmentsGroup: $("#detail-adjustments-group"),
  detailAdjustments: $("#detail-adjustments"),
  detailReferenceGroup: $("#detail-reference-group"),
  detailReference: $("#detail-reference"),
  detailTechnicalGroup: $("#detail-technical-group"),
  detailTechnical: $("#detail-technical"),
  historyGrid: $("#history-grid"),
  toast: $("#toast"),
};

const state = {
  mode: "t2v",
  style: "natural",
  firstFile: null,
  lastFile: null,
  references: [],
  capabilities: null,
  jobs: [],
  selectedJobId: null,
  pollTimer: null,
  toastTimer: null,
  promptHistorySignature: "",
  jobDetailsSignature: "",
  resolutionCatalog: null,
  resolutionCatalogSignature: "",
  previewUrls: { first: null, last: null },
};

const fallbackResolutionCatalog = {
  default_aspect_ratio: "16:9",
  default_quality: "sd",
  aspect_ratios: [
    {
      id: "16:9",
      label: "横 16:9",
      presets: {
        preview: { width: 672, height: 384 },
        sd: { width: 864, height: 480 },
        hd: { width: 1312, height: 736 },
        native: { width: 1344, height: 768 },
      },
    },
    {
      id: "9:16",
      label: "縦 9:16",
      presets: {
        preview: { width: 384, height: 672 },
        sd: { width: 480, height: 864 },
        hd: { width: 736, height: 1312 },
        native: { width: 768, height: 1344 },
      },
    },
    {
      id: "1:1",
      label: "正方形 1:1",
      presets: {
        preview: { width: 384, height: 384 },
        sd: { width: 480, height: 480 },
        hd: { width: 736, height: 736 },
        native: { width: 768, height: 768 },
      },
    },
    {
      id: "4:3",
      label: "横 4:3",
      presets: {
        preview: { width: 512, height: 384 },
        sd: { width: 640, height: 480 },
        hd: { width: 896, height: 672 },
        native: { width: 1024, height: 768 },
      },
    },
    {
      id: "3:4",
      label: "縦 3:4",
      presets: {
        preview: { width: 384, height: 512 },
        sd: { width: 480, height: 640 },
        hd: { width: 672, height: 896 },
        native: { width: 768, height: 1024 },
      },
    },
  ],
  qualities: [
    { id: "preview", label: "Preview" },
    { id: "sd", label: "SD 480p相当" },
    { id: "hd", label: "HD 720p相当" },
    { id: "native", label: "Native 768p" },
  ],
};

const modeDescriptions = {
  t2v: "テキストだけで、映像と同期音声を同時に生成します。",
  i2v: "1枚の画像を開始フレームにして、動きと同期音声を生成します。",
  first_last: "開始と終了の2枚を指定して、その間の動きと音を生成します。",
  omni: "画像・動画・音声を順番付きで参照します。",
};

function updateModeDescription() {
  if (state.mode !== "omni") {
    ui.modeDescription.textContent = modeDescriptions[state.mode];
    return;
  }
  const useMax = ui.refImageSize.value === "max";
  ui.modeDescription.textContent = useMax
    ? "画像・動画・音声を順番付きで参照します。画像はアップスケールせず、短辺2048pxを上限に高精度で解析します。"
    : "画像・動画・音声を順番付きで参照します。画像は出力面積に合わせて縮小し、軽い設定で解析します。";
  ui.refImageSizeNote.textContent = useMax
    ? "画像はアップスケールせず、短辺2048pxを上限に元解像度寄りで解析します。細部を残しやすい一方、Qwen解析とattentionが重くなります。"
    : "画像を出力面積に合わせて縮小して解析します。通常はこちらが速く、参照処理の負荷も抑えられます。";
  ui.refImageSizeNote.classList.toggle("high-precision", useMax);
}

const statusLabels = {
  queued: "待機中",
  running: "生成中",
  completed: "完成",
  failed: "失敗",
  cancelled: "中止",
  interrupted: "中断",
};

const modeLabels = {
  t2v: "Text",
  i2v: "Image",
  first_last: "Frames",
  omni: "Omni",
};

const audioPresetLabels = {
  auto: "音は自動",
  dialogue: "会話優先",
  ambience: "環境音優先",
  effects: "効果音優先",
  music: "音楽優先",
  quiet: "静かな音場",
};

const accelerationLabels = {
  off: "高速化なし（比較用）",
  community: "おすすめ：控えめ（通常）",
  conservative: "より慎重（品質優先）",
  balanced: "速度優先（品質を確認）",
};

function jobAccelerationMeta(job) {
  const mode = job.acceleration || job.acceleration_mode || "off";
  const cache = job.cache || job.easycache || job.easy_cache || job.cache_stats || {};
  const skipped = cache.skipped_steps
    ?? cache.skipped
    ?? job.easycache_skipped_steps
    ?? job.cache_skipped_steps;
  const total = cache.total_steps ?? cache.total ?? job.easycache_total_steps;
  const backend = job.backend || job.engine_backend;
  const parts = [];
  if (backend) parts.push(`Backend ${backend}`);
  if (job.attention_backend === "sage") parts.push("SageAttention");
  if (mode !== "off") {
    if (cache.enabled === false) {
      parts.push(cache.reason === "steps_below_12" ? "EasyCache 自動OFF（Draft）" : "EasyCache OFF");
    } else {
      const threshold = Number.isFinite(Number(cache.reuse_threshold))
        ? Number(cache.reuse_threshold).toFixed(2)
        : mode === "conservative" ? "0.20" : mode === "balanced" ? "0.30" : mode;
      let label = `EasyCache ${threshold}`;
      if (Number.isFinite(Number(skipped))) {
        label += ` · ${Number(skipped)}${Number.isFinite(Number(total)) ? `/${Number(total)}` : ""} skip`;
      }
      if (Number.isFinite(Number(cache.speedup)) && Number(cache.speedup) > 0) {
        label += ` · ×${Number(cache.speedup).toFixed(2)}`;
      }
      parts.push(label);
    }
  }
  return parts;
}

const samples = {
  t2v: "雨の夜の東京。黒いスポーツカーが、ネオンの反射する濡れた路面をゆっくり走る。カメラは車の横を滑らかに追う。雨音、タイヤが水たまりを通る音、遠い街の環境音が続く。",
  i2v: "開始画像の人物が自然に動き始め、ゆっくりカメラへ顔を向ける。カメラは小さくゆっくり寄る。静かな室内音と、衣服が動く小さな音が聞こえる。",
  first_last: "開始画像の構図から終了画像の構図へ、人物の動きとカメラ位置が滑らかにつながる。動作は自然な速度を保ち、同じ空間の環境音が途切れず続く。",
  omni: "Cut1\n<Picture 1>の人物が静かな海辺を歩く。外見と衣装は参照画像どおりに保つ。カメラは正面斜めからゆっくり並走し、穏やかな波音と海風が続く。\n\nCut2\n<Picture 1>の人物がカメラを見て、やわらかな声で「きれいな海だね。」と一度だけ言う。弱い風が髪と衣服を揺らす。",
};

function toast(message, duration = 3300) {
  clearTimeout(state.toastTimer);
  ui.toast.textContent = message;
  ui.toast.classList.add("visible");
  state.toastTimer = setTimeout(() => ui.toast.classList.remove("visible"), duration);
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function formatElapsed(iso) {
  if (!iso) return "経過 00:00";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  const formatted = hours
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
  return `経過 ${formatted}`;
}

function setPromptValue(value, focus = false) {
  ui.prompt.value = String(value || "").slice(0, 4000);
  ui.promptCount.textContent = String(ui.prompt.value.length);
  ui.formError.textContent = "";
  if (focus) ui.prompt.focus();
}

function secondsSince(iso) {
  if (!iso) return 0;
  return Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
}

function stageEstimate(actual, ceiling, elapsed, timeConstant) {
  if (ceiling <= actual) return actual;
  const fraction = 0.92 * (1 - Math.exp(-elapsed / Math.max(timeConstant, 1)));
  return Math.min(ceiling, actual + (ceiling - actual) * fraction);
}

function denoiseStepSeconds(job) {
  const quickWork = 320 * 192 * 124;
  const work = Math.max(quickWork, Number(job.width) * Number(job.height) * Number(job.num_frames));
  const referenceFactor = job.mode === "omni" ? 1 + Math.min(4, job.attachments?.length || 0) * 0.28 : 1;
  return Math.max(5, Math.min(240, 5 * Math.pow(work / quickWork, 0.8) * referenceFactor));
}

function displayedProgress(job) {
  const actual = Math.max(0, Math.min(100, Number(job.progress) || 0));
  if (job.status !== "running" || actual >= 100) return { value: actual, estimated: false };
  const elapsed = secondsSince(job.progress_updated_at || job.started_at || job.created_at);

  if (actual >= 55 && actual < 90 && Number(job.total_steps) > 0) {
    const total = Number(job.total_steps);
    const step = Number(job.step) || 0;
    const nextBoundary = Math.min(90, 55 + 35 * (step + 1) / total - 0.04);
    const value = stageEstimate(actual, nextBoundary, elapsed, denoiseStepSeconds(job));
    return { value, estimated: value > actual + 0.04 };
  }

  let ceiling = actual;
  let timeConstant = 30;
  if (actual < 4) [ceiling, timeConstant] = [3.8, 8];
  else if (actual < 7) [ceiling, timeConstant] = [6.8, 12];
  else if (actual < 17) [ceiling, timeConstant] = [16.8, 75];
  else if (actual < 26) [ceiling, timeConstant] = [25.8, 85];
  else if (actual < 30) [ceiling, timeConstant] = [29.8, 35];
  else if (actual < 45) {
    ceiling = 44.8;
    timeConstant = job.mode === "omni" ? 180 * Math.max(1, job.attachments?.length || 1) : 55;
  } else if (actual < 52) {
    ceiling = 51.8;
    timeConstant = job.mode === "omni" ? 150 * Math.max(1, job.attachments?.length || 1) : 70;
  } else if (actual < 55) [ceiling, timeConstant] = [54.8, 45];
  else if (actual < 91) [ceiling, timeConstant] = [90.8, 40];
  else if (actual < 92) [ceiling, timeConstant] = [91.8, 90];
  else if (actual < 93) [ceiling, timeConstant] = [92.8, 45];
  else if (actual < 96) [ceiling, timeConstant] = [95.8, 35];
  else if (actual < 100) [ceiling, timeConstant] = [99.4, 22];

  const value = stageEstimate(actual, ceiling, elapsed, timeConstant);
  return { value, estimated: value > actual + 0.04 };
}

function selectedJob() {
  return state.jobs.find((job) => job.id === state.selectedJobId) || null;
}

function setMode(mode) {
  if (mode === "omni" && state.capabilities && !state.capabilities.modes.omni) {
    toast("OmniはRef2VA公式重みの準備後に有効になります。");
    return;
  }
  state.mode = mode;
  $$(".mode-card").forEach((button) => {
    const selected = button.dataset.mode === mode;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-checked", String(selected));
  });
  updateModeDescription();
  const hasAttachment = mode !== "t2v";
  ui.attachmentSection.classList.toggle("hidden", !hasAttachment);
  ui.frameUploads.classList.toggle("hidden", mode === "omni");
  ui.omniUpload.classList.toggle("hidden", mode !== "omni");
  ui.lastUploadCard.classList.toggle("hidden", mode === "i2v");
  ui.lastImageHint.textContent = mode === "first_last" ? "必須" : "任意";
  ui.styleStep.textContent = hasAttachment ? "03" : "02";
  ui.promptStep.textContent = hasAttachment ? "04" : "03";
  ui.settingsStep.textContent = hasAttachment ? "05" : "04";
  updateSummary();
}

function setStyle(style) {
  state.style = style;
  $$(".style-chip").forEach((button) => button.classList.toggle("selected", button.dataset.style === style));
}

function setFrameFile(which, file) {
  const isFirst = which === "first";
  const stateKey = isFirst ? "firstFile" : "lastFile";
  const preview = isFirst ? ui.firstPreview : ui.lastPreview;
  const remove = isFirst ? ui.removeFirst : ui.removeLast;
  const oldUrl = state.previewUrls[which];
  if (oldUrl) URL.revokeObjectURL(oldUrl);
  state[stateKey] = file || null;
  state.previewUrls[which] = file ? URL.createObjectURL(file) : null;
  preview.replaceChildren();
  if (file) {
    const image = document.createElement("img");
    image.src = state.previewUrls[which];
    image.alt = isFirst ? "開始画像プレビュー" : "終了画像プレビュー";
    preview.append(image);
    remove.classList.remove("hidden");
  } else {
    const symbol = document.createElement("span");
    symbol.className = "upload-symbol";
    symbol.textContent = "＋";
    preview.append(symbol);
    remove.classList.add("hidden");
  }
}

function referenceType(file) {
  if (file.type.startsWith("image/")) return "image";
  if (file.type.startsWith("video/")) return "video";
  if (file.type.startsWith("audio/")) return "audio";
  return "unknown";
}

function referenceKind(file) {
  return { image: "画像", video: "動画", audio: "音声" }[referenceType(file)] || "素材";
}

function referenceTag(type, index) {
  const label = { image: "Picture", video: "Video", audio: "Audio" }[type] || "Reference";
  return `<${label} ${index}>`;
}

function jobReferenceMap(job) {
  if (job?.mode !== "omni") return [];
  const counts = { image: 0, video: 0, audio: 0 };
  return (job?.attachments || [])
    .filter((attachment) => ["image", "video", "audio"].includes(attachment?.kind))
    .map((attachment) => {
      counts[attachment.kind] += 1;
      return {
        kind: attachment.kind,
        name: String(attachment.name || ""),
        size: Number(attachment.size || 0),
        tag: referenceTag(attachment.kind, counts[attachment.kind]),
      };
    });
}

function attachmentSignature(job) {
  return (job?.attachments || [])
    .map((entry) => [entry?.kind || "", entry?.name || "", Number(entry?.size || 0)].join("\u0002"))
    .join("\u0003");
}

function prepareReferencesForReuse(job) {
  const expected = jobReferenceMap(job);
  if (!expected.length) return "";
  // Browser File objects do not expose a trusted persisted content hash.  A
  // filename+size comparison can mistake different content for the old input,
  // so prompt reuse always requires explicit reattachment in the audited order.
  state.references = [];
  renderReferences();
  const order = expected.map((entry) => `${entry.tag} ${entry.name}`).join(" → ");
  return `内容の取り違えを防ぐため参照素材をクリアしました。同じ順で再添付: ${order}`;
}

function insertReferenceTag(tag) {
  const start = ui.prompt.selectionStart ?? ui.prompt.value.length;
  const end = ui.prompt.selectionEnd ?? start;
  const before = ui.prompt.value.slice(0, start);
  const after = ui.prompt.value.slice(end);
  const leading = before && !/\s$/.test(before) ? " " : "";
  const trailing = after && !/^\s/.test(after) ? " " : "";
  const insertion = `${leading}${tag}${trailing}`;
  const value = `${before}${insertion}${after}`.slice(0, 4000);
  setPromptValue(value, true);
  const cursor = Math.min(value.length, before.length + insertion.length - trailing.length);
  ui.prompt.setSelectionRange(cursor, cursor);
  toast(`${tag} をプロンプトへ挿入しました。`);
}

function addReferences(files) {
  const incoming = Array.from(files).filter((file) => /^(image|video|audio)\//.test(file.type));
  const remaining = Math.max(0, 12 - state.references.length);
  state.references.push(...incoming.slice(0, remaining));
  if (incoming.length > remaining) toast("Omni参照は合計12素材までです。");
  renderReferences();
}

function updateStandaloneAudioPolicyNote() {
  const fullContent = ui.standaloneAudioPolicy.value === "full_content";
  ui.standaloneAudioPolicyNote.textContent = fullContent
    ? "音声波形全体をH3へ渡します。声色・抑揚だけでなく、元音声の言葉、間、場面まで出力へ強く影響する場合があります。"
    : "明示台詞がある生成では、添付音声をH3の生成条件から外します。声色は参照されません。";
}

function renderReferences() {
  ui.referenceList.replaceChildren();
  const counts = { image: 0, video: 0, audio: 0 };
  state.references.forEach((file, index) => {
    const type = referenceType(file);
    counts[type] = (counts[type] || 0) + 1;
    const tag = referenceTag(type, counts[type]);
    const item = document.createElement("div");
    item.className = "reference-item";

    const number = document.createElement("span");
    number.className = "reference-index";
    number.textContent = String(index + 1).padStart(2, "0");

    const copy = document.createElement("span");
    copy.className = "reference-name";
    const nameLine = document.createElement("span");
    nameLine.className = "reference-name-line";
    const tagButton = document.createElement("button");
    tagButton.type = "button";
    tagButton.className = "reference-tag";
    tagButton.textContent = tag;
    tagButton.title = `${tag} をプロンプトへ挿入`;
    tagButton.setAttribute("aria-label", `${tag} をプロンプトへ挿入`);
    tagButton.addEventListener("click", () => insertReferenceTag(tag));
    const name = document.createElement("strong");
    name.textContent = file.name;
    nameLine.append(tagButton, name);
    const meta = document.createElement("small");
    meta.textContent = `${referenceKind(file)} · ${formatBytes(file.size)} · UI順 ${String(index + 1).padStart(2, "0")}`;
    copy.append(nameLine, meta);

    const actions = document.createElement("span");
    actions.className = "reference-actions";
    [
      ["↑", -1, "前へ"],
      ["↓", 1, "後ろへ"],
      ["×", 0, "削除"],
    ].forEach(([label, direction, title]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.title = title;
      button.addEventListener("click", () => {
        if (direction === 0) {
          state.references.splice(index, 1);
        } else {
          const target = index + direction;
          if (target >= 0 && target < state.references.length) {
            [state.references[index], state.references[target]] = [state.references[target], state.references[index]];
          }
        }
        renderReferences();
      });
      actions.append(button);
    });
    item.append(number, copy, actions);
    ui.referenceList.append(item);
  });
  const hasStandaloneAudio = state.references.some((file) => referenceType(file) === "audio");
  ui.referenceAudioPolicyField.classList.toggle("hidden", !hasStandaloneAudio);
  if (!hasStandaloneAudio) ui.standaloneAudioPolicy.value = "dialogue_priority";
  updateStandaloneAudioPolicyNote();
  updateSummary();
}

function renderPromptHistory() {
  const previous = ui.promptHistory.value;
  const unique = [];
  const prompts = new Set();
  state.jobs.forEach((job) => {
    const normalized = String(job.prompt || "").trim();
    const promptKey = [
      normalized,
      job.prompt_processing_mode || "community",
      job.standalone_audio_policy_requested || "dialogue_priority",
      job.audio_preset || "auto",
      job.dialogue || "",
      job.soundscape || "",
      job.music_policy || "auto",
      job.audio_gain_db ?? 0,
      attachmentSignature(job),
    ].join("\u0001");
    if (!normalized || prompts.has(promptKey)) return;
    prompts.add(promptKey);
    unique.push(job);
  });
  const signature = unique.slice(0, 20).map((job) => [
    job.id,
    job.prompt,
    job.prompt_processing_mode,
    job.standalone_audio_policy_requested,
    job.audio_preset,
    job.dialogue,
    job.soundscape,
    job.music_policy,
    job.audio_gain_db,
    attachmentSignature(job),
  ].join("\u0001")).join("\u0000");
  if (signature === state.promptHistorySignature) return;
  state.promptHistorySignature = signature;

  ui.promptHistory.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = unique.length ? "過去のプロンプトを選択…" : "再利用できるプロンプトはまだありません";
  ui.promptHistory.append(placeholder);
  unique.slice(0, 20).forEach((job) => {
    const option = document.createElement("option");
    option.value = job.id;
    const oneLine = job.prompt.replace(/\s+/g, " ").trim();
    const audioLabel = audioPresetLabels[job.audio_preset || "auto"] || "音は自動";
    option.textContent = `[${modeLabels[job.mode] || job.mode} · ${audioLabel}] ${oneLine.length > 62 ? `${oneLine.slice(0, 62)}…` : oneLine}`;
    ui.promptHistory.append(option);
  });
  if (unique.some((job) => job.id === previous)) ui.promptHistory.value = previous;
  ui.reusePrompt.disabled = !ui.promptHistory.value;
}

function restoreAudioPrompt(job) {
  const riskyAudioPolicy = [
    job?.standalone_audio_policy_requested,
    job?.standalone_audio_policy_effective,
  ].some((value) => value === "full_content");
  const riskyPromptProcessing = !["community", "raw_en"].includes(
    job?.prompt_processing_mode || "community",
  );
  // Persisted jobs from removed modes return to the current community planner.
  const restoredPromptMode = ["community", "raw_en"].includes(job?.prompt_processing_mode)
    ? job.prompt_processing_mode
    : "community";
  ui.promptProcessingMode.value = restoredPromptMode;
  // Full-content Audio is an explicit risk opt-in and is never silently
  // restored from history.  A reused job always returns to the safe policy.
  ui.standaloneAudioPolicy.value = "dialogue_priority";
  updateStandaloneAudioPolicyNote();
  ui.soundPreset.value = job?.audio_preset || "auto";
  ui.musicPolicy.value = job?.music_policy || "auto";
  ui.dialogue.value = job?.dialogue || "";
  ui.dialogueCount.textContent = String(ui.dialogue.value.length);
  ui.soundscape.value = job?.soundscape || "";
  ui.soundscapeCount.textContent = String(ui.soundscape.value.length);
  ui.audioGain.value = String(job?.audio_gain_db ?? 0);
  ui.soundSection.open = Boolean(
    ui.dialogue.value
    || ui.soundscape.value
    || ui.soundPreset.value !== "auto"
    || ui.musicPolicy.value !== "auto"
    || ui.audioGain.value !== "0"
  );
  syncPromptModeControls({ resetRawFields: restoredPromptMode === "raw_en" });
  const messages = [];
  if (riskyAudioPolicy) {
    messages.push("元音声を使う実験設定は安全のため復元せず、「指定台詞を優先」へ戻しました。");
  }
  if (riskyPromptProcessing) {
    messages.push("日本語本文の直渡しは復元せず、本文の読み上げを避ける推奨処理へ戻しました。");
  }
  return messages.join(" ");
}

function reusePromptOnly(job) {
  if (!job) return;
  setPromptValue(job.prompt, true);
  const audioPolicyMessage = restoreAudioPrompt(job);
  const referenceMessage = prepareReferencesForReuse(job);
  ui.prompt.closest(".form-section").scrollIntoView({ behavior: "smooth", block: "center" });
  const messages = [referenceMessage, audioPolicyMessage].filter(Boolean);
  toast(messages.join(" ") || "プロンプトと任意の音声詳細を復元しました。", messages.length ? 9000 : 3300);
}

function validResolutionPreset(preset) {
  const width = Number(preset?.width);
  const height = Number(preset?.height);
  return Number.isInteger(width) && width > 0 && Number.isInteger(height) && height > 0;
}

function normalizeResolutionCatalog(rawCatalog) {
  const usable = rawCatalog
    && Array.isArray(rawCatalog.aspect_ratios)
    && Array.isArray(rawCatalog.qualities);
  const source = usable ? rawCatalog : fallbackResolutionCatalog;
  const qualities = source.qualities
    .filter((quality) => quality && typeof quality.id === "string")
    .map((quality) => ({ id: quality.id, label: String(quality.label || quality.id) }));
  const aspectRatios = source.aspect_ratios
    .filter((aspect) => aspect && typeof aspect.id === "string" && aspect.presets)
    .map((aspect) => {
      const presets = {};
      qualities.forEach((quality) => {
        const preset = aspect.presets[quality.id];
        if (!validResolutionPreset(preset)) return;
        presets[quality.id] = {
          width: Number(preset.width),
          height: Number(preset.height),
          label: String(preset.label || `${preset.width}×${preset.height}`),
        };
      });
      return { id: aspect.id, label: String(aspect.label || aspect.id), presets };
    })
    .filter((aspect) => Object.keys(aspect.presets).length > 0);

  if ((!qualities.length || !aspectRatios.length) && source !== fallbackResolutionCatalog) {
    return normalizeResolutionCatalog(fallbackResolutionCatalog);
  }
  const defaultAspect = aspectRatios.some((aspect) => aspect.id === source.default_aspect_ratio)
    ? source.default_aspect_ratio
    : aspectRatios[0].id;
  const defaultAspectEntry = aspectRatios.find((aspect) => aspect.id === defaultAspect);
  const defaultQuality = defaultAspectEntry.presets[source.default_quality]
    ? source.default_quality
    : qualities.find((quality) => defaultAspectEntry.presets[quality.id])?.id;
  return {
    default_aspect_ratio: defaultAspect,
    default_quality: defaultQuality,
    aspect_ratios: aspectRatios,
    qualities,
  };
}

function activeResolutionCatalog() {
  return state.resolutionCatalog || normalizeResolutionCatalog(fallbackResolutionCatalog);
}

function selectedResolutionAspect() {
  const catalog = activeResolutionCatalog();
  return catalog.aspect_ratios.find((aspect) => aspect.id === ui.aspectRatio.value)
    || catalog.aspect_ratios[0];
}

function syncResolutionQualityOptions(preferredQuality = ui.resolutionQuality.value) {
  const catalog = activeResolutionCatalog();
  const aspect = selectedResolutionAspect();
  ui.aspectRatio.value = aspect.id;
  ui.resolutionQuality.replaceChildren();
  catalog.qualities.forEach((quality) => {
    const preset = aspect.presets[quality.id];
    if (!validResolutionPreset(preset)) return;
    const option = document.createElement("option");
    option.value = quality.id;
    option.textContent = `${quality.label} · ${preset.width}×${preset.height}`;
    ui.resolutionQuality.append(option);
  });
  const available = Array.from(ui.resolutionQuality.options).map((option) => option.value);
  ui.resolutionQuality.value = available.includes(preferredQuality)
    ? preferredQuality
    : available.includes(catalog.default_quality)
      ? catalog.default_quality
      : available[0] || "";
}

function configureResolutionControls(rawCatalog) {
  const catalog = normalizeResolutionCatalog(rawCatalog);
  const signature = JSON.stringify(catalog);
  if (signature === state.resolutionCatalogSignature) return;

  const previousAspect = ui.aspectRatio.value;
  const previousQuality = ui.resolutionQuality.value;
  state.resolutionCatalog = catalog;
  state.resolutionCatalogSignature = signature;
  ui.aspectRatio.replaceChildren();
  catalog.aspect_ratios.forEach((aspect) => {
    const option = document.createElement("option");
    option.value = aspect.id;
    option.textContent = aspect.label;
    ui.aspectRatio.append(option);
  });
  const aspectIds = catalog.aspect_ratios.map((aspect) => aspect.id);
  ui.aspectRatio.value = aspectIds.includes(previousAspect)
    ? previousAspect
    : catalog.default_aspect_ratio;
  syncResolutionQualityOptions(previousQuality || catalog.default_quality);
  updateSummary();
}

function selectedResolutionPreset() {
  const catalog = activeResolutionCatalog();
  const aspect = selectedResolutionAspect();
  const quality = catalog.qualities.find((item) => item.id === ui.resolutionQuality.value);
  const preset = aspect.presets[quality?.id];
  if (!quality || !validResolutionPreset(preset)) return null;
  return {
    aspectId: aspect.id,
    aspectLabel: aspect.label,
    qualityId: quality.id,
    qualityLabel: quality.label,
    width: Number(preset.width),
    height: Number(preset.height),
  };
}

function setResolutionSelection(aspectId, qualityId) {
  const catalog = activeResolutionCatalog();
  const aspect = catalog.aspect_ratios.find((item) => item.id === aspectId)
    || catalog.aspect_ratios.find((item) => item.id === catalog.default_aspect_ratio)
    || catalog.aspect_ratios[0];
  ui.aspectRatio.value = aspect.id;
  syncResolutionQualityOptions(qualityId || catalog.default_quality);
}

function selectNearestResolution(width, height) {
  const targetWidth = Number(width);
  const targetHeight = Number(height);
  const catalog = activeResolutionCatalog();
  if (!(targetWidth > 0 && targetHeight > 0)) {
    setResolutionSelection(catalog.default_aspect_ratio, catalog.default_quality);
    return selectedResolutionPreset();
  }

  let nearest = null;
  catalog.aspect_ratios.forEach((aspect) => {
    catalog.qualities.forEach((quality) => {
      const preset = aspect.presets[quality.id];
      if (!validResolutionPreset(preset)) return;
      const exact = preset.width === targetWidth && preset.height === targetHeight;
      const areaDelta = Math.abs(Math.log((preset.width * preset.height) / (targetWidth * targetHeight)));
      const aspectDelta = Math.abs(Math.log((preset.width / preset.height) / (targetWidth / targetHeight)));
      const score = exact ? -1 : areaDelta + (aspectDelta * 2);
      if (!nearest || score < nearest.score) {
        nearest = { aspectId: aspect.id, qualityId: quality.id, score };
      }
    });
  });
  setResolutionSelection(nearest?.aspectId, nearest?.qualityId);
  return selectedResolutionPreset();
}

function updateSummary() {
  updateModeDescription();
  const quality = ui.quality.options[ui.quality.selectedIndex].text.split(" · ")[0];
  const resolution = selectedResolutionPreset();
  const resolutionQuality = resolution?.qualityLabel.split(/\s+/)[0] || "—";
  const resolutionSummary = resolution
    ? `${resolution.aspectId} · ${resolutionQuality} · ${resolution.width}×${resolution.height}`
    : "解像度未設定";
  const duration = ui.duration.options[ui.duration.selectedIndex].text;
  const audio = audioPresetLabels[ui.soundPreset.value] || "音は自動";
  const requestedAcceleration = ui.acceleration.value;
  const cacheAutoOff = Number(ui.quality.value) < 12 && requestedAcceleration !== "off";
  const acceleration = cacheAutoOff
    ? "Draft：高速化なし（自動）"
    : accelerationLabels[requestedAcceleration] || "高速化なし（比較用）";
  ui.generateSummary.textContent = `${resolutionSummary} · ${duration} · ${quality} · ${audio} · ${acceleration}`;
  ui.accelerationNote.classList.toggle("auto-off", cacheAutoOff);
  ui.accelerationNote.textContent = cacheAutoOff
    ? "Draft（12 steps未満）では自動的に高速化なしになります。"
    : "公開例はthreshold 0.20・sampling 15%～95%。EasyCacheは近似処理なので、Draft（steps<12）では自動OFF。映像と音声がわずかに変わる可能性があります。";
  ui.accelerationNote.textContent = cacheAutoOff
    ? "Draft（12 steps未満）では自動的に高速化なしになります。"
    : "通常は「おすすめ：控えめ（通常）」で問題ありません。品質比較は「高速化なし」、速度優先の試作は「速度優先」を選んでください。";
  const { width, height } = resolution || { width: 0, height: 0 };
  const denoiseForwards = Math.max(1, Number(ui.quality.value) - 1);
  const baselineWork = 960 * 544 * 124 * 7;
  const outputWork = width * height * Number(ui.duration.value) * denoiseForwards;
  const relativeWork = outputWork / baselineWork;
  const omniReferences = state.mode === "omni" ? state.references.length : 0;
  const omniImageReferences = state.mode === "omni"
    ? state.references.filter((file) => file.type.startsWith("image/")).length
    : 0;
  const highPrecisionReferences = omniImageReferences > 0 && ui.refImageSize.value === "max";
  const heavy = relativeWork > 2 || omniReferences >= 2 || highPrecisionReferences;
  ui.weightWarning.classList.toggle("hidden", !heavy);
  if (!heavy) return;

  let severity = "高負荷設定です";
  if (relativeWork >= 6) severity = "かなり高負荷です";
  if (relativeWork >= 12 || (relativeWork >= 6 && omniReferences >= 2)) severity = "最大級の負荷です";
  ui.weightWarningTitle.textContent = `${severity}（出力側の概算 ×${relativeWork.toFixed(1)}）`;
  const referencePolicy = !omniImageReferences
    ? ""
    : ui.refImageSize.value === "max"
      ? " 高精度設定の画像はアップスケールせず、短辺2048pxを上限に解析するためmatchより重くなります。"
      : " 高速設定の画像は出力面積に合わせて縮小し、参照解析負荷を抑えます。";
  const referenceNote = omniReferences
    ? ` さらにOmni参照${omniReferences}件のQwen解析とattention負荷が加わります。${referencePolicy}`
    : "";
  ui.weightWarningText.textContent = `基準は960×544・約5秒・Draftです。これは画素数・長さ・denoise回数だけの相対目安です。${referenceNote}`;
}

function updatePlannerWarning() {
  const wantsCommunityPlanner = ui.promptProcessingMode.value === "community";
  const plannerReady = state.capabilities?.prompt_planner?.ready === true;
  const show = wantsCommunityPlanner && !plannerReady;
  ui.plannerWarning.classList.toggle("hidden", !show);
  ui.plannerWarning.textContent = show
    ? "公開成功例方式に必要なローカルQwenプロンプトモデルが未準備です。Setup-H3-Studioを再実行してください。モデルをH3と同時常駐させることはありません。"
    : "";
}

function syncPromptModeControls({ resetRawFields = false } = {}) {
  const raw = ui.promptProcessingMode.value === "raw_en";
  if (raw && resetRawFields) {
    setStyle("natural");
    ui.soundPreset.value = "auto";
    ui.musicPolicy.value = "auto";
    ui.dialogue.value = "";
    ui.dialogueCount.textContent = "0";
    ui.soundscape.value = "";
    ui.soundscapeCount.textContent = "0";
    ui.audioGain.value = "0";
    ui.soundSection.open = false;
  }

  $$("#style-row .style-chip").forEach((button) => {
    button.disabled = raw;
  });
  const soundControlsReady = state.capabilities?.audio_controls?.supported === true && !raw;
  [ui.soundPreset, ui.musicPolicy, ui.dialogue, ui.soundscape, ui.audioGain].forEach((element) => {
    element.disabled = !soundControlsReady;
  });
  ui.promptProcessingNote.textContent = raw
    ? "英語H3プロンプトを検証後、そのままnative Comfyへ渡します。スタイル・台詞・音響は別欄から追記されないため、必要な内容をこのプロンプト1本へ書いてください。"
    : "日本語の意図を短い英語Storyboardへローカル変換し、実際の台詞だけを普通の引用符内へ原文のまま残します。独自IR・<d>・tokenizerパッチは使いません。変換後の全文は生成後の詳細で確認できます。";
  updatePlannerWarning();
  updateSummary();
}

function validateForm() {
  const prompt = ui.prompt.value.trim();
  if (!prompt) return "プロンプトを入力してください。";
  if (!selectedResolutionPreset()) return "解像度設定を読み込めませんでした。ページを再読み込みしてください。";
  if (state.mode === "i2v" && !state.firstFile) return "開始画像を追加してください。";
  if (state.mode === "first_last" && (!state.firstFile || !state.lastFile)) return "開始画像と終了画像を追加してください。";
  if (state.mode === "omni" && !state.references.length) return "Omni参照素材を追加してください。";
  if (state.mode === "omni" && state.capabilities && !state.capabilities.modes.omni) return "Omniモデルはまだ準備中です。";
  return null;
}

async function submitJob() {
  const error = validateForm();
  ui.formError.textContent = error || "";
  if (error) return;

  const { width, height } = selectedResolutionPreset();
  const data = new FormData();
  data.append("mode", state.mode);
  data.append("style", state.style);
  // raw_en is an audit/pass-through mode, so preserve the textarea bytes.
  // Empty-input validation above may inspect trim(), but submission must not.
  data.append("prompt", ui.prompt.value);
  data.append("width", String(width));
  data.append("height", String(height));
  data.append("num_frames", ui.duration.value);
  data.append("steps", ui.quality.value);
  data.append("seed", ui.seed.value || "42");
  data.append("acceleration", ui.acceleration.value);
  data.append("ref_image_size", ui.refImageSize.value);
  data.append("prompt_processing_mode", ui.promptProcessingMode.value);
  data.append("standalone_audio_policy", ui.standaloneAudioPolicy.value);
  data.append("audio_preset", ui.soundPreset.value);
  data.append("dialogue", ui.dialogue.value.trim());
  data.append("soundscape", ui.soundscape.value.trim());
  data.append("music_policy", ui.musicPolicy.value);
  data.append("audio_gain_db", ui.audioGain.value);
  if (["i2v", "first_last"].includes(state.mode) && state.firstFile) data.append("first_image", state.firstFile);
  if (state.mode === "first_last" && state.lastFile) data.append("last_image", state.lastFile);
  if (state.mode === "omni") state.references.forEach((file) => data.append("references", file));

  ui.generate.disabled = true;
  ui.formError.textContent = "";
  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: localMutationHeaders,
      body: data,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "生成を開始できませんでした。");
    state.selectedJobId = payload.id;
    toast("生成キューへ追加しました。画面を閉じずにお待ちください。");
    await refreshState();
  } catch (errorValue) {
    ui.formError.textContent = errorValue.message;
  } finally {
    ui.generate.disabled = false;
  }
}

function updateSystem(snapshot, capabilities) {
  state.capabilities = capabilities;
  configureResolutionControls(capabilities.resolution);
  const ready = capabilities.fl2va;
  ui.modelDot.classList.toggle("ready", ready);
  ui.modelStatus.textContent = ready ? "FL2VA READY" : "MODEL MISSING";
  ui.omniLock.textContent = capabilities.ref2va ? "READY" : "Ref2VA準備中";
  ui.omniLock.classList.toggle("ready", capabilities.ref2va);
  updatePlannerWarning();
  const soundControlsReady = capabilities.audio_controls?.supported === true;
  ui.soundSection.classList.toggle("pending", !soundControlsReady);
  ui.soundStatus.textContent = soundControlsReady
    ? "日本語台詞は原文のまま1回だけ保持し、映像・カメラ・音響の英語制御文とは分離します。"
    : "現在の生成が終わったあと、NIKU H STUDIOサーバーを再起動すると音響コントロールが有効になります。";
  syncPromptModeControls();

  if (snapshot.gpu) {
    ui.gpuStatus.textContent = `GPU ${snapshot.gpu.utilization}% · ${Math.round(snapshot.gpu.memory_used_mib / 1024)}GB`;
  } else {
    ui.gpuStatus.textContent = "GPU —";
  }
  ui.ramStatus.textContent = `RAM ${snapshot.ram.available_gib}GB free`;
}

function showOnly(name) {
  const mapping = {
    empty: ui.emptyStage,
    progress: ui.progressStage,
    result: ui.resultStage,
    error: ui.errorStage,
  };
  Object.entries(mapping).forEach(([key, element]) => element.classList.toggle("hidden", key !== name));
}

function hasDetailValue(value) {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return Boolean(value.trim());
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}

function firstDetailValue(...values) {
  return values.find((value) => hasDetailValue(value));
}

function detailObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function compactDetailObject(entries) {
  return Object.fromEntries(Object.entries(entries).filter(([, value]) => hasDetailValue(value)));
}

function detailMessage(item) {
  if (!item || typeof item !== "object" || Array.isArray(item)) return null;
  const message = item.message ?? item.text ?? item.detail ?? item.description;
  if (!hasDetailValue(message)) return null;
  const label = item.level ?? item.severity ?? item.code ?? item.kind;
  return label ? `[${label}] ${message}` : String(message);
}

function formatDetailValue(value, preserveString = false) {
  if (typeof value === "string") return preserveString ? value : value.trim();
  if (Array.isArray(value)) {
    const messages = value.map((item) => detailMessage(item));
    if (messages.every(Boolean)) return messages.map((message) => `• ${message}`).join("\n");
    if (value.every((item) => ["string", "number", "boolean"].includes(typeof item))) {
      return value.map((item) => `• ${item}`).join("\n");
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function setDetailGroup(group, content, value, preserveString = false) {
  const visible = hasDetailValue(value);
  group.classList.toggle("hidden", !visible);
  content.textContent = visible ? formatDetailValue(value, preserveString) : "";
  if (!visible) group.open = false;
  return visible;
}

function compilerDetails(job) {
  const compiler = detailObject(job.compiler ?? job.prompt_processing);
  return {
    compiler,
    effectivePrompt: firstDetailValue(
      job.effective_prompt,
      job.compiled_prompt,
      compiler.effective_prompt,
      compiler.compiled_prompt,
      compiler.prompt,
    ),
    adjustments: firstDetailValue(
      job.auto_adjustments,
      compiler.auto_adjustments,
      compiler.adjustments,
    ),
  };
}

function renderJobDetails(job) {
  const terminal = job && !["queued", "running"].includes(job.status);
  ui.jobDetails.classList.toggle("hidden", !terminal);
  if (!terminal) {
    ui.jobDetailsDisclosure.open = false;
    state.jobDetailsSignature = "";
    return;
  }

  const { compiler, effectivePrompt, adjustments } = compilerDetails(job);
  const originalPrompt = firstDetailValue(job.original_prompt, job.prompt);
  const compilerDiagnostics = firstDetailValue(
    job.compiler_diagnostics,
    compiler.diagnostics,
  );
  const referenceInfo = firstDetailValue(
    job.reference_analysis,
    job.reference_map,
    compiler.reference_analysis,
    compiler.reference_map,
    compiler.references,
  );
  const compilerMetadata = compactDetailObject({
    status: compiler.status,
    processing_mode_requested: compiler.processing_mode_requested,
    processing_mode_effective: compiler.processing_mode_effective,
    workflow_profile: compiler.workflow_profile,
    prompt_cache_hit: compiler.prompt_cache_hit,
    prompt_cache_key: compiler.prompt_cache_key,
    model_repo: compiler.model_repo,
    model_revision: compiler.model_revision,
    worker_elapsed_ms: compiler.worker_elapsed_ms,
    local_only: compiler.local_only,
    model_inference: compiler.model_inference,
    mode: compiler.mode,
    compiler_metadata: compiler.compiler_metadata,
    provenance: compiler.provenance,
    diagnostics: compilerDiagnostics,
  });
  const references = compactDetailObject({
    compiler: compilerMetadata,
    references: referenceInfo,
    attachments: job.attachments,
    standalone_audio_policy_requested: job.standalone_audio_policy_requested,
    standalone_audio_policy_effective: job.standalone_audio_policy_effective,
    standalone_audio_conditioning: job.standalone_audio_conditioning,
    excluded_audio_reference_ids: job.excluded_audio_reference_ids,
  });
  const technical = compactDetailObject({
    job_id: job.id,
    status: job.status,
    backend: job.backend,
    attention_backend: job.attention_backend,
    mode: job.mode,
    resolution: job.width && job.height ? `${job.width}x${job.height}` : null,
    frames: job.num_frames,
    steps: job.steps,
    seed: job.seed,
    acceleration: job.acceleration ?? job.acceleration_mode,
    scheduler: job.scheduler,
    cache: job.cache,
    audio_output: job.audio_output,
    timings: job.timings,
    media: job.media,
    engine: detailObject(job.technical).engine,
    runtime_diagnostics: job.diagnostics,
  });
  const signature = JSON.stringify([
    job.id,
    job.finished_at,
    originalPrompt,
    effectivePrompt,
    adjustments,
    references,
    technical,
  ]);
  if (signature === state.jobDetailsSignature) return;

  const changedJob = ui.jobDetails.dataset.jobId !== job.id;
  if (changedJob) {
    ui.jobDetailsDisclosure.open = false;
    [
      ui.detailOriginalGroup,
      ui.detailEffectiveGroup,
      ui.detailAdjustmentsGroup,
      ui.detailReferenceGroup,
      ui.detailTechnicalGroup,
    ].forEach((group) => { group.open = false; });
  }
  ui.jobDetails.dataset.jobId = job.id;
  state.jobDetailsSignature = signature;

  const visibleLabels = [];
  if (setDetailGroup(ui.detailOriginalGroup, ui.detailOriginal, originalPrompt, true)) {
    visibleLabels.push("入力した原文");
  }
  if (setDetailGroup(ui.detailEffectiveGroup, ui.detailEffective, effectivePrompt, true)) {
    visibleLabels.push("実効プロンプト");
  }
  if (setDetailGroup(ui.detailAdjustmentsGroup, ui.detailAdjustments, adjustments)) {
    visibleLabels.push("自動調整");
  }
  if (setDetailGroup(ui.detailReferenceGroup, ui.detailReference, references)) {
    visibleLabels.push("参考情報");
  }
  if (setDetailGroup(ui.detailTechnicalGroup, ui.detailTechnical, technical)) {
    visibleLabels.push("技術情報");
  }
  ui.jobDetailsCaption.textContent = visibleLabels.length
    ? `${visibleLabels.join("・")}を必要なときだけ確認できます`
    : "このジョブには追加情報がありません";
}

function renderSelectedJob() {
  const job = selectedJob();
  if (!job) {
    ui.queueBadge.textContent = "待機中";
    showOnly("empty");
    renderJobDetails(null);
    return;
  }

  ui.queueBadge.textContent = statusLabels[job.status] || job.status;
  renderJobDetails(job);
  if (["queued", "running"].includes(job.status)) {
    showOnly("progress");
    const progress = displayedProgress(job);
    ui.progressRing.style.setProperty("--progress", `${progress.value * 3.6}deg`);
    const showDecimal = progress.estimated || !Number.isInteger(Number(job.progress));
    ui.progressNumber.textContent = `${showDecimal ? progress.value.toFixed(1) : Math.round(progress.value)}%`;
    ui.progressCaption.textContent = progress.estimated ? "ESTIMATE" : "PROGRESS";
    ui.progressPhase.textContent = job.phase || "準備しています";
    ui.progressMessage.textContent = job.message || "ローカルで処理しています。";
    ui.progressElapsed.textContent = formatElapsed(job.started_at);
    ui.progressStep.textContent = job.total_steps
      ? `${job.step || 0} / ${job.total_steps} steps${progress.estimated ? " · ステップ内推定" : ""}`
      : progress.estimated ? "段階内の推定進捗" : "モデル準備中";
    ui.cancelJob.disabled = !job.can_cancel;
    return;
  }

  if (job.status === "completed") {
    showOnly("result");
    if (ui.resultVideo.dataset.jobId !== job.id) {
      ui.resultVideo.src = `${job.result}?v=${encodeURIComponent(job.finished_at || Date.now())}`;
      ui.resultVideo.poster = job.preview || "";
      ui.resultVideo.dataset.jobId = job.id;
      ui.resultVideo.load();
    }
    ui.resultTitle.textContent = job.prompt.length > 70 ? `${job.prompt.slice(0, 70)}…` : job.prompt;
    ui.resultMeta.textContent = [
      `${job.width}×${job.height}`,
      `${job.num_frames} frames`,
      `${job.steps - 1} denoise`,
      "32 kHz stereo",
      ...jobAccelerationMeta(job),
    ].join(" · ");
    ui.downloadResult.href = job.result;
    return;
  }

  showOnly("error");
  ui.errorTitle.textContent = job.status === "cancelled" ? "生成をキャンセルしました" : "生成を完了できませんでした";
  ui.errorMessage.textContent = job.message || "詳細ログを確認してください。";
  ui.reuseErrorPrompt.disabled = !job.prompt;
  ui.logView.textContent = (job.logs || []).map((entry) => entry.text).join("\n");
  ui.logView.classList.add("hidden");
}

function createHistoryCard(job) {
  const card = document.createElement("article");
  card.className = "history-card";
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `${statusLabels[job.status] || job.status}: ${job.prompt}`);

  const thumb = document.createElement("div");
  thumb.className = "history-thumb";
  if (job.preview) {
    const image = document.createElement("img");
    image.loading = "lazy";
    image.src = job.preview;
    image.alt = "生成動画のプレビュー";
    thumb.append(image);
  }
  const badge = document.createElement("span");
  badge.className = `history-status ${job.status}`;
  badge.textContent = statusLabels[job.status] || job.status;
  thumb.append(badge);

  const copy = document.createElement("div");
  copy.className = "history-copy";
  const prompt = document.createElement("strong");
  prompt.textContent = job.prompt;
  const meta = document.createElement("small");
  meta.textContent = [
    `${job.width}×${job.height}`,
    `${job.steps - 1} steps`,
    ...jobAccelerationMeta(job),
  ].join(" · ");
  copy.append(prompt, meta);
  card.append(thumb, copy);

  const select = () => {
    state.selectedJobId = job.id;
    renderSelectedJob();
    $("#stage-card").scrollIntoView({ behavior: "smooth", block: "center" });
  };
  card.addEventListener("click", select);
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select();
    }
  });
  return card;
}

function renderHistory() {
  ui.historyGrid.replaceChildren();
  if (!state.jobs.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "生成履歴はまだありません。";
    ui.historyGrid.append(empty);
    return;
  }
  state.jobs.slice(0, 9).forEach((job) => ui.historyGrid.append(createHistoryCard(job)));
}

async function refreshState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error("status unavailable");
    const payload = await response.json();
    state.jobs = payload.jobs;
    updateSystem(payload.system, payload.capabilities);
    if (!state.selectedJobId) {
      const active = state.jobs.find((job) => ["queued", "running"].includes(job.status));
      state.selectedJobId = active?.id || state.jobs[0]?.id || null;
    }
    renderSelectedJob();
    renderHistory();
    renderPromptHistory();
  } catch {
    ui.modelDot.classList.remove("ready");
    ui.modelStatus.textContent = "接続を確認中";
  }
}

function resetForm() {
  setMode("t2v");
  setStyle("natural");
  setPromptValue("");
  ui.quality.value = "20";
  const resolutionCatalog = activeResolutionCatalog();
  setResolutionSelection(resolutionCatalog.default_aspect_ratio, resolutionCatalog.default_quality);
  ui.duration.value = "124";
  ui.seed.value = "42";
  ui.acceleration.value = "community";
  ui.refImageSize.value = "match";
  ui.promptProcessingMode.value = "community";
  ui.standaloneAudioPolicy.value = "dialogue_priority";
  updateStandaloneAudioPolicyNote();
  ui.soundPreset.value = "auto";
  ui.musicPolicy.value = "auto";
  ui.dialogue.value = "";
  ui.dialogueCount.textContent = "0";
  ui.soundscape.value = "";
  ui.soundscapeCount.textContent = "0";
  ui.audioGain.value = "0";
  ui.soundSection.open = false;
  setFrameFile("first", null);
  setFrameFile("last", null);
  state.references = [];
  renderReferences();
  syncPromptModeControls();
  ui.formError.textContent = "";
}

function reuseSelectedJob() {
  const job = selectedJob();
  if (!job) return;
  setMode(job.mode === "omni" && !state.capabilities?.modes.omni ? "t2v" : job.mode);
  setStyle(job.style);
  setPromptValue(job.prompt);
  ui.quality.value = String(job.steps);
  selectNearestResolution(job.width, job.height);
  ui.duration.value = String(job.num_frames);
  ui.seed.value = String(job.seed);
  ui.acceleration.value = job.acceleration || job.acceleration_mode || "off";
  ui.refImageSize.value = job.ref_image_size || "match";
  const audioPolicyMessage = restoreAudioPrompt(job);
  const referenceMessage = prepareReferencesForReuse(job);
  syncPromptModeControls();
  const messages = [referenceMessage, audioPolicyMessage].filter(Boolean);
  if (messages.length) toast(messages.join(" "), 9000);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function cancelSelectedJob() {
  const job = selectedJob();
  if (!job || !job.can_cancel) return;
  if (!window.confirm("現在の生成をキャンセルしますか？")) return;
  const response = await fetch(`/api/jobs/${job.id}/cancel`, {
    method: "POST",
    headers: localMutationHeaders,
  });
  if (response.ok) {
    toast("生成をキャンセルしました。");
    await refreshState();
  }
}

function wireUploadDrop(label, which) {
  ["dragenter", "dragover"].forEach((name) => label.addEventListener(name, (event) => {
    event.preventDefault();
    label.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach((name) => label.addEventListener(name, (event) => {
    event.preventDefault();
    label.classList.remove("dragging");
  }));
  label.addEventListener("drop", (event) => {
    const file = Array.from(event.dataTransfer.files).find((item) => item.type.startsWith("image/"));
    if (file) setFrameFile(which, file);
  });
}

function initializeEvents() {
  ui.modeGrid.addEventListener("click", (event) => {
    const button = event.target.closest(".mode-card");
    if (button) setMode(button.dataset.mode);
  });
  $("#style-row").addEventListener("click", (event) => {
    const button = event.target.closest(".style-chip");
    if (button) setStyle(button.dataset.style);
  });
  ui.prompt.addEventListener("input", () => {
    ui.promptCount.textContent = String(ui.prompt.value.length);
    ui.formError.textContent = "";
  });
  $("#sample-prompt").addEventListener("click", () => {
    setPromptValue(samples[state.mode], true);
  });
  ui.promptHistory.addEventListener("change", () => {
    ui.reusePrompt.disabled = !ui.promptHistory.value;
  });
  ui.reusePrompt.addEventListener("click", () => {
    reusePromptOnly(state.jobs.find((job) => job.id === ui.promptHistory.value));
  });
  ui.aspectRatio.addEventListener("change", () => {
    syncResolutionQualityOptions();
    updateSummary();
  });
  [ui.quality, ui.resolutionQuality, ui.duration, ui.acceleration, ui.refImageSize, ui.soundPreset, ui.musicPolicy].forEach((element) => element.addEventListener("change", updateSummary));
  ui.promptProcessingMode.addEventListener("change", () => {
    syncPromptModeControls({ resetRawFields: true });
    if (ui.promptProcessingMode.value === "raw_en") {
      toast("英語直結モードへ合わせ、別欄のスタイル・台詞・音響設定を自動に戻しました。");
    }
  });
  ui.standaloneAudioPolicy.addEventListener("change", () => {
    updateStandaloneAudioPolicyNote();
    updateSummary();
  });
  ui.dialogue.addEventListener("input", () => {
    ui.dialogueCount.textContent = String(ui.dialogue.value.length);
  });
  ui.soundscape.addEventListener("input", () => {
    ui.soundscapeCount.textContent = String(ui.soundscape.value.length);
  });
  ui.quickPreview.addEventListener("click", () => {
    ui.quality.value = "8";
    const aspectId = selectedResolutionAspect()?.id || activeResolutionCatalog().default_aspect_ratio;
    setResolutionSelection(aspectId, "preview");
    ui.duration.value = "124";
    updateSummary();
    const preview = selectedResolutionPreset();
    toast(`軽量プレビュー設定（${preview.width}×${preview.height}・約5秒・Draft）へ変更しました。`);
  });
  $("#random-seed").addEventListener("click", () => {
    ui.seed.value = String(Math.floor(Math.random() * 2_147_483_647));
  });
  ui.firstInput.addEventListener("change", () => setFrameFile("first", ui.firstInput.files[0] || null));
  ui.lastInput.addEventListener("change", () => setFrameFile("last", ui.lastInput.files[0] || null));
  ui.removeFirst.addEventListener("click", (event) => {
    event.preventDefault();
    setFrameFile("first", null);
    ui.firstInput.value = "";
  });
  ui.removeLast.addEventListener("click", (event) => {
    event.preventDefault();
    setFrameFile("last", null);
    ui.lastInput.value = "";
  });
  wireUploadDrop($("#first-upload-card"), "first");
  wireUploadDrop($("#last-upload-card"), "last");
  ui.referenceInput.addEventListener("change", () => {
    addReferences(ui.referenceInput.files);
    ui.referenceInput.value = "";
  });
  ["dragenter", "dragover"].forEach((name) => ui.referenceDropZone.addEventListener(name, (event) => {
    event.preventDefault();
    ui.referenceDropZone.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach((name) => ui.referenceDropZone.addEventListener(name, (event) => {
    event.preventDefault();
    ui.referenceDropZone.classList.remove("dragging");
  }));
  ui.referenceDropZone.addEventListener("drop", (event) => addReferences(event.dataTransfer.files));
  ui.generate.addEventListener("click", submitJob);
  $("#reset-form").addEventListener("click", resetForm);
  ui.cancelJob.addEventListener("click", cancelSelectedJob);
  ui.reuseJob.addEventListener("click", reuseSelectedJob);
  ui.reuseErrorPrompt.addEventListener("click", () => reusePromptOnly(selectedJob()));
  ui.showLog.addEventListener("click", () => ui.logView.classList.toggle("hidden"));
  $("#refresh-history").addEventListener("click", refreshState);
  $("#open-folder").addEventListener("click", async () => {
    const response = await fetch("/api/open-output-folder", {
      method: "POST",
      headers: localMutationHeaders,
    });
    if (!response.ok) toast("生成フォルダを開けませんでした。");
  });
}

async function initialize() {
  initializeEvents();
  setMode("t2v");
  setStyle("natural");
  updateSummary();
  await refreshState();
  state.pollTimer = setInterval(refreshState, 1500);
}

initialize();
