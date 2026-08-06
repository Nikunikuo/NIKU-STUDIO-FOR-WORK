"use client";

import {
  CSSProperties,
  FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  StoryAssetCategory,
  StoryCreateAssetFields,
  StoryCreateProjectRequest,
  StoryProjectSummary,
  StorySessionBinding,
  StorySidecarClient,
  StorySidecarHealth,
} from "@/lib/story/sidecar-client";

type WorkspaceView = "overview" | "script" | "world" | "production";
type AssetKind = StoryAssetCategory;
type MediaKind = "image" | "video" | "audio" | "none";
type TakeState = "candidate" | "selected" | "rendering" | "rejected";

type ResolutionPreset = {
  width: number;
  height: number;
  label: string;
};

type ResolutionAspect = {
  id: string;
  label: string;
  presets: Record<string, ResolutionPreset>;
};

type ResolutionCatalog = {
  defaultAspectRatio: string;
  defaultQuality: string;
  qualities: Array<{ id: string; label: string }>;
  aspects: ResolutionAspect[];
};

type GenerationSettings = {
  style: "natural" | "cinematic" | "photoreal" | "anime" | "illustration" | "product" | "moody";
  aspectRatio: string;
  resolutionQuality: string;
  durationFrames: 124 | 243 | 345;
  steps: number;
  seed: number;
  acceleration: "off" | "community" | "conservative" | "balanced";
  refImageSize: "match" | "max";
  promptProcessingMode: "community" | "raw_en";
  standaloneAudioPolicy: "dialogue_priority" | "full_content";
  audioPreset: "auto" | "dialogue" | "ambience" | "effects" | "music" | "quiet";
  musicPolicy: "auto" | "none" | "subtle" | "prominent";
  audioGainDb: number;
  dialogue: string;
  soundscape: string;
};

type Project = {
  id: string;
  code: string;
  title: string;
  subtitle: string;
  revision: number;
  phase: string;
  progress: number;
  accent: string;
  updated: string;
  episodeTitle: string;
  sceneTitle: string;
  logline: string;
  summary: string;
  projectRoot?: string;
  thumbnailUrl?: string;
};

type Shot = {
  id: string;
  label: string;
  title: string;
  status: "selected" | "active" | "draft" | "approved" | "planned";
  order?: number;
  episodeId?: string;
  sceneId?: string;
  storyPurpose?: string;
  performance?: string;
  action?: string;
  dialogue?: string;
  soundscape?: string;
  musicPolicy?: string;
  camera?: string;
  durationFrames?: 124 | 243 | 345;
  rawPayload?: Readonly<Record<string, unknown>>;
};

type Asset = {
  id: string;
  kind: AssetKind;
  name: string;
  role: string;
  caption: string;
  promptCaption: string;
  locked: boolean;
  tone: string;
  mediaKind?: MediaKind;
  contentUrl?: string;
  originalFileName?: string;
  rawPayload?: Readonly<Record<string, unknown>>;
};

type ProjectCreateDraft = StoryCreateProjectRequest;

type AssetEditorDraft = StoryCreateAssetFields & {
  readonly file: File | null;
};

type Take = {
  id: string;
  label: string;
  state: TakeState;
  score: number | null;
  note: string;
  seed: number;
  tone: string;
  shotId?: string;
  artifactPath?: string;
  artifactUrl?: string;
};

type Message = {
  id: number;
  role: "codex" | "user" | "system";
  text: string;
  meta?: string;
};

const NAV_ITEMS: Array<{ id: WorkspaceView; label: string; hint: string }> = [
  { id: "overview", label: "概要", hint: "準備 4/5" },
  { id: "script", label: "脚本", hint: "12 shots" },
  { id: "world", label: "World Bible", hint: "4 assets" },
  { id: "production", label: "動画制作", hint: "3 takes" },
];

const EMPTY_TAKE: Take = {
  id: "no_take",
  label: "TAKE 未生成",
  state: "candidate",
  score: null,
  note: "H3で生成すると、このProjectとShotに紐づいたTakeがここへ追加されます。",
  seed: 0,
  tone: "take-blue",
};

const EMPTY_PROJECT: Project = {
  id: "",
  code: "PROJECT",
  title: "Projectを読み込み中",
  subtitle: "Project Coreへ接続しています",
  revision: 0,
  phase: "未接続",
  progress: 0,
  accent: "#65cdb9",
  updated: "—",
  episodeTitle: "Episode未設定",
  sceneTitle: "Scene未設定",
  logline: "Project Coreから物語を読み込んでいます。",
  summary: "",
};

const INITIAL_PROJECT_DRAFT: ProjectCreateDraft = {
  title: "",
  code: "",
  subtitle: "短編・ローカルH3制作",
  episodeTitle: "第1話",
  sceneTitle: "Scene 01",
  shotTitle: "Shot 01",
  logline: "",
  summary: "",
};

const INITIAL_ASSET_DRAFT: AssetEditorDraft = {
  name: "",
  category: "reference",
  role: "参照素材",
  caption: "",
  promptCaption: "",
  locked: false,
  source: "human",
  file: null,
};

const DEFAULT_GENERATION_SETTINGS: GenerationSettings = {
  style: "anime",
  aspectRatio: "16:9",
  resolutionQuality: "sd",
  durationFrames: 124,
  steps: 20,
  seed: 42,
  acceleration: "community",
  refImageSize: "match",
  promptProcessingMode: "community",
  standaloneAudioPolicy: "dialogue_priority",
  audioPreset: "auto",
  musicPolicy: "auto",
  audioGainDb: 0,
  dialogue: "",
  soundscape: "",
};

const STORY_SIDECAR = new StorySidecarClient({ timeoutMilliseconds: 5000 });

// The integrated distribution keeps Project Core data below this repository's
// Drama Studio directory. H3 protocol identifiers remain unchanged.
const INTEGRATED_STORY_ROOT = "C:\\PROJECT_HUB\\NIKU-STUDIO-FOR-WORK\\drama-studio";

const DEFAULT_RESOLUTION_CATALOG: ResolutionCatalog = {
  defaultAspectRatio: "16:9",
  defaultQuality: "sd",
  qualities: [
    { id: "preview", label: "Preview" },
    { id: "sd", label: "SD 480p" },
    { id: "hd", label: "HD 720p" },
    { id: "native", label: "Native 768p" },
  ],
  aspects: [
    { id: "16:9", label: "16:9", presets: { preview: { width: 672, height: 384, label: "672×384" }, sd: { width: 864, height: 480, label: "864×480" }, hd: { width: 1312, height: 736, label: "1312×736" }, native: { width: 1344, height: 768, label: "1344×768" } } },
    { id: "9:16", label: "9:16", presets: { preview: { width: 384, height: 672, label: "384×672" }, sd: { width: 480, height: 864, label: "480×864" }, hd: { width: 736, height: 1312, label: "736×1312" }, native: { width: 768, height: 1344, label: "768×1344" } } },
    { id: "1:1", label: "1:1", presets: { preview: { width: 384, height: 384, label: "384×384" }, sd: { width: 480, height: 480, label: "480×480" }, hd: { width: 736, height: 736, label: "736×736" }, native: { width: 768, height: 768, label: "768×768" } } },
    { id: "4:3", label: "4:3", presets: { preview: { width: 512, height: 384, label: "512×384" }, sd: { width: 640, height: 480, label: "640×480" }, hd: { width: 896, height: 672, label: "896×672" }, native: { width: 1024, height: 768, label: "1024×768" } } },
    { id: "3:4", label: "3:4", presets: { preview: { width: 384, height: 512, label: "384×512" }, sd: { width: 480, height: 640, label: "480×640" }, hd: { width: 672, height: 896, label: "672×896" }, native: { width: 768, height: 1024, label: "768×1024" } } },
  ],
};

function normalizeResolutionCatalog(value: unknown): ResolutionCatalog {
  const source = asRecord(value);
  const rawQualities = Array.isArray(source?.qualities) ? source.qualities : [];
  const qualities = rawQualities.flatMap((item) => {
    const record = asRecord(item);
    return typeof record?.id === "string" && typeof record.label === "string"
      ? [{ id: record.id, label: record.label }]
      : [];
  });
  const rawAspects = Array.isArray(source?.aspectRatios)
    ? source.aspectRatios
    : Array.isArray(source?.aspect_ratios) ? source.aspect_ratios : [];
  const aspects = rawAspects.flatMap((item) => {
    const record = asRecord(item);
    const rawPresets = asRecord(record?.presets);
    if (typeof record?.id !== "string" || typeof record.label !== "string" || !rawPresets) return [];
    const presets: Record<string, ResolutionPreset> = {};
    for (const [id, rawPreset] of Object.entries(rawPresets)) {
      const preset = asRecord(rawPreset);
      if (typeof preset?.width === "number" && typeof preset.height === "number") {
        presets[id] = {
          width: preset.width,
          height: preset.height,
          label: typeof preset.label === "string" ? preset.label : `${preset.width}×${preset.height}`,
        };
      }
    }
    return Object.keys(presets).length > 0
      ? [{ id: record.id, label: record.label, presets }]
      : [];
  });
  if (!qualities.length || !aspects.length) return DEFAULT_RESOLUTION_CATALOG;
  const defaultAspectRatio = typeof source?.defaultAspectRatio === "string"
    ? source.defaultAspectRatio
    : typeof source?.default_aspect_ratio === "string" ? source.default_aspect_ratio : aspects[0].id;
  const defaultQuality = typeof source?.defaultQuality === "string"
    ? source.defaultQuality
    : typeof source?.default_quality === "string" ? source.default_quality : qualities[0].id;
  return { defaultAspectRatio, defaultQuality, qualities, aspects };
}

type StoryWireEntity = {
  readonly entityType: string;
  readonly entityId: string;
  readonly revision: number;
  readonly payload: Readonly<Record<string, unknown>>;
};

function asRecord(value: unknown): Readonly<Record<string, unknown>> | null {
  return typeof value === "object" && value !== null
    ? value as Readonly<Record<string, unknown>>
    : null;
}

function contextEntities(context: Readonly<Record<string, unknown>>): StoryWireEntity[] {
  if (!Array.isArray(context.entities)) return [];
  return context.entities.flatMap((value) => {
    const entity = asRecord(value);
    const payload = asRecord(entity?.payload);
    if (!entity || !payload || typeof entity.entityType !== "string" || typeof entity.entityId !== "string") {
      return [];
    }
    const revision = typeof entity.revision === "number" && Number.isInteger(entity.revision)
      ? entity.revision
      : 0;
    return [{ entityType: entity.entityType, entityId: entity.entityId, revision, payload }];
  });
}

function camelToSnakeKey(value: string): string {
  return value.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
}

function canonicalizePayload(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicalizePayload);
  const record = asRecord(value);
  if (!record) return value;
  return Object.fromEntries(
    Object.entries(record).map(([key, item]) => [camelToSnakeKey(key), canonicalizePayload(item)]),
  );
}

function metadataString(
  metadata: Readonly<Record<string, unknown>> | undefined,
  key: string,
  fallback: string,
): string {
  return typeof metadata?.[key] === "string" ? String(metadata[key]) : fallback;
}

function projectFromSummary(summary: StoryProjectSummary): Project {
  const metadata = summary.metadata;
  return {
    id: summary.projectId,
    code: metadataString(metadata, "code", summary.code || summary.projectId),
    title: summary.title || summary.name || "無題のProject",
    subtitle: metadataString(metadata, "subtitle", "ローカル物語Project"),
    revision: summary.revision,
    phase: metadataString(metadata, "phase", "企画・脚本"),
    progress: typeof metadata?.progress === "number" ? metadata.progress : 0,
    accent: metadataString(metadata, "accent", "#65cdb9"),
    updated: summary.updatedAt ?? "たった今",
    episodeTitle: metadataString(metadata, "episodeTitle", "未設定エピソード"),
    sceneTitle: metadataString(metadata, "sceneTitle", "未設定シーン"),
    logline: metadataString(metadata, "logline", "ログラインを入力してください。"),
    summary: metadataString(metadata, "summary", "概要と脚本を編集して物語を組み立てます。"),
    projectRoot: summary.projectRoot,
  };
}

function projectFromContext(
  current: Project,
  context: Readonly<Record<string, unknown>>,
  entities: readonly StoryWireEntity[],
): Project {
  const focus = asRecord(context.focus);
  const episodeId = typeof focus?.episodeId === "string" ? focus.episodeId : null;
  const sceneId = typeof focus?.sceneId === "string" ? focus.sceneId : null;
  const episode = entities.find((entity) => entity.entityType === "episode" && (!episodeId || entity.entityId === episodeId));
  const scene = entities.find((entity) => entity.entityType === "scene" && (!sceneId || entity.entityId === sceneId));
  return {
    ...current,
    title: typeof context.projectName === "string" ? context.projectName : current.title,
    revision: typeof context.projectRevision === "number"
      ? context.projectRevision
      : typeof context.currentProjectRevision === "number"
        ? context.currentProjectRevision
        : current.revision,
    episodeTitle: typeof episode?.payload.title === "string" ? episode.payload.title : current.episodeTitle,
    sceneTitle: typeof scene?.payload.title === "string" ? scene.payload.title : current.sceneTitle,
    logline: typeof episode?.payload.logline === "string" ? episode.payload.logline : current.logline,
    summary: typeof episode?.payload.summary === "string"
      ? episode.payload.summary
      : typeof scene?.payload.summary === "string"
        ? scene.payload.summary
        : current.summary,
    subtitle: typeof episode?.payload.subtitle === "string" ? episode.payload.subtitle : current.subtitle,
    projectRoot: typeof context.projectRoot === "string" ? context.projectRoot : current.projectRoot,
  };
}

function shotsFromContext(
  entities: readonly StoryWireEntity[],
  focusedSceneId: string | null,
): Shot[] {
  return entities
    .filter((entity) => entity.entityType === "shot" && (
      !focusedSceneId || entity.payload.sceneId === focusedSceneId
    ))
    .map((entity): Shot => {
      const payload = entity.payload;
      const order = Number(payload.order);
      const duration = Number(payload.durationFrames);
      const rawStatus = String(payload.status ?? "draft");
      const status: Shot["status"] = ["selected", "active", "approved", "planned"].includes(rawStatus)
        ? rawStatus as Shot["status"]
        : "draft";
      return {
        id: entity.entityId,
        label: typeof payload.label === "string" ? payload.label : typeof payload.code === "string" ? payload.code : entity.entityId,
        title: typeof payload.title === "string" ? payload.title : "無題のShot",
        status,
        order: Number.isFinite(order) ? order : 0,
        episodeId: typeof payload.episodeId === "string" ? payload.episodeId : "",
        sceneId: typeof payload.sceneId === "string" ? payload.sceneId : "",
        storyPurpose: typeof payload.storyPurpose === "string" ? payload.storyPurpose : typeof payload.intent === "string" ? payload.intent : "",
        performance: typeof payload.performance === "string" ? payload.performance : typeof payload.acting === "string" ? payload.acting : "",
        action: typeof payload.action === "string" ? payload.action : typeof payload.description === "string" ? payload.description : "",
        dialogue: typeof payload.dialogue === "string" ? payload.dialogue : "",
        soundscape: typeof payload.soundscape === "string" ? payload.soundscape : "",
        musicPolicy: typeof payload.musicPolicy === "string"
          ? payload.musicPolicy
          : typeof payload.music_policy === "string"
            ? payload.music_policy
            : "auto",
        camera: typeof payload.camera === "string" ? payload.camera : "",
        durationFrames: [124, 243, 345].includes(duration) ? duration as 124 | 243 | 345 : 124,
        rawPayload: payload,
      };
    })
    .sort((left, right) => (left.order ?? 0) - (right.order ?? 0) || left.id.localeCompare(right.id));
}

function assetsFromContext(
  entities: readonly StoryWireEntity[],
  fallback: readonly Asset[],
  projectId?: string,
  sessionId?: string,
): Asset[] {
  const assets = entities.flatMap((entity) => {
    const { payload } = entity;
    if (
      entity.entityType !== "asset" ||
      payload.role === "generated_take" ||
      typeof payload.name !== "string"
    ) {
      return [];
    }
    return [assetFromPayload(entity.entityId, payload, projectId, sessionId)];
  });
  return assets.length > 0 ? assets : fallback.map((asset) => ({ ...asset }));
}

function assetFromPayload(
  assetId: string,
  payload: Readonly<Record<string, unknown>>,
  projectId?: string,
  sessionId?: string,
): Asset {
  const rawKind = String(payload.category ?? payload.kind ?? "reference");
  const kind: AssetKind = ["character", "environment", "prop", "reference"].includes(rawKind)
    ? rawKind as AssetKind
    : "reference";
  const rawMediaKind = String(payload.mediaKind ?? (["image", "video", "audio"].includes(String(payload.kind)) ? payload.kind : "none"));
  const mediaKind: MediaKind = ["image", "video", "audio"].includes(rawMediaKind)
    ? rawMediaKind as MediaKind
    : "none";
  const hasContent = mediaKind !== "none" && projectId && sessionId && typeof payload.relativePath === "string";
  return {
    id: assetId,
    kind,
    name: typeof payload.name === "string" ? payload.name : assetId,
    role: typeof payload.role === "string" ? payload.role : "参照素材",
    caption: typeof payload.caption === "string" ? payload.caption : "",
    promptCaption: typeof payload.promptCaption === "string" ? payload.promptCaption : "",
    locked: payload.locked === true,
    tone: typeof payload.tone === "string" ? payload.tone : "steel",
    mediaKind,
    contentUrl: hasContent ? STORY_SIDECAR.assetContentUrl(projectId, assetId, sessionId) : undefined,
    originalFileName: typeof payload.originalFileName === "string" ? payload.originalFileName : undefined,
    rawPayload: payload,
  };
}

function promptFromContext(
  entities: readonly StoryWireEntity[],
  focusedPromptId: string | null,
  focusedShotId: string,
  fallback: string,
): string {
  const entity = entities.find((candidate) => (
    candidate.entityType === "prompt" &&
    (candidate.entityId === focusedPromptId || candidate.payload.shotId === focusedShotId)
  ));
  return typeof entity?.payload.prompt === "string" ? entity.payload.prompt : fallback;
}

function takesFromContext(
  entities: readonly StoryWireEntity[],
  focusedShotId: string,
  fallback: readonly Take[],
  projectId: string,
  sessionId: string,
): { readonly takes: Take[]; readonly selectedTakeId: string } {
  const jobsByTake = new Map<string, StoryWireEntity>();
  for (const entity of entities) {
    if (entity.entityType === "job" && typeof entity.payload.takeId === "string") {
      jobsByTake.set(entity.payload.takeId, entity);
    }
  }

  const realTakes = entities
    .filter((entity) => entity.entityType === "take" && entity.payload.shotId === focusedShotId)
    .sort((left, right) => left.revision - right.revision || left.entityId.localeCompare(right.entityId))
    .map((entity, index) => takeFromEntity(entity, index, fallback.length, jobsByTake, projectId, sessionId));

  const selectedRealTake = realTakes.find((take) => take.state === "selected");
  const fixtureTakes = fallback.map((take) => ({
    ...take,
    state: selectedRealTake && take.state === "selected" ? "candidate" as const : take.state,
  }));
  const takes = [...fixtureTakes, ...realTakes];
  const selectedTakeId = selectedRealTake?.id
    ?? realTakes.at(-1)?.id
    ?? fixtureTakes.find((take) => take.state === "selected")?.id
    ?? fixtureTakes[0]?.id
    ?? "";
  return { takes, selectedTakeId };
}

function takeFromEntity(
  entity: StoryWireEntity,
  index: number,
  labelOffset: number,
  jobsByTake: ReadonlyMap<string, StoryWireEntity>,
  projectId: string,
  sessionId: string,
): Take {
  const job = jobsByTake.get(entity.entityId);
  const request = asRecord(job?.payload.request);
  const fields = asRecord(request?.fields);
  const parsedSeed = Number(fields?.seed);
  const status = String(entity.payload.status ?? "ready");
  const selection = String(entity.payload.selection ?? "candidate");
  const state: TakeState = selection === "selected"
    ? "selected"
    : ["queued", "running", "processing"].includes(status)
      ? "rendering"
      : ["failed", "cancelled", "interrupted"].includes(status)
        ? "rejected"
        : "candidate";
  const artifactPath = typeof entity.payload.artifactRelativePath === "string"
    ? entity.payload.artifactRelativePath
    : undefined;
  const artifactId = typeof entity.payload.artifactId === "string"
    ? entity.payload.artifactId
    : undefined;
  return {
    id: entity.entityId,
    label: `TAKE ${String(labelOffset + index + 1).padStart(3, "0")}`,
    state,
    score: state === "candidate" || state === "selected" ? 88 : null,
    note: artifactPath ? `実H3生成完了 · ${artifactPath}` : `Project Coreから復元 · ${status}`,
    seed: Number.isFinite(parsedSeed) ? parsedSeed : 0,
    tone: "take-green",
    shotId: typeof entity.payload.shotId === "string" ? entity.payload.shotId : undefined,
    artifactPath,
    artifactUrl: artifactId
      ? `${STORY_SIDECAR.origin}/api/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(artifactId)}/content?sessionId=${encodeURIComponent(sessionId)}`
      : undefined,
  };
}

function selectedTakesFromContext(
  entities: readonly StoryWireEntity[],
  projectId: string,
  sessionId: string,
): Record<string, Take> {
  const jobsByTake = new Map<string, StoryWireEntity>();
  for (const entity of entities) {
    if (entity.entityType === "job" && typeof entity.payload.takeId === "string") {
      jobsByTake.set(entity.payload.takeId, entity);
    }
  }
  const selected = entities
    .filter((entity) => entity.entityType === "take" && entity.payload.selection === "selected" && entity.payload.status === "ready")
    .sort((left, right) => left.revision - right.revision || left.entityId.localeCompare(right.entityId));
  const result: Record<string, Take> = {};
  for (const [index, entity] of selected.entries()) {
    const shotId = typeof entity.payload.shotId === "string" ? entity.payload.shotId : "";
    if (!shotId) continue;
    result[shotId] = takeFromEntity(entity, index, 0, jobsByTake, projectId, sessionId);
  }
  return result;
}

function endFrameAssetsFromContext(entities: readonly StoryWireEntity[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const entity of entities) {
    if (entity.entityType !== "take") continue;
    const shotId = typeof entity.payload.shotId === "string" ? entity.payload.shotId : "";
    const assetId = typeof entity.payload.endFrameAssetId === "string" ? entity.payload.endFrameAssetId : "";
    if (shotId && assetId) result[shotId] = assetId;
  }
  return result;
}

function stateLabel(state: TakeState) {
  switch (state) {
    case "selected":
      return "採用";
    case "rendering":
      return "生成中";
    case "rejected":
      return "却下";
    default:
      return "候補";
  }
}

function assetKindLabel(kind: AssetKind) {
  return kind === "character" ? "人物" : kind === "environment" ? "舞台" : kind === "prop" ? "小道具" : "参照素材";
}

function safeDownloadName(value: string): string {
  const normalized = value.trim().replace(/[^a-zA-Z0-9._-]+/g, "_");
  return normalized || "h3-take";
}

async function downloadMediaToBrowser(url: string, filename: string): Promise<void> {
  const response = await fetch(url, { credentials: "omit" });
  if (!response.ok) throw new Error(`download failed (${response.status})`);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectId] = useState(() => (
    typeof window === "undefined" ? "" : window.localStorage.getItem("h3story.activeProjectId") ?? ""
  ));
  const [view, setView] = useState<WorkspaceView>("overview");
  const [selectedShotId, setSelectedShotId] = useState("");
  const [selectedPromptId, setSelectedPromptId] = useState("");
  const [selectedAssetId, setSelectedAssetId] = useState("");
  const [selectedTakeId, setSelectedTakeId] = useState("");
  const [selectedTakesByShot, setSelectedTakesByShot] = useState<Record<string, Take>>({});
  const [shots, setShots] = useState<Shot[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [takes, setTakes] = useState<Take[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [prompt, setPrompt] = useState("");
  const [projectLauncherOpen, setProjectLauncherOpen] = useState(false);
  const [projectCreateOpen, setProjectCreateOpen] = useState(false);
  const [projectCreateDraft, setProjectCreateDraft] = useState<ProjectCreateDraft>(INITIAL_PROJECT_DRAFT);
  const [projectCreateSubmitting, setProjectCreateSubmitting] = useState(false);
  const [assetEditorOpen, setAssetEditorOpen] = useState(false);
  const [assetEditorMode, setAssetEditorMode] = useState<"create" | "edit">("create");
  const [assetEditorDraft, setAssetEditorDraft] = useState<AssetEditorDraft>(INITIAL_ASSET_DRAFT);
  const [assetEditorSubmitting, setAssetEditorSubmitting] = useState(false);
  const [scriptEditing, setScriptEditing] = useState(false);
  const [scriptDraft, setScriptDraft] = useState<Shot | null>(null);
  const [scriptSubmitting, setScriptSubmitting] = useState(false);
  const [handoffOpen, setHandoffOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [sidecarHealth, setSidecarHealth] = useState<StorySidecarHealth | null>(null);
  const [h3Capabilities, setH3Capabilities] = useState<Readonly<Record<string, unknown>> | null>(null);
  const [sessionBinding, setSessionBinding] = useState<StorySessionBinding | null>(null);
  const [issuedHandoffPath, setIssuedHandoffPath] = useState<string | null>(null);
  const [renderSubmitting, setRenderSubmitting] = useState(false);
  const [carryOverEndFrame, setCarryOverEndFrame] = useState(true);
  const [endFrameAssetByShot, setEndFrameAssetByShot] = useState<Record<string, string>>({});
  const [generationSettings, setGenerationSettings] = useState<GenerationSettings>(DEFAULT_GENERATION_SETTINGS);
  const [dirtyAssetIds, setDirtyAssetIds] = useState<Set<string>>(() => new Set());
  const [promptDirty, setPromptDirty] = useState(false);
  const renderRunToken = useRef(0);

  const project = useMemo(
    () => projects.find((item) => item.id === activeProjectId) ?? projects[0] ?? EMPTY_PROJECT,
    [activeProjectId, projects],
  );
  const selectedAsset = assets.find((item) => item.id === selectedAssetId) ?? assets[0] ?? null;
  const selectedTake = takes.find((item) => item.id === selectedTakeId) ?? takes[0] ?? EMPTY_TAKE;
  const selectedShot = shots.find((item) => item.id === selectedShotId) ?? shots[0] ?? {
    id: "sh_001",
    label: "Shot 01",
    title: "未設定Shot",
    status: "draft" as const,
  };
  const rendering = renderSubmitting || takes.some((item) => item.state === "rendering");
  const activeRevision = sessionBinding?.boundRevision ?? project.revision;
  const resolutionCatalog = useMemo(
    () => normalizeResolutionCatalog(h3Capabilities?.resolution),
    [h3Capabilities],
  );
  const handoffSlug = project.code.toLowerCase();
  const projectRootForHandoff = project.projectRoot?.replaceAll("/", "\\") ?? `projects\\${handoffSlug}`;
  const handoffPath = `${INTEGRATED_STORY_ROOT}\\${projectRootForHandoff}\\.h3story\\handoffs\\hof_${handoffSlug}_${activeRevision}.json`;
  const visibleHandoffPath = issuedHandoffPath ?? handoffPath;

  useEffect(() => {
    setGenerationSettings((current) => ({
      ...current,
      durationFrames: selectedShot.durationFrames ?? current.durationFrames,
      dialogue: selectedShot.dialogue ?? "",
      soundscape: selectedShot.soundscape ?? "",
      musicPolicy: (selectedShot.musicPolicy as GenerationSettings["musicPolicy"] | undefined) ?? current.musicPolicy,
    }));
  }, [selectedShot.dialogue, selectedShot.durationFrames, selectedShot.id, selectedShot.musicPolicy, selectedShot.soundscape]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 2600);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    if (activeProjectId) window.localStorage.setItem("h3story.activeProjectId", activeProjectId);
  }, [activeProjectId]);

  useEffect(() => {
    let active = true;

    async function bindProjectContext() {
      try {
        const health = await STORY_SIDECAR.health();
        if (!active) return;
        setSidecarHealth(health);
        try {
          const h3State = await STORY_SIDECAR.getH3State();
          const capabilities = asRecord(h3State.capabilities);
          if (active && capabilities) setH3Capabilities(capabilities);
        } catch {
          if (active) setH3Capabilities(null);
        }
        if (health.projectCore !== "ready") return;
        const summaries = await STORY_SIDECAR.listProjects();
        if (!active) return;
        const registry = summaries.map(projectFromSummary);
        setProjects(registry);
        const nextProjectId = registry.some((candidate) => candidate.id === activeProjectId)
          ? activeProjectId
          : registry[0]?.id ?? "";
        if (!nextProjectId) {
          setSessionBinding(null);
          setShots([]);
          setAssets([]);
          setTakes([]);
          setSelectedTakesByShot({});
          setPrompt("");
          setMessages([]);
          return;
        }
        if (nextProjectId !== activeProjectId) {
          setActiveProjectId(nextProjectId);
          return;
        }
        const binding = await STORY_SIDECAR.activateProject(
          nextProjectId,
          "codex-web-ui",
        );
        if (!active) return;
        const context = await STORY_SIDECAR.getContext(nextProjectId, binding.sessionId);
        if (!active) return;
        const entities = contextEntities(context);
        setEndFrameAssetByShot(endFrameAssetsFromContext(entities));
        const contextFocus = asRecord(context.focus);
        const focusedSceneId = typeof contextFocus?.sceneId === "string" ? contextFocus.sceneId : null;
        const projectShots = shotsFromContext(entities, focusedSceneId);
        const fallbackShotId = projectShots[0]?.id ?? "";
        const focusedShotId = typeof contextFocus?.shotId === "string" && projectShots.some((shot) => shot.id === contextFocus.shotId)
          ? contextFocus.shotId
          : fallbackShotId;
        const focusedPromptId = typeof contextFocus?.promptId === "string" ? contextFocus.promptId : null;
        const hydratedAssets = assetsFromContext(entities, [], nextProjectId, binding.sessionId);
        const hydratedTakes = takesFromContext(
          entities,
          focusedShotId,
          [],
          nextProjectId,
          binding.sessionId,
        );
        const registryProject = registry.find((candidate) => candidate.id === nextProjectId) ?? EMPTY_PROJECT;
        const thumbnail = hydratedAssets.find((asset) => (
          asset.kind === "reference" && asset.mediaKind === "image" && (
            asset.name === "プロジェクトサムネイル" || asset.role.includes("プロジェクトサムネイル")
          )
        ));
        const boundProject = {
          ...projectFromContext(registryProject, context, entities),
          thumbnailUrl: thumbnail?.contentUrl,
        };
        setProjects((current) => current.map((candidate) => candidate.id === boundProject.id ? boundProject : candidate));
        setShots(projectShots);
        setAssets(hydratedAssets);
        setSelectedAssetId(hydratedAssets[0]?.id ?? "");
        setSelectedShotId(focusedShotId);
        setSelectedPromptId(focusedPromptId ?? "");
        setPrompt(promptFromContext(
          entities,
          focusedPromptId,
          focusedShotId,
          "",
        ));
        setTakes(hydratedTakes.takes);
        setSelectedTakeId(hydratedTakes.selectedTakeId);
        setSelectedTakesByShot(selectedTakesFromContext(entities, nextProjectId, binding.sessionId));
        const focusedShot = projectShots.find((candidate) => candidate.id === focusedShotId) ?? projectShots[0];
        const focusedTake = hydratedTakes.takes.find((candidate) => candidate.id === hydratedTakes.selectedTakeId);
        const pendingRequestMessages: Message[] = entities
          .filter((entity) => entity.entityType === "agent_request" && entity.payload.status === "pending" && typeof entity.payload.request === "string")
          .flatMap((entity, index) => ([
            {
              id: binding.boundRevision * 1000 + 100 + index * 2,
              role: "user" as const,
              text: String(entity.payload.request),
              meta: "Project Core inbox",
            },
            {
              id: binding.boundRevision * 1000 + 101 + index * 2,
              role: "codex" as const,
              text: "この依頼はProject Coreに保存済みです。Codexで続けると、同じProject・Revision・Focusから処理を再開できます。",
              meta: `queued · ${entity.entityId}`,
            },
          ]));
        setMessages([
          {
            id: binding.boundRevision * 10,
            role: "system",
            text: "Project Manifest と Context Snapshot を照合しました。",
            meta: `revision ${binding.boundRevision} · hash verified`,
          },
          {
            id: binding.boundRevision * 10 + 1,
            role: "codex",
            text: `『${boundProject.title}』を開きました。現在地は ${focusedShot?.label ?? "Shot未設定"}。${focusedTake?.label ?? "採用Take未設定"}${focusedTake ? (focusedTake.state === "selected" ? "が採用中です。" : "を確認できます。") : "です。"}`,
            meta: "H3STORY_ACTIVATED",
          },
          {
            id: binding.boundRevision * 10 + 2,
            role: "codex",
            text: `${boundProject.sceneTitle}の制作状態をProject Coreから復元しました。以後の提案と生成は、このProject IDとRevisionに固定します。`,
          },
          ...pendingRequestMessages,
        ]);
        setSessionBinding(binding);
      } catch (error) {
        if (!active) return;
        setSidecarHealth({
          status: "degraded",
          version: "0.1.0",
          projectCore: "unavailable",
          h3: "offline",
        });
        setMessages([{
          id: Date.now(),
          role: "system",
          text: error instanceof Error ? `Project Coreの読み込みに失敗しました: ${error.message}` : "Project Coreの読み込みに失敗しました。",
          meta: "not loaded",
        }]);
      }
    }

    void bindProjectContext();
    return () => {
      active = false;
    };
  }, [activeProjectId]);

  function switchProject(id: string) {
    if (scriptEditing || promptDirty || dirtyAssetIds.size > 0) {
      setToast("未保存の編集を保存またはキャンセルしてからProjectを切り替えてください");
      return;
    }
    const next = projects.find((item) => item.id === id);
    if (!next) return;
    setActiveProjectId(next.id);
    setAssets([]);
    setSelectedAssetId("");
    setShots([]);
    setSelectedShotId("");
    setSelectedPromptId("");
    setPrompt("");
    setTakes([]);
    setSelectedTakesByShot({});
    setSelectedTakeId("");
    setMessages([{ id: Date.now(), role: "system", text: `『${next.title}』をProject Coreから読み込んでいます。`, meta: "loading" }]);
    setSessionBinding(null);
    setIssuedHandoffPath(null);
    setDirtyAssetIds(new Set());
    setPromptDirty(false);
    renderRunToken.current += 1;
    setRenderSubmitting(false);
    setProjectLauncherOpen(false);
    setHandoffOpen(false);
    setView("overview");
    setToast(`${next.title} を開きました`);
  }

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (projectCreateSubmitting) return;
    const request: StoryCreateProjectRequest = {
      title: projectCreateDraft.title.trim(),
      code: projectCreateDraft.code.trim().toUpperCase(),
      subtitle: projectCreateDraft.subtitle.trim(),
      episodeTitle: projectCreateDraft.episodeTitle.trim(),
      sceneTitle: projectCreateDraft.sceneTitle.trim(),
      shotTitle: projectCreateDraft.shotTitle.trim(),
      logline: projectCreateDraft.logline.trim(),
      summary: projectCreateDraft.summary.trim(),
    };
    if (!request.title || !request.code || !request.episodeTitle || !request.sceneTitle || !request.shotTitle) {
      setToast("Project名・コード・Episode・Scene・Shotは必須です");
      return;
    }
    setProjectCreateSubmitting(true);
    try {
      const created = await STORY_SIDECAR.createProject(request);
      const next = projectFromSummary({
        projectId: created.projectId,
        projectRoot: created.projectRoot,
        code: created.code,
        title: created.title,
        revision: created.revision,
        stateDigest: created.stateDigest,
        focus: created.focus,
        metadata: created.metadata,
      });
      setProjects((current) => [...current.filter((candidate) => candidate.id !== next.id), next]);
      setProjectCreateDraft(INITIAL_PROJECT_DRAFT);
      setProjectCreateOpen(false);
      setProjectLauncherOpen(false);
      setActiveProjectId(next.id);
      setSessionBinding(null);
      setAssets([]);
      setShots([]);
      setSelectedPromptId("");
      setTakes([]);
      setSelectedTakesByShot({});
      setPrompt("");
      setToast(`『${next.title}』を作成しました`);
    } catch (error) {
      setToast(error instanceof Error ? `Project作成失敗: ${error.message}` : "Project作成に失敗しました");
    } finally {
      setProjectCreateSubmitting(false);
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = chatInput.trim();
    if (!text) return;
    const requestedAt = Date.now();
    const userMessage: Message = { id: requestedAt, role: "user", text };
    setMessages((current) => [...current, userMessage]);
    setChatInput("");
    if (!sessionBinding) {
      setMessages((current) => [...current, {
        id: requestedAt + 1,
        role: "system",
        text: "Sidecar未接続のため、この依頼はProject Coreへ保存されていません。",
        meta: "UI only",
      }]);
      setToast("Codex依頼を保存できませんでした: Sidecar未接続");
      return;
    }

    const requestId = `agent_request_${requestedAt.toString(36)}`;
    try {
      const revision = await commitProjectOperations(
        `Queue Codex request: ${text.slice(0, 120)}`,
        [{
          op: "upsert_entity",
          entityType: "agent_request",
          entityId: requestId,
          payload: {
            request: text,
            status: "pending",
            requested_by: "web_ui",
            project_id: project.id,
            episode_id: String(sessionBinding.focus.episodeId ?? "ep_001"),
            scene_id: String(sessionBinding.focus.sceneId ?? "sc_003"),
            shot_id: selectedShot.id,
            prompt_id: `prompt_${selectedShot.id.replace("sh_", "")}`,
            created_at: new Date(requestedAt).toISOString(),
          },
        }],
      );
      setMessages((current) => [...current, {
        id: requestedAt + 1,
        role: "codex",
        text: `依頼をProject Coreの REV ${revision} に保存しました。「Codexで続ける」から同じProject Contextで処理を再開できます。`,
        meta: `queued · ${requestId}`,
      }]);
      setToast(`Codex依頼を REV ${revision} に保存しました`);
    } catch (error) {
      setMessages((current) => [...current, {
        id: requestedAt + 1,
        role: "system",
        text: error instanceof Error ? `Codex依頼の保存に失敗しました: ${error.message}` : "Codex依頼の保存に失敗しました。",
        meta: "not committed",
      }]);
      setToast("Codex依頼をProject Coreへ保存できませんでした");
    }
  }

  function updateAssetCaption(value: string) {
    if (!selectedAsset) return;
    setAssets((current) =>
      current.map((item) => (item.id === selectedAsset.id ? { ...item, caption: value } : item)),
    );
    setDirtyAssetIds((current) => new Set(current).add(selectedAsset.id));
  }

  async function commitProjectOperations(
    message: string,
    operations: Array<{
      op: "upsert_entity" | "delete_entity" | "set_focus" | "set_approval";
      entityType?: string;
      entityId?: string;
      payload?: Readonly<Record<string, unknown>>;
    }>,
  ): Promise<number> {
    if (!sessionBinding) throw new Error("Project Core session is not bound");
    const result = await STORY_SIDECAR.commitPatch(project.id, {
      sessionId: sessionBinding.sessionId,
      expectedRevision: sessionBinding.boundRevision,
      message,
      operations,
    });
    setSessionBinding((current) => current ? {
      ...current,
      boundRevision: result.revision,
      stateDigest: result.stateDigest,
    } : current);
    setProjects((current) => current.map((candidate) => candidate.id === project.id ? { ...candidate, revision: result.revision, updated: "たった今" } : candidate));
    return result.revision;
  }

  function canonicalAssetPayload(asset: Asset): Readonly<Record<string, unknown>> {
    const preserved = asRecord(canonicalizePayload(asset.rawPayload)) ?? {};
    return {
      ...preserved,
      category: asset.kind,
      name: asset.name,
      role: asset.role,
      caption: asset.caption,
      prompt_caption: asset.promptCaption,
      locked: asset.locked,
      tone: asset.tone,
      revision_hint: activeRevision,
    };
  }

  async function commitAssetCaption() {
    if (!selectedAsset) return;
    if (!dirtyAssetIds.has(selectedAsset.id)) return;
    if (!sessionBinding) {
      setToast("未保存: Project Coreへ接続されていません");
      return;
    }
    try {
      const revision = await commitProjectOperations(
        `Update asset caption: ${selectedAsset.id}`,
        [{
          op: "upsert_entity",
          entityType: "asset",
          entityId: selectedAsset.id,
          payload: canonicalAssetPayload(selectedAsset),
        }],
      );
      setDirtyAssetIds((current) => {
        const next = new Set(current);
        next.delete(selectedAsset.id);
        return next;
      });
      setToast(`${selectedAsset.name} のキャプションを REV ${revision} に保存しました`);
    } catch (error) {
      setToast(error instanceof Error ? `素材保存失敗: ${error.message}` : "素材保存に失敗しました");
    }
  }

  async function toggleAssetLock() {
    if (!selectedAsset) return;
    const nextAsset = { ...selectedAsset, locked: !selectedAsset.locked };
    if (!sessionBinding) {
      setToast("固定状態を保存できません: Project Core未接続");
      return;
    }
    try {
      const revision = await commitProjectOperations(
        `${nextAsset.locked ? "Lock" : "Unlock"} asset: ${nextAsset.id}`,
        [{
          op: "upsert_entity",
          entityType: "asset",
          entityId: nextAsset.id,
          payload: canonicalAssetPayload(nextAsset),
        }],
      );
      setToast(`${nextAsset.name} の固定状態を REV ${revision} に保存しました`);
    } catch (error) {
      setToast(error instanceof Error ? `固定状態の保存失敗: ${error.message}` : "固定状態の保存に失敗しました");
      return;
    }
    setAssets((current) => current.map((item) => item.id === nextAsset.id ? nextAsset : item));
  }

  function openAssetEditor(mode: "create" | "edit") {
    if (mode === "edit" && !selectedAsset) return;
    setAssetEditorMode(mode);
    setAssetEditorDraft(mode === "edit" && selectedAsset ? {
      name: selectedAsset.name,
      category: selectedAsset.kind,
      role: selectedAsset.role,
      caption: selectedAsset.caption,
      promptCaption: selectedAsset.promptCaption,
      locked: selectedAsset.locked,
      source: "human",
      file: null,
    } : INITIAL_ASSET_DRAFT);
    setAssetEditorOpen(true);
  }

  async function saveAsset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (assetEditorSubmitting || !sessionBinding) {
      if (!sessionBinding) setToast("素材を保存できません: Project Core未接続");
      return;
    }
    const fields: StoryCreateAssetFields = {
      name: assetEditorDraft.name.trim(),
      category: assetEditorDraft.category,
      role: assetEditorDraft.role.trim(),
      caption: assetEditorDraft.caption.trim(),
      promptCaption: assetEditorDraft.promptCaption.trim(),
      locked: assetEditorDraft.locked,
      source: assetEditorDraft.source,
    };
    if (!fields.name) {
      setToast("素材名は必須です");
      return;
    }
    setAssetEditorSubmitting(true);
    try {
      if (assetEditorMode === "create") {
        const result = assetEditorDraft.file
          ? await STORY_SIDECAR.uploadAsset(project.id, sessionBinding.sessionId, sessionBinding.boundRevision, fields, assetEditorDraft.file)
          : await STORY_SIDECAR.createAsset(project.id, sessionBinding.sessionId, sessionBinding.boundRevision, fields);
        const nextAsset = assetFromPayload(result.assetId, result.asset, project.id, sessionBinding.sessionId);
        setAssets((current) => [...current.filter((candidate) => candidate.id !== nextAsset.id), nextAsset]);
        setSelectedAssetId(nextAsset.id);
        setSessionBinding((current) => current ? { ...current, boundRevision: result.revision, stateDigest: result.stateDigest } : current);
        setProjects((current) => current.map((candidate) => candidate.id === project.id ? { ...candidate, revision: result.revision, updated: "たった今" } : candidate));
        setToast(`${nextAsset.name} を REV ${result.revision} に追加しました`);
      } else if (selectedAsset) {
        const nextAsset: Asset = { ...selectedAsset, ...fields, kind: fields.category, promptCaption: fields.promptCaption };
        const revision = await commitProjectOperations(
          `Update asset: ${nextAsset.id}`,
          [{ op: "upsert_entity", entityType: "asset", entityId: nextAsset.id, payload: canonicalAssetPayload(nextAsset) }],
        );
        setAssets((current) => current.map((candidate) => candidate.id === nextAsset.id ? nextAsset : candidate));
        setToast(`${nextAsset.name} を REV ${revision} に保存しました`);
      }
      setAssetEditorOpen(false);
      setAssetEditorDraft(INITIAL_ASSET_DRAFT);
    } catch (error) {
      setToast(error instanceof Error ? `素材保存失敗: ${error.message}` : "素材保存に失敗しました");
    } finally {
      setAssetEditorSubmitting(false);
    }
  }

  function beginScriptEdit() {
    if (!selectedShot) return;
    setScriptDraft({ ...selectedShot });
    setScriptEditing(true);
  }

  async function saveScript(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!scriptDraft || scriptSubmitting || !sessionBinding) {
      if (!sessionBinding) setToast("脚本を保存できません: Project Core未接続");
      return;
    }
    setScriptSubmitting(true);
    try {
      const preserved = asRecord(canonicalizePayload(scriptDraft.rawPayload)) ?? {};
      const payload = {
        ...preserved,
        label: scriptDraft.label,
        title: scriptDraft.title,
        status: scriptDraft.status,
        order: scriptDraft.order ?? 0,
        episode_id: scriptDraft.episodeId || String(sessionBinding.focus.episodeId ?? "ep_001"),
        scene_id: scriptDraft.sceneId || String(sessionBinding.focus.sceneId ?? "sc_001"),
        story_purpose: scriptDraft.storyPurpose ?? "",
        intent: scriptDraft.storyPurpose ?? "",
        performance: scriptDraft.performance ?? "",
        acting: scriptDraft.performance ?? "",
        action: scriptDraft.action ?? "",
        description: scriptDraft.action ?? "",
        dialogue: scriptDraft.dialogue ?? "",
        soundscape: scriptDraft.soundscape ?? "",
        music_policy: scriptDraft.musicPolicy ?? "auto",
        camera: scriptDraft.camera ?? "",
        duration_frames: scriptDraft.durationFrames ?? 124,
      };
      const revision = await commitProjectOperations(
        `Update script shot: ${scriptDraft.id}`,
        [{ op: "upsert_entity", entityType: "shot", entityId: scriptDraft.id, payload }],
      );
      const nextShot = { ...scriptDraft, rawPayload: payload };
      setShots((current) => current.map((candidate) => candidate.id === nextShot.id ? nextShot : candidate));
      setScriptEditing(false);
      setScriptDraft(null);
      setToast(`${nextShot.label} の脚本を REV ${revision} に保存しました`);
    } catch (error) {
      setToast(error instanceof Error ? `脚本保存失敗: ${error.message}` : "脚本保存に失敗しました");
    } finally {
      setScriptSubmitting(false);
    }
  }

  async function selectScriptShot(id: string) {
    if (scriptEditing && scriptDraft?.id !== id) {
      setToast("編集中のShotを保存またはキャンセルしてください");
      return;
    }
    const nextShot = shots.find((candidate) => candidate.id === id);
    if (!nextShot) return;
    setSelectedShotId(id);
    if (!sessionBinding) return;
    try {
      const context = await STORY_SIDECAR.getContext(project.id, sessionBinding.sessionId);
      const entities = contextEntities(context);
      setEndFrameAssetByShot(endFrameAssetsFromContext(entities));
      const promptEntity = entities.find((entity) => entity.entityType === "prompt" && entity.payload.shotId === id);
      const focus = asRecord(canonicalizePayload(sessionBinding.focus)) ?? {};
      const revision = await commitProjectOperations(`Focus shot: ${id}`, [{
        op: "set_focus",
        payload: {
          ...focus,
          episode_id: nextShot.episodeId || focus.episode_id,
          scene_id: nextShot.sceneId || focus.scene_id,
          shot_id: id,
          prompt_id: promptEntity?.entityId ?? null,
        },
      }]);
      setPrompt(promptFromContext(entities, promptEntity?.entityId ?? null, id, ""));
      setSelectedPromptId(promptEntity?.entityId ?? "");
      const hydratedTakes = takesFromContext(entities, id, [], project.id, sessionBinding.sessionId);
      setTakes(hydratedTakes.takes);
      setSelectedTakeId(hydratedTakes.selectedTakeId);
      setSelectedTakesByShot(selectedTakesFromContext(entities, project.id, sessionBinding.sessionId));
      setSessionBinding((current) => current ? {
        ...current,
        boundRevision: revision,
        focus: { ...current.focus, shotId: id, promptId: promptEntity?.entityId ?? null },
      } : current);
    } catch (error) {
      setToast(error instanceof Error ? `Focus更新失敗: ${error.message}` : "Focus更新に失敗しました");
    }
  }

  function updatePrompt(value: string) {
    setPrompt(value);
    setPromptDirty(true);
  }

  async function commitPrompt() {
    if (!promptDirty) return;
    if (!sessionBinding) {
      setToast("未保存: Project Coreへ接続されていません");
      return;
    }
    const promptId = selectedPromptId || `prompt_${selectedShot.id.replace("sh_", "")}`;
    const referenceAssetIds = assets.filter((asset) => asset.mediaKind && asset.mediaKind !== "none" && asset.contentUrl).map((asset) => asset.id);
    try {
      const context = await STORY_SIDECAR.getContext(project.id, sessionBinding.sessionId);
      const existingPrompt = contextEntities(context).find((entity) => entity.entityType === "prompt" && entity.entityId === promptId);
      const preserved = asRecord(canonicalizePayload(existingPrompt?.payload)) ?? {};
      const revision = await commitProjectOperations(
        `Update H3 prompt: ${selectedShot.id}`,
        [{
          op: "upsert_entity",
          entityType: "prompt",
          entityId: promptId,
          payload: {
            ...preserved,
            code: `PROMPT-${selectedShot.id.replace("sh_", "SH")}`,
            prompt,
            mode: referenceAssetIds.length > 0 ? "omni" : "t2v",
            episode_id: selectedShot.episodeId || String(sessionBinding.focus.episodeId ?? "ep_001"),
            scene_id: selectedShot.sceneId || String(sessionBinding.focus.sceneId ?? "sc_001"),
            shot_id: selectedShot.id,
            reference_asset_ids: referenceAssetIds,
            revision_hint: activeRevision,
          },
        }],
      );
      setPromptDirty(false);
      setSelectedPromptId(promptId);
      setToast(`${selectedShot.label} のPromptを REV ${revision} に保存しました`);
    } catch (error) {
      setToast(error instanceof Error ? `Prompt保存失敗: ${error.message}` : "Prompt保存に失敗しました");
    }
  }

  async function selectTake(id: string) {
    if (!sessionBinding) {
      setToast("Takeを採用できません: Project Core未接続");
      return;
    }
    {
      try {
        const context = await STORY_SIDECAR.getContext(project.id, sessionBinding.sessionId);
        const entities = Array.isArray(context.entities) ? context.entities : [];
        const takeEntities = entities.filter((entity): entity is Record<string, unknown> => (
          typeof entity === "object" && entity !== null && (entity as Record<string, unknown>).entityType === "take"
        ));
        const target = takeEntities.find((entity) => entity.entityId === id);
        if (target) {
          const targetPayload = (target.payload as Record<string, unknown> | undefined) ?? {};
          if (String(targetPayload.status ?? "") !== "ready") {
            setToast("完成していないTakeは採用できません");
            return;
          }
          const operations = takeEntities
            .filter((entity) => entity.entityId === id || (entity.payload as Record<string, unknown> | undefined)?.selection === "selected")
            .map((entity) => {
              const payload = (entity.payload as Record<string, unknown> | undefined) ?? {};
              return {
                op: "upsert_entity" as const,
                entityType: "take",
                entityId: String(entity.entityId),
                payload: {
                  ...(canonicalizePayload(payload) as Record<string, unknown>),
                  selection: entity.entityId === id ? "selected" : "candidate",
                },
              };
            });
          const revision = await commitProjectOperations(`Select take: ${id}`, operations);
          setToast(`${id.toUpperCase()} を REV ${revision} で採用しました`);
        } else {
          setToast("このTakeはProject Coreに存在しないため採用できません");
          return;
        }
      } catch (error) {
        setToast(error instanceof Error ? `Take採用失敗: ${error.message}` : "Take採用に失敗しました");
        return;
      }
    }
    setSelectedTakeId(id);
    setTakes((current) =>
      current.map((item) => ({
        ...item,
        state: item.id === id ? "selected" : item.state === "selected" ? "candidate" : item.state,
      })),
    );
    const selected = takes.find((item) => item.id === id);
    if (selected) {
      setSelectedTakesByShot((current) => ({
        ...current,
        [selectedShot.id]: { ...selected, state: "selected" },
      }));
    }
  }

  async function downloadTake(take: Take) {
    if (!take.artifactUrl) {
      setToast("このTakeにはダウンロード可能な実ファイルがありません");
      return;
    }
    const shotToken = safeDownloadName(take.shotId ?? selectedShot.id);
    const filename = `${safeDownloadName(project.code)}_${shotToken}_${safeDownloadName(take.id)}.mp4`;
    try {
      await downloadMediaToBrowser(take.artifactUrl, filename);
      setToast(`${take.label}をブラウザのダウンロードへ保存しました`);
    } catch (error) {
      setToast(error instanceof Error ? `ダウンロード失敗: ${error.message}` : "ダウンロードに失敗しました");
    }
  }

  async function startRender() {
    if (rendering) return;
    if (!selectedShot.id || !prompt.trim()) {
      setToast("生成するShotとPromptを設定してください");
      return;
    }
    if (!sessionBinding || !sidecarHealth || sidecarHealth.h3 === "offline" || sidecarHealth.h3 === "incompatible") {
      setToast("実H3へ接続できないため生成していません。NIKU H STUDIOの起動状態を確認してください");
      return;
    }

    const token = renderRunToken.current + 1;
    const fileAssets = assets.filter((asset) => asset.mediaKind && asset.mediaKind !== "none" && asset.contentUrl);
    const mediaCounts = {
      image: fileAssets.filter((asset) => asset.mediaKind === "image").length,
      video: fileAssets.filter((asset) => asset.mediaKind === "video").length,
      audio: fileAssets.filter((asset) => asset.mediaKind === "audio").length,
    };
    if (fileAssets.length > 12 || mediaCounts.image > 9 || mediaCounts.video > 3 || mediaCounts.audio > 3) {
      setToast("H3参照上限は合計12件（画像9・動画3・音声3）です。素材を整理してください");
      return;
    }
    if (mediaCounts.audio > 0 && mediaCounts.image + mediaCounts.video === 0) {
      setToast("音声参照には少なくとも1件の画像または動画参照が必要です");
      return;
    }
    const shotIndex = shots.findIndex((shot) => shot.id === selectedShot.id);
    const predecessorShot = shotIndex > 0 ? shots[shotIndex - 1] : undefined;
    const firstImageAssetId = carryOverEndFrame && predecessorShot
      ? endFrameAssetByShot[predecessorShot.id] ?? null
      : null;
    const referenceAssetIds = firstImageAssetId ? [] : fileAssets.map((asset) => asset.id);
    const renderMode = firstImageAssetId ? "i2v" : referenceAssetIds.length > 0 ? "omni" : "t2v";
    const resolutionAspect = resolutionCatalog.aspects.find((aspect) => aspect.id === generationSettings.aspectRatio)
      ?? resolutionCatalog.aspects.find((aspect) => aspect.id === resolutionCatalog.defaultAspectRatio)
      ?? resolutionCatalog.aspects[0];
    const resolution = resolutionAspect?.presets[generationSettings.resolutionQuality]
      ?? resolutionAspect?.presets[resolutionCatalog.defaultQuality]
      ?? Object.values(resolutionAspect?.presets ?? {})[0];
    if (!resolution) {
      setToast("NIKU H STUDIOの解像度カタログを取得できませんでした");
      return;
    }
    const rawPromptMode = generationSettings.promptProcessingMode === "raw_en";
    const effectiveSettings = rawPromptMode
      ? {
          ...generationSettings,
          style: "natural" as const,
          standaloneAudioPolicy: "dialogue_priority" as const,
          audioPreset: "auto" as const,
          dialogue: "",
          soundscape: "",
          musicPolicy: "auto" as const,
        }
      : generationSettings;
    renderRunToken.current = token;
    setRenderSubmitting(true);
    try {
      const submitted = await STORY_SIDECAR.submitRender(project.id, selectedShot.id, {
        sessionId: sessionBinding.sessionId,
        expectedRevision: sessionBinding.boundRevision,
        request: {
          shotId: selectedShot.id,
          fields: {
            mode: renderMode,
            style: effectiveSettings.style,
            prompt,
            width: resolution.width,
            height: resolution.height,
            num_frames: effectiveSettings.durationFrames,
            steps: effectiveSettings.steps,
            seed: effectiveSettings.seed,
            acceleration: effectiveSettings.acceleration,
            ref_image_size: effectiveSettings.refImageSize,
            prompt_processing_mode: effectiveSettings.promptProcessingMode,
            standalone_audio_policy: effectiveSettings.standaloneAudioPolicy,
            audio_preset: effectiveSettings.audioPreset,
            dialogue: effectiveSettings.dialogue,
            soundscape: effectiveSettings.soundscape,
            music_policy: effectiveSettings.musicPolicy,
            audio_gain_db: effectiveSettings.audioGainDb,
          },
          uploads: {
            first_image_asset_id: firstImageAssetId,
            last_image_asset_id: null,
            reference_asset_ids: referenceAssetIds,
          },
        },
      });
      if (token !== renderRunToken.current) return;
      const takeId = String(submitted.takeId ?? "");
      const localJobId = String(submitted.localJobId ?? "");
      const h3JobId = String(submitted.h3JobId ?? "");
      const revision = Number(submitted.revision);
      if (!takeId || !localJobId || !/^[a-f0-9]{12}$/.test(h3JobId) || !Number.isInteger(revision)) {
        throw new Error("Sidecar returned an invalid render binding");
      }
      const label = `TAKE ${String(takes.length + 1).padStart(3, "0")}`;
      const nextTake: Take = {
        id: takeId,
        label,
        state: "rendering",
        score: null,
        note: `H3 job ${h3JobId} をProject Revision ${revision}へ記録しました。`,
        seed: effectiveSettings.seed,
        tone: "take-green",
        shotId: selectedShot.id,
      };
      setTakes((current) => [...current, nextTake]);
      setSelectedTakeId(takeId);
      setSessionBinding((current) => current ? { ...current, boundRevision: revision } : current);
      setSidecarHealth((current) => current ? { ...current, h3: "busy" } : current);
      setToast(`${label} を実H3キューへ追加しました`);
      void trackLiveRender({
        token,
        projectId: project.id,
        shotId: selectedShot.id,
        sessionId: sessionBinding.sessionId,
        revision,
        localJobId,
        h3JobId,
        takeId,
        label,
      });
    } catch (error) {
      setToast(error instanceof Error ? `H3投入失敗: ${error.message}` : "H3投入に失敗しました");
    } finally {
      if (token === renderRunToken.current) setRenderSubmitting(false);
    }
  }

  async function trackLiveRender(options: {
    token: number;
    projectId: string;
    shotId: string;
    sessionId: string;
    revision: number;
    localJobId: string;
    h3JobId: string;
    takeId: string;
    label: string;
  }) {
    let transientFailures = 0;
    for (let attempt = 0; attempt < 1800; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      if (options.token !== renderRunToken.current) return;
      try {
        const job = await STORY_SIDECAR.getH3Job(options.h3JobId);
        transientFailures = 0;
        const status = String(job.status ?? "running");
        const progress = typeof job.progress === "number" ? job.progress : 0;
        const progressPercent = progress <= 1 ? progress * 100 : progress;
        setTakes((current) => current.map((item) => item.id === options.takeId ? {
          ...item,
          note: `実H3 ${status} · ${Math.round(progressPercent)}% · ${String(job.message ?? "生成処理中")}`,
        } : item));
        if (!["completed", "failed", "cancelled", "interrupted"].includes(status)) continue;

        const terminalMessage = String(job.message ?? "").trim();
        const firstDiagnostic = Array.isArray(job.diagnostics)
          ? job.diagnostics.find((item): item is Record<string, unknown> => (
              typeof item === "object" && item !== null
            ))
          : undefined;
        const diagnosticCode = String(firstDiagnostic?.code ?? "").trim();
        const diagnosticMessage = String(firstDiagnostic?.message ?? "").trim();
        const terminalFailure = [status, diagnosticCode, diagnosticMessage || terminalMessage]
          .filter(Boolean)
          .join(" · ");

        const synced = await STORY_SIDECAR.syncRender(options.projectId, options.localJobId, {
          sessionId: options.sessionId,
          expectedRevision: options.revision,
          downloadResult: true,
          extractEndFrame: carryOverEndFrame,
        });
        if (options.token !== renderRunToken.current) return;
        const syncedRevision = Number(synced.revision);
        if (Number.isInteger(syncedRevision)) {
          setSessionBinding((current) => current ? { ...current, boundRevision: syncedRevision } : current);
        }
        const completed = status === "completed";
        const artifactId = String(synced.artifactId ?? "").trim();
        const endFrameAssetId = String(synced.endFrameAssetId ?? "").trim();
        if (completed && endFrameAssetId) {
          setEndFrameAssetByShot((current) => ({ ...current, [options.shotId]: endFrameAssetId }));
        }
        setTakes((current) => current.map((item) => item.id === options.takeId ? {
          ...item,
          state: completed ? "candidate" : "rejected",
          score: completed ? 88 : null,
          note: completed
            ? `実H3生成完了 · ${String(synced.artifactRelativePath ?? "Project出力へ保存済み")}`
            : `実H3 ${terminalFailure || status}`,
          artifactUrl: completed && artifactId
            ? STORY_SIDECAR.assetContentUrl(options.projectId, artifactId, options.sessionId)
            : item.artifactUrl,
        } : item));
        setSidecarHealth((current) => current ? { ...current, h3: "ready" } : current);
        setToast(completed
          ? `${options.label} の実H3生成が完了しました`
          : `${options.label}: ${terminalFailure || status}`);
        return;
      } catch (error) {
        transientFailures += 1;
        if (transientFailures < 3) continue;
        setTakes((current) => current.map((item) => item.id === options.takeId ? {
          ...item,
          state: "rejected",
          note: error instanceof Error ? `H3追跡失敗: ${error.message}` : "H3追跡に失敗しました",
        } : item));
        setToast("H3ジョブの追跡に失敗しました。Project履歴から再同期できます");
        return;
      }
    }
  }

  async function copyHandoff() {
    let path = visibleHandoffPath;
    if (sessionBinding) {
      try {
        const issued = await STORY_SIDECAR.createHandoff(
          project.id,
          sessionBinding.sessionId,
        );
        const projectRoot = String(issued.capsule.projectRoot ?? "").replaceAll("/", "\\");
        const relativePath = issued.path.replaceAll("/", "\\");
        path = `${INTEGRATED_STORY_ROOT}\\${projectRoot}\\${relativePath}`;
        setIssuedHandoffPath(path);
      } catch {
        setToast("Project CoreでHandoffを発行できませんでした");
        return;
      }
    }
    const handoff = `H3STORY_HANDOFF:\n${path}`;
    try {
      await navigator.clipboard.writeText(handoff);
      setToast("Codex用Handoffをコピーしました");
    } catch {
      setToast("Handoffを表示しました。手動でコピーしてください");
    }
  }

  return (
    <main className="studio-shell">
      <header className="topbar">
        <button className="brand" type="button" onClick={() => setView("overview")}>
          <span className="brand-mark">H3</span>
          <span>
            <strong>NIKU STDUIO FOR WORK</strong>
            <small>LOCAL DIRECTOR</small>
          </span>
        </button>

        <button
          className="project-identity"
          type="button"
          onClick={() => setProjectLauncherOpen(true)}
          aria-label="プロジェクトを切り替える"
        >
          <span className="project-accent" style={{ background: project.accent }} />
          <span className="identity-copy">
            <span>{project.title}</span>
            <small>{project.code}</small>
          </span>
          <span className="identity-meta">
            <span>REV {activeRevision}</span>
            <small>{project.phase}</small>
          </span>
          <span className="chevron">⌄</span>
        </button>

        <div className="topbar-actions">
          <div className="engine-status" title="NIKU H STUDIO renderer">
            <span className={`live-dot ${sidecarHealth?.h3 === "offline" ? "offline" : ""}`} />
            <span>{sidecarHealth?.h3 === "ready" ? "H3 ready" : sidecarHealth?.h3 === "busy" ? "H3 busy" : sidecarHealth?.h3 === "incompatible" ? "H3 incompatible" : sidecarHealth ? "H3 offline" : "Sidecar…"}</span>
            <small>{sessionBinding ? "project bound" : "not bound"}</small>
          </div>
          <button className="button ghost compact" type="button" onClick={() => setToast(dirtyAssetIds.size > 0 || promptDirty || scriptEditing ? "未保存の編集があります" : "Project Coreへ保存済みです")}>
            {dirtyAssetIds.size > 0 || promptDirty || scriptEditing ? "未保存あり" : "保存済み"}
          </button>
          <button className="button primary compact" type="button" disabled={!sessionBinding || scriptEditing || promptDirty || dirtyAssetIds.size > 0} onClick={() => setHandoffOpen(true)}>
            Codexで続ける
          </button>
        </div>
      </header>

      <div className="workspace-grid">
        <aside className="left-rail">
          <nav className="workspace-nav" aria-label="制作ワークスペース">
            <p className="rail-kicker">WORKSPACE</p>
            {NAV_ITEMS.map((item) => (
              <button
                key={item.id}
                className={`nav-item ${view === item.id ? "active" : ""}`}
                type="button"
                onClick={() => setView(item.id)}
              >
                <span className={`nav-icon nav-${item.id}`} />
                <span>{item.label}</span>
                <small>{item.id === "production" ? `${takes.length} takes` : item.id === "script" ? `${shots.length} shots` : item.id === "world" ? `${assets.length} assets` : `REV ${activeRevision}`}</small>
              </button>
            ))}
          </nav>

          <section className="episode-tree">
            <div className="rail-section-title">
              <span>EPISODE 01</span>
            </div>
            <button className="scene-row open" type="button" onClick={() => setView("script")}>
              <span>03</span>
              <span>
                <strong>{project.sceneTitle}</strong>
                <small>{shots.length} shots</small>
              </span>
              <em>⌃</em>
            </button>
            <div className="shot-list">
              {shots.map((shot) => (
                <button
                  key={shot.id}
                  className={`shot-row ${selectedShotId === shot.id ? "active" : ""}`}
                  type="button"
                  onClick={() => {
                    void selectScriptShot(shot.id);
                    setView("production");
                  }}
                >
                  <span className={`shot-status ${shot.status}`} />
                  <span>
                    <small>{shot.label}</small>
                    <strong>{shot.title}</strong>
                  </span>
                </button>
              ))}
            </div>
          </section>

          <div className="rail-footer">
            <div>
              <span>PROJECT PROGRESS</span>
              <strong>{project.progress}%</strong>
            </div>
            <div className="progress-track"><span style={{ width: `${project.progress}%` }} /></div>
            <small>最終更新 {project.updated}</small>
          </div>
        </aside>

        <section className="content-stage">
          {view === "overview" && (
            <OverviewView
              project={project}
              revision={activeRevision}
              onOpenScript={() => setView("script")}
              onOpenProduction={() => setView("production")}
              onOpenWorld={() => setView("world")}
              shotCount={shots.length}
              assetCount={assets.length}
              takeCount={takes.length}
            />
          )}
          {view === "script" && (
            <ScriptView
              project={project}
              shots={shots}
              selectedShotId={selectedShotId}
              onSelectShot={selectScriptShot}
              editing={scriptEditing}
              draft={scriptDraft}
              saving={scriptSubmitting}
              onBeginEdit={beginScriptEdit}
              onDraftChange={setScriptDraft}
              onSave={saveScript}
              onCancel={() => { setScriptEditing(false); setScriptDraft(null); }}
              onProduction={() => setView("production")}
            />
          )}
          {view === "world" && (
            <WorldView
              assets={assets}
              selectedAsset={selectedAsset}
              onSelectAsset={setSelectedAssetId}
              onCaptionChange={updateAssetCaption}
              onCaptionCommit={commitAssetCaption}
              onToggleLock={toggleAssetLock}
              onCreateAsset={() => openAssetEditor("create")}
              onEditAsset={() => openAssetEditor("edit")}
              captionDirty={selectedAsset ? dirtyAssetIds.has(selectedAsset.id) : false}
            />
          )}
          {view === "production" && (
            <ProductionView
              shots={shots}
              assets={assets}
              prompt={prompt}
              revision={activeRevision}
              onPromptChange={updatePrompt}
              onPromptCommit={commitPrompt}
              takes={takes}
              selectedTake={selectedTake}
              selectedTakesByShot={selectedTakesByShot}
              selectedShot={selectedShot}
              onPreviewTake={setSelectedTakeId}
              onSelectTake={selectTake}
              onDownloadTake={downloadTake}
              onRender={startRender}
              carryOverEndFrame={carryOverEndFrame}
              onCarryOverEndFrameChange={setCarryOverEndFrame}
              continuityAssetReady={Boolean((() => { const index = shots.findIndex((shot) => shot.id === selectedShot.id); return index > 0 && endFrameAssetByShot[shots[index - 1]?.id]; })())}
              settings={generationSettings}
              resolutionCatalog={resolutionCatalog}
              onSettingsChange={setGenerationSettings}
              rendering={rendering}
              onSelectShot={selectScriptShot}
              promptDirty={promptDirty}
            />
          )}
        </section>

        <aside className="codex-dock">
          <div className="dock-head">
            <div className="codex-orb">C</div>
            <div>
              <strong>Codex Director</strong>
              <small><span className={`live-dot ${sessionBinding ? "" : "offline"}`} /> {sessionBinding ? "project-bound" : "not bound"}</small>
            </div>
          </div>

          <div className="binding-card">
            <div>
              <span>ACTIVE CONTEXT</span>
              <em>{sessionBinding ? "同期済み" : "接続待ち"}</em>
            </div>
            <strong>{project.title}</strong>
            <code>{project.id.slice(0, 20)}…</code>
            <p>EP01 / Scene 03 / {selectedShot.label.replace("Shot ", "Shot ")}</p>
          </div>

          <div className="message-stream" aria-live="polite">
            {messages.map((message) => (
              <article key={message.id} className={`message ${message.role}`}>
                <div className="message-label">
                  <span>{message.role === "codex" ? "CODEX" : message.role === "user" ? "YOU" : "SYSTEM"}</span>
                  {(message.meta || message.role === "system") && (
                    <small>{message.role === "system" ? `revision ${activeRevision} · hash verified` : message.meta}</small>
                  )}
                </div>
                <p>{message.text}</p>
              </article>
            ))}
          </div>

          <div className="quick-actions">
            <button type="button" onClick={() => setChatInput("このShotの連続性を確認して")}>連続性を確認</button>
            <button type="button" onClick={() => setChatInput("警告灯を強調したPrompt差分を作って")}>Prompt差分</button>
          </div>

          <form className="chat-compose" onSubmit={sendMessage}>
            <textarea
              value={chatInput}
              onChange={(event) => setChatInput(event.target.value)}
              placeholder="現在のProjectについてCodexに依頼…"
              aria-label="Codexへの依頼"
            />
            <div>
              <span>REV {activeRevision} に提案</span>
              <button type="submit" aria-label="Codexへ送信">↑</button>
            </div>
          </form>
        </aside>
      </div>

      {projectLauncherOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setProjectLauncherOpen(false)}>
          <section className="project-modal" role="dialog" aria-modal="true" aria-labelledby="project-modal-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <div>
                <span className="eyebrow">PROJECT REGISTRY</span>
                <h2 id="project-modal-title">制作プロジェクト</h2>
              </div>
              <button type="button" onClick={() => setProjectLauncherOpen(false)} aria-label="閉じる">×</button>
            </div>
            <div className="project-list">
              {projects.map((item) => (
                <button key={item.id} className={`project-card ${item.id === project.id ? "active" : ""}`} type="button" onClick={() => switchProject(item.id)}>
                  <span className="project-card-art" style={{ "--project-tone": item.accent } as CSSProperties} />
                  <span className="project-card-copy">
                    <span><strong>{item.title}</strong><em>{item.phase}</em></span>
                    <small>{item.subtitle}</small>
                    <code>{item.code} · REV {item.revision}</code>
                  </span>
                  <span className="project-card-progress">{item.progress}%</span>
                </button>
              ))}
              <button className="new-project-card" type="button" onClick={() => { setProjectLauncherOpen(false); setProjectCreateOpen(true); }}>＋ 新しいProjectを作る</button>
            </div>
          </section>
        </div>
      )}

      {projectCreateOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => !projectCreateSubmitting && setProjectCreateOpen(false)}>
          <form className="editor-modal" role="dialog" aria-modal="true" aria-labelledby="project-create-title" onSubmit={createProject} onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <div><span className="eyebrow">NEW STORY PROJECT</span><h2 id="project-create-title">新規Projectを作成</h2></div>
              <button type="button" disabled={projectCreateSubmitting} onClick={() => setProjectCreateOpen(false)} aria-label="閉じる">×</button>
            </div>
            <p className="editor-lead">空のProject CoreにEpisode・Scene・最初のShotを作成します。入力内容は再読込後も保持されます。</p>
            <div className="editor-grid two-columns">
              <label>Project名<input required autoFocus value={projectCreateDraft.title} onChange={(event) => setProjectCreateDraft((current) => ({ ...current, title: event.target.value }))} /></label>
              <label>Projectコード<input required pattern="[A-Za-z0-9_-]+" placeholder="MY-STORY-01" value={projectCreateDraft.code} onChange={(event) => setProjectCreateDraft((current) => ({ ...current, code: event.target.value }))} /></label>
              <label className="wide">サブタイトル<input value={projectCreateDraft.subtitle} onChange={(event) => setProjectCreateDraft((current) => ({ ...current, subtitle: event.target.value }))} /></label>
              <label>Episodeタイトル<input required value={projectCreateDraft.episodeTitle} onChange={(event) => setProjectCreateDraft((current) => ({ ...current, episodeTitle: event.target.value }))} /></label>
              <label>Sceneタイトル<input required value={projectCreateDraft.sceneTitle} onChange={(event) => setProjectCreateDraft((current) => ({ ...current, sceneTitle: event.target.value }))} /></label>
              <label className="wide">最初のShot<input required value={projectCreateDraft.shotTitle} onChange={(event) => setProjectCreateDraft((current) => ({ ...current, shotTitle: event.target.value }))} /></label>
              <label className="wide">ログライン<textarea value={projectCreateDraft.logline} onChange={(event) => setProjectCreateDraft((current) => ({ ...current, logline: event.target.value }))} /></label>
              <label className="wide">概要<textarea value={projectCreateDraft.summary} onChange={(event) => setProjectCreateDraft((current) => ({ ...current, summary: event.target.value }))} /></label>
            </div>
            <div className="modal-actions">
              <button className="button ghost" type="button" disabled={projectCreateSubmitting} onClick={() => setProjectCreateOpen(false)}>キャンセル</button>
              <button className="button primary" type="submit" disabled={projectCreateSubmitting}>{projectCreateSubmitting ? "作成中…" : "Projectを作成"}</button>
            </div>
          </form>
        </div>
      )}

      {assetEditorOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => !assetEditorSubmitting && setAssetEditorOpen(false)}>
          <form className="editor-modal asset-editor-modal" role="dialog" aria-modal="true" aria-labelledby="asset-editor-title" onSubmit={saveAsset} onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <div><span className="eyebrow">PROJECT ASSET</span><h2 id="asset-editor-title">{assetEditorMode === "create" ? "素材を追加" : "素材を編集"}</h2></div>
              <button type="button" disabled={assetEditorSubmitting} onClick={() => setAssetEditorOpen(false)} aria-label="閉じる">×</button>
            </div>
            <p className="editor-lead">画像・動画・音声はProject配下へ保存し、H3参照素材として同じIDで管理します。</p>
            <div className="editor-grid two-columns">
              <label>素材名<input required autoFocus value={assetEditorDraft.name} onChange={(event) => setAssetEditorDraft((current) => ({ ...current, name: event.target.value }))} /></label>
              <label>分類<select value={assetEditorDraft.category} onChange={(event) => setAssetEditorDraft((current) => ({ ...current, category: event.target.value as AssetKind }))}><option value="character">人物</option><option value="environment">舞台</option><option value="prop">小道具</option><option value="reference">参照素材</option></select></label>
              <label className="wide">役割<input value={assetEditorDraft.role} onChange={(event) => setAssetEditorDraft((current) => ({ ...current, role: event.target.value }))} /></label>
              {assetEditorMode === "create" && <label className="wide">素材ファイル（任意）<input type="file" accept=".png,.jpg,.jpeg,.webp,.gif,.mp4,.webm,.wav,.mp3,.flac,.m4a,.ogg" onChange={(event) => setAssetEditorDraft((current) => ({ ...current, file: event.target.files?.[0] ?? null }))} /><small>未指定の場合はメタデータ素材として保存します。PNG/JPEG/WebP/GIF、MP4/WebM、WAV/MP3/M4A/Ogg/FLACに対応。</small></label>}
              <label className="wide">人間向けキャプション<textarea value={assetEditorDraft.caption} onChange={(event) => setAssetEditorDraft((current) => ({ ...current, caption: event.target.value }))} /></label>
              <label className="wide">H3用キャプション<textarea value={assetEditorDraft.promptCaption} onChange={(event) => setAssetEditorDraft((current) => ({ ...current, promptCaption: event.target.value }))} /><small>これは素材の意味・外見メタデータです。日本語PromptのH3書式化はNIKU H STUDIO既存フローが担当します。</small></label>
              <label className="checkbox-field wide"><input type="checkbox" checked={assetEditorDraft.locked} onChange={(event) => setAssetEditorDraft((current) => ({ ...current, locked: event.target.checked }))} />外見・設定を固定する</label>
            </div>
            <div className="modal-actions">
              <button className="button ghost" type="button" disabled={assetEditorSubmitting} onClick={() => setAssetEditorOpen(false)}>キャンセル</button>
              <button className="button primary" type="submit" disabled={assetEditorSubmitting || !sessionBinding}>{assetEditorSubmitting ? "保存中…" : "素材を保存"}</button>
            </div>
          </form>
        </div>
      )}

      {handoffOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setHandoffOpen(false)}>
          <section className="handoff-modal" role="dialog" aria-modal="true" aria-labelledby="handoff-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="handoff-mark">↗</div>
            <span className="eyebrow">CODEX HANDOFF</span>
            <h2 id="handoff-title">まっさらなCodexへ引き継ぐ</h2>
            <p>Project ID、Revision、現在地、承認状態を固定したContext Capsuleを発行します。</p>
            <div className="handoff-summary">
              <div><span>Project</span><strong>{project.title}</strong></div>
              <div><span>Revision</span><strong>{activeRevision}</strong></div>
              <div><span>Focus</span><strong>EP01 / SC03 / {selectedShot.id.replace("sh_", "SH")}</strong></div>
              <div><span>Next</span><strong>Take比較</strong></div>
            </div>
            <pre>H3STORY_HANDOFF:{"\n"}{visibleHandoffPath}</pre>
            <div className="modal-actions">
              <button className="button ghost" type="button" onClick={() => setHandoffOpen(false)}>キャンセル</button>
              <button className="button primary" type="button" onClick={copyHandoff}>Handoffをコピー</button>
            </div>
          </section>
        </div>
      )}

      {toast && <div className="toast" role="status"><span>✓</span>{toast}</div>}
    </main>
  );
}

function OverviewView({
  project,
  revision,
  onOpenScript,
  onOpenProduction,
  onOpenWorld,
  shotCount,
  assetCount,
  takeCount,
}: {
  project: Project;
  revision: number;
  onOpenScript: () => void;
  onOpenProduction: () => void;
  onOpenWorld: () => void;
  shotCount: number;
  assetCount: number;
  takeCount: number;
}) {
  return (
    <div className="view overview-view">
      <div className="view-heading">
        <div>
          <span className="eyebrow">PROJECT OVERVIEW</span>
          <h1>{project.title}</h1>
          <p>脚本、世界設定、参照素材、生成テイクを一つのRevisionで管理します。</p>
        </div>
        <div className="heading-actions">
          <button className="button primary" type="button" onClick={onOpenProduction}>動画制作へ</button>
        </div>
      </div>

      <section className="hero-card">
        <div className="hero-art">
          {project.thumbnailUrl ? <img className="project-thumbnail" src={project.thumbnailUrl} alt={`${project.title} プロジェクトサムネイル`} /> : <>
            <div className="orbit-line one" />
            <div className="orbit-line two" />
            <span className="tower tower-a" />
            <span className="tower tower-b" />
            <span className="hero-person person-a" />
            <span className="hero-person person-b" />
            <div className="hero-caption"><span>EPISODE 01</span><strong>{project.episodeTitle}</strong></div>
          </>}
        </div>
        <div className="hero-copy">
          <span className="status-pill approved">構成承認済み</span>
          <h2>{project.logline}</h2>
          <p>{project.summary}</p>
          <div className="metric-row">
            <div><span>1</span><small>Active episode</small></div>
            <div><span>{shotCount}</span><small>Shots</small></div>
            <div><span>{assetCount}</span><small>World assets</small></div>
            <div><span>{takeCount}</span><small>H3 takes</small></div>
          </div>
        </div>
      </section>

      <div className="overview-grid">
        <section className="panel preparation-panel">
          <div className="panel-head"><div><span className="eyebrow">PREPARATION</span><h3>制作準備</h3></div><strong>{[shotCount > 0, assetCount > 0, takeCount > 0].filter(Boolean).length} / 3</strong></div>
          <div className="check-list">
            <button type="button" onClick={onOpenScript}><span className={`check ${shotCount > 0 ? "done" : "current"}`}>{shotCount > 0 ? "✓" : "1"}</span><span><strong>概要と脚本</strong><small>{shotCount} shots · Project Core</small></span><em>→</em></button>
            <button type="button" onClick={onOpenWorld}><span className={`check ${assetCount > 0 ? "done" : "current"}`}>{assetCount > 0 ? "✓" : "2"}</span><span><strong>参照素材</strong><small>{assetCount} assets · uploaded / metadata</small></span><em>→</em></button>
            <button type="button" onClick={onOpenProduction}><span className={`check ${takeCount > 0 ? "done" : "current"}`}>{takeCount > 0 ? "✓" : "3"}</span><span><strong>動画テイク</strong><small>{takeCount} real H3 takes</small></span><em>→</em></button>
          </div>
        </section>

        <section className="panel activity-panel">
          <div className="panel-head"><div><span className="eyebrow">PROJECT CORE</span><h3>現在の保存状態</h3></div></div>
          <div className="activity-list">
            <div><span className="activity-icon codex">R</span><p><strong>Project Revision {revision}</strong><small>現在のUIはこのRevisionへCAS保存します</small></p></div>
            <div><span className="activity-icon user">S</span><p><strong>{shotCount} Script shots / {assetCount} Assets</strong><small>再読込時はProject Coreから復元</small></p></div>
            <div><span className="activity-icon render">H3</span><p><strong>{takeCount} H3 takes</strong><small>実ジョブだけを表示。デモTakeは作成しません</small></p></div>
          </div>
        </section>
      </div>
    </div>
  );
}

function ScriptView({
  project,
  shots,
  selectedShotId,
  onSelectShot,
  editing,
  draft,
  saving,
  onBeginEdit,
  onDraftChange,
  onSave,
  onCancel,
  onProduction,
}: {
  project: Project;
  shots: Shot[];
  selectedShotId: string;
  onSelectShot: (id: string) => void;
  editing: boolean;
  draft: Shot | null;
  saving: boolean;
  onBeginEdit: () => void;
  onDraftChange: (shot: Shot) => void;
  onSave: (event: FormEvent<HTMLFormElement>) => void;
  onCancel: () => void;
  onProduction: () => void;
}) {
  const selected = shots.find((shot) => shot.id === selectedShotId) ?? shots[0] ?? null;
  const displayed = editing && draft?.id === selected?.id ? draft : selected;
  return (
    <div className="view script-view">
      <div className="view-heading">
        <div><span className="eyebrow">EPISODE 01 / SCRIPT</span><h1>{project.episodeTitle}</h1><p>脚本意図とH3向け生成記述を分離して管理します。</p></div>
        <div className="heading-actions"><button className="button primary" type="button" disabled={!selected} onClick={onProduction}>選択Shotを制作</button></div>
      </div>
      <form className="script-layout" onSubmit={onSave}>
        <section className="script-document">
          <div className="document-toolbar"><span>Scene · INT/EXT {project.sceneTitle}</span><div>{editing ? <><button type="button" disabled={saving} onClick={onCancel}>キャンセル</button><button type="submit" disabled={saving}>{saving ? "保存中…" : "保存"}</button></> : <button type="button" disabled={!selected} onClick={onBeginEdit}>編集</button>}</div></div>
          <div className="scene-intent"><span>SCENE INTENT</span><p>{project.logline}</p></div>
          {shots.length === 0 && <div className="empty-state"><strong>Shotがありません</strong><p>Project作成時の初期Shot、またはCodexが追加したShotがここに表示されます。</p></div>}
          {shots.map((shot) => (
            <button key={shot.id} className={`script-shot ${selectedShotId === shot.id ? "active" : ""}`} type="button" onClick={() => onSelectShot(shot.id)}>
              <span className="script-shot-number">{String(shot.order ?? 0).padStart(2, "0")}</span>
              <span className="script-shot-body">
                <span><strong>{shot.title}</strong><em>{shot.status}</em></span>
                <p>{shot.action || shot.storyPurpose || "アクション未設定"}</p>
                {shot.dialogue && <blockquote><span>台詞</span>{shot.dialogue}</blockquote>}
                <small>Camera: {shot.camera || "未設定"} · {shot.durationFrames === 345 ? "14.4s" : shot.durationFrames === 243 ? "10.1s" : "5.2s"}</small>
              </span>
              <span className="script-shot-arrow">→</span>
            </button>
          ))}
        </section>
        <aside className="script-inspector">
          <span className="eyebrow">SHOT INSPECTOR</span>
          {displayed ? <>
            <h3>{displayed.label}</h3>
            <label>Shotタイトル<input disabled={!editing} value={displayed.title} onChange={(event) => draft && onDraftChange({ ...draft, title: event.target.value })} /></label>
            <label>物語上の目的<textarea disabled={!editing} value={displayed.storyPurpose ?? ""} onChange={(event) => draft && onDraftChange({ ...draft, storyPurpose: event.target.value })} /></label>
            <label>アクション<textarea disabled={!editing} value={displayed.action ?? ""} onChange={(event) => draft && onDraftChange({ ...draft, action: event.target.value })} /></label>
            <label>演技<textarea disabled={!editing} value={displayed.performance ?? ""} onChange={(event) => draft && onDraftChange({ ...draft, performance: event.target.value })} /></label>
            <label>台詞<textarea disabled={!editing} value={displayed.dialogue ?? ""} onChange={(event) => draft && onDraftChange({ ...draft, dialogue: event.target.value })} /></label>
            <label>SE / Soundscape<textarea disabled={!editing} value={displayed.soundscape ?? ""} onChange={(event) => draft && onDraftChange({ ...draft, soundscape: event.target.value })} /></label>
            <label>BGM<select disabled={!editing} value={displayed.musicPolicy ?? "auto"} onChange={(event) => draft && onDraftChange({ ...draft, musicPolicy: event.target.value })}><option value="none">なし</option><option value="auto">自動</option></select></label>
            <label>カメラ<textarea disabled={!editing} value={displayed.camera ?? ""} onChange={(event) => draft && onDraftChange({ ...draft, camera: event.target.value })} /></label>
            <div className="inspector-pair"><label>生成尺<select disabled={!editing} value={displayed.durationFrames ?? 124} onChange={(event) => draft && onDraftChange({ ...draft, durationFrames: Number(event.target.value) as 124 | 243 | 345 })}><option value="124">5.2秒</option><option value="243">10.1秒</option><option value="345">14.4秒</option></select></label><label>状態<select disabled={!editing} value={displayed.status} onChange={(event) => draft && onDraftChange({ ...draft, status: event.target.value as Shot["status"] })}><option value="draft">Draft</option><option value="planned">Planned</option><option value="active">Active</option><option value="approved">Approved</option></select></label></div>
            <div className="save-state"><span className={editing ? "dirty" : "saved"} />{editing ? "未保存の編集" : "Project Coreに保存済み"}</div>
          </> : <div className="empty-state"><strong>Shot未選択</strong><p>脚本Shotを追加すると編集できます。</p></div>}
        </aside>
      </form>
    </div>
  );
}

function WorldView({ assets, selectedAsset, onSelectAsset, onCaptionChange, onCaptionCommit, onToggleLock, onCreateAsset, onEditAsset, captionDirty }: { assets: Asset[]; selectedAsset: Asset | null; onSelectAsset: (id: string) => void; onCaptionChange: (value: string) => void; onCaptionCommit: () => void; onToggleLock: () => void; onCreateAsset: () => void; onEditAsset: () => void; captionDirty: boolean }) {
  return (
    <div className="view world-view">
      <div className="view-heading">
        <div><span className="eyebrow">WORLD BIBLE / ASSET VAULT</span><h1>参照素材とキャプション</h1><p>人間向け説明、連続性ルール、H3用Captionを分けて保存します。</p></div>
        <div className="heading-actions"><button className="button ghost" type="button" onClick={onCreateAsset}>素材を読み込む</button><button className="button primary" type="button" onClick={onCreateAsset}>＋ 素材を追加</button></div>
      </div>
      <div className="world-layout">
        <section className="asset-grid" aria-label="素材一覧">
          {assets.map((asset) => (
            <button key={asset.id} className={`asset-card ${selectedAsset?.id === asset.id ? "active" : ""}`} type="button" onClick={() => onSelectAsset(asset.id)}>
              <span className={`asset-art ${asset.tone}`}>{asset.contentUrl && asset.mediaKind === "image" ? <img src={asset.contentUrl} alt="" /> : asset.contentUrl && asset.mediaKind === "video" ? <video src={asset.contentUrl} muted preload="metadata" /> : <span className="asset-silhouette" />}<em>{assetKindLabel(asset.kind)}</em>{asset.locked && <b>LOCKED</b>}</span>
              <span className="asset-card-copy"><span><strong>{asset.name}</strong><code>{asset.id}</code></span><small>{asset.role}</small><p>{asset.caption}</p></span>
            </button>
          ))}
          <button className="asset-add-card" type="button" onClick={onCreateAsset}><span>＋</span><strong>素材を追加</strong><small>画像・動画・音声</small></button>
        </section>
        <aside className="asset-inspector">
          {selectedAsset ? <>
            <div className={`asset-inspector-art ${selectedAsset.tone}`}>{selectedAsset.contentUrl && selectedAsset.mediaKind === "image" ? <img src={selectedAsset.contentUrl} alt={selectedAsset.name} /> : selectedAsset.contentUrl && selectedAsset.mediaKind === "video" ? <video src={selectedAsset.contentUrl} controls preload="metadata" /> : selectedAsset.contentUrl && selectedAsset.mediaKind === "audio" ? <audio src={selectedAsset.contentUrl} controls preload="metadata" /> : <span className="asset-silhouette large" />}<em>{selectedAsset.name}</em></div>
            <div className="asset-title-row"><div><span>{assetKindLabel(selectedAsset.kind)}{selectedAsset.mediaKind && selectedAsset.mediaKind !== "none" ? ` · ${selectedAsset.mediaKind}` : ""}</span><h3>{selectedAsset.name}</h3><code>{selectedAsset.id}</code></div><div><button type="button" disabled={captionDirty} onClick={onEditAsset}>編集</button><button className={selectedAsset.locked ? "locked" : ""} type="button" disabled={captionDirty} onClick={onToggleLock}>{selectedAsset.locked ? "固定中" : "固定する"}</button></div></div>
            <label>人間向けキャプション<textarea value={selectedAsset.caption} onChange={(event) => onCaptionChange(event.target.value)} /></label>
            <div className="asset-inline-actions"><span>{captionDirty ? "未保存の編集" : "Project Coreに保存済み"}</span><button type="button" disabled={!captionDirty} onClick={onCaptionCommit}>{captionDirty ? "キャプションを保存" : "保存済み"}</button></div>
            <label>H3用キャプション<textarea value={selectedAsset.promptCaption} readOnly /><small>素材メタデータとして保存。日本語Promptの書式化はNIKU H STUDIO既存communityフローが担当します。</small></label>
            <div className="usage-block"><span>H3参照</span><p>{selectedAsset.contentUrl ? `@${selectedAsset.name} は送信時に正式なH3参照タグへ束縛されます。` : "ファイル未登録のため、キャプションだけがPrompt文脈へ展開されます。"}</p></div>
          </> : <div className="empty-state asset-empty"><strong>素材はまだありません</strong><p>人間がアップロードするか、CodexがGPT-image-2で生成した素材をここへ追加できます。</p><button className="button primary" type="button" onClick={onCreateAsset}>最初の素材を追加</button></div>}
        </aside>
      </div>
    </div>
  );
}

type ProductionViewProps = {
  shots: Shot[];
  assets: Asset[];
  prompt: string;
  revision: number;
  onPromptChange: (value: string) => void;
  onPromptCommit: () => void;
  takes: Take[];
  selectedTake: Take;
  selectedTakesByShot: Readonly<Record<string, Take>>;
  selectedShot: Shot;
  onPreviewTake: (id: string) => void;
  onSelectTake: (id: string) => void;
  onDownloadTake: (take: Take) => void;
  onRender: () => void;
  rendering: boolean;
  onSelectShot: (id: string) => void;
  promptDirty: boolean;
  carryOverEndFrame: boolean;
  onCarryOverEndFrameChange: (value: boolean) => void;
  continuityAssetReady: boolean;
  settings: GenerationSettings;
  resolutionCatalog: ResolutionCatalog;
  onSettingsChange: (settings: GenerationSettings) => void;
};

function TakeThumbnail({ take, className = "" }: { readonly take?: Take; readonly className?: string }) {
  const [failedArtifactKey, setFailedArtifactKey] = useState<string | null>(null);
  const artifactKey = take?.artifactUrl ? `${take.id}:${take.artifactUrl}` : "";
  const mediaFailed = artifactKey !== "" && failedArtifactKey === artifactKey;

  if (take?.artifactUrl && !mediaFailed) {
    return (
      <video
        className={className}
        src={take.artifactUrl}
        muted
        playsInline
        preload="metadata"
        aria-label={`${take.label} preview`}
        onLoadedMetadata={(event) => {
          try {
            event.currentTarget.currentTime = 0;
          } catch {
            // Some browsers do not allow seeking until the first media frame is ready.
          }
        }}
        onError={() => setFailedArtifactKey(artifactKey)}
      />
    );
  }
  return <span className={`${className} thumbnail-placeholder`.trim()} aria-hidden="true"><span className="take-thumb-scene" /></span>;
}

function ProductionView({ shots, assets, prompt, revision, onPromptChange, onPromptCommit, takes, selectedTake, selectedTakesByShot, selectedShot, onPreviewTake, onSelectTake, onDownloadTake, onRender, rendering, onSelectShot, promptDirty, carryOverEndFrame, onCarryOverEndFrameChange, continuityAssetReady, settings, resolutionCatalog, onSettingsChange }: ProductionViewProps) {
  const fileAssets = assets.filter((asset) => asset.mediaKind && asset.mediaKind !== "none" && asset.contentUrl);
  const renderMode = carryOverEndFrame && continuityAssetReady ? "i2v" : fileAssets.length > 0 ? "omni" : "t2v";
  const durationLabel = settings.durationFrames === 345 ? "14.4s" : settings.durationFrames === 243 ? "10.1s" : "5.2s";
  const selectedAspect = resolutionCatalog.aspects.find((aspect) => aspect.id === settings.aspectRatio)
    ?? resolutionCatalog.aspects[0];
  const selectedResolution = selectedAspect?.presets[settings.resolutionQuality]
    ?? selectedAspect?.presets[resolutionCatalog.defaultQuality];
  const rawPromptMode = settings.promptProcessingMode === "raw_en";
  const accelerationNote = settings.steps < 12
    ? "Draft（12 steps未満）ではH3が自動的に高速化なしにします。"
    : settings.acceleration === "off"
      ? "高速化なし。品質比較の基準です。"
      : settings.acceleration === "community"
        ? "通常はこちら。控えめな近似で、品質と速度のバランスを取りたいときに使います。"
        : settings.acceleration === "conservative"
          ? "さらに慎重な高速化。品質を優先したい場合に使います。"
          : "速度優先の高速化。試作向けで、品質を確認してから本番採用してください。";
  const updateSettings = (patch: Partial<GenerationSettings>) => onSettingsChange({ ...settings, ...patch });
  const updatePromptMode = (promptProcessingMode: GenerationSettings["promptProcessingMode"]) => {
    onSettingsChange(promptProcessingMode === "raw_en"
      ? { ...settings, promptProcessingMode, style: "natural", standaloneAudioPolicy: "dialogue_priority", audioPreset: "auto", dialogue: "", soundscape: "", musicPolicy: "auto" }
      : { ...settings, promptProcessingMode });
  };
  const hasTake = selectedTake.id !== EMPTY_TAKE.id;
  return (
    <div className="production-view">
      <div className="production-head">
        <div><span className="eyebrow">EP01 / SCENE 03 / {selectedShot.label.toUpperCase()}</span><h2>{selectedShot.title}</h2></div>
        <div className="production-meta"><span>{durationLabel}</span><span>24fps</span><span>{selectedResolution ? `${selectedResolution.width}×${selectedResolution.height}` : "H3 resolution"}</span><span>{renderMode.toUpperCase()}</span></div>
      </div>
      <div className="production-grid">
        <section className="prompt-workbench">
          <div className="prompt-head"><div><span>H3 GENERATION PROMPT</span><em>Revision {revision}</em></div><button type="button" disabled={!promptDirty} onClick={onPromptCommit}>{promptDirty ? "Promptを保存" : "保存済み"}</button></div>
          <div className="reference-strip">{assets.map((asset) => <span key={asset.id}><span className={`mini-avatar ${asset.tone}`} />@{asset.name}{asset.contentUrl ? " · file" : " · caption"}</span>)}</div>
          <textarea className="prompt-editor" value={prompt} onChange={(event) => onPromptChange(event.target.value)} aria-label="H3生成プロンプト" />
          <div className="negative-rules"><span>IDENTITY GUARDS</span><p>{assets.filter((asset) => asset.locked).map((asset) => `${asset.name}の固定外見`).join(" · ")} · 文字を生成しない · 時間的不連続を避ける</p></div>
          <div className="generation-settings generation-settings-expanded">
            <label>Mode<select disabled value={renderMode}><option value="t2v">Text to video</option><option value="i2v">Image to video</option><option value="omni">Omni reference</option></select></label>
            <label>Style<select value={settings.style} disabled={rawPromptMode} onChange={(event) => updateSettings({ style: event.target.value as GenerationSettings["style"] })}><option value="natural">Natural</option><option value="cinematic">Cinematic</option><option value="photoreal">Photoreal</option><option value="anime">Anime</option><option value="illustration">Illustration</option><option value="product">Product</option><option value="moody">Moody</option></select></label>
            <label>Aspect ratio<select value={settings.aspectRatio} onChange={(event) => updateSettings({ aspectRatio: event.target.value })}>{resolutionCatalog.aspects.map((aspect) => <option key={aspect.id} value={aspect.id}>{aspect.label}</option>)}</select></label>
            <label>Resolution<select value={settings.resolutionQuality} onChange={(event) => updateSettings({ resolutionQuality: event.target.value })}>{resolutionCatalog.qualities.filter((quality) => selectedAspect?.presets[quality.id]).map((quality) => <option key={quality.id} value={quality.id}>{quality.label} · {selectedAspect?.presets[quality.id]?.label}</option>)}</select></label>
            <label>Duration<select value={settings.durationFrames} onChange={(event) => updateSettings({ durationFrames: Number(event.target.value) as GenerationSettings["durationFrames"] })}><option value="124">5.2s · 124 frames</option><option value="243">10.1s · 243 frames</option><option value="345">14.4s · 345 frames</option></select></label>
            <label>Quality<select value={settings.steps} onChange={(event) => updateSettings({ steps: Number(event.target.value) })}><option value="8">Draft · 8 steps</option><option value="20">Standard · 20 steps</option><option value="30">High · 30 steps</option></select></label>
            <label>Seed<span className="seed-control"><input type="number" min="0" max="9007199254740991" value={settings.seed} onChange={(event) => updateSettings({ seed: Math.max(0, Number(event.target.value) || 0) })} /><button type="button" onClick={() => updateSettings({ seed: Math.floor(Math.random() * 2147483647) })}>↻</button></span></label>
            <label className="acceleration-setting">高速化<select aria-describedby="story-acceleration-note" value={settings.acceleration} onChange={(event) => updateSettings({ acceleration: event.target.value as GenerationSettings["acceleration"] })}><option value="off">高速化なし（比較用）</option><option value="community">おすすめ：控えめ（通常）</option><option value="conservative">より慎重（品質優先）</option><option value="balanced">速度優先（品質を確認）</option></select><small id="story-acceleration-note">{accelerationNote}</small></label>
            <label>Reference size<select value={settings.refImageSize} onChange={(event) => updateSettings({ refImageSize: event.target.value as GenerationSettings["refImageSize"] })}><option value="match">Match output</option><option value="max">Max quality</option></select></label>
            <label>Prompt flow<select value={settings.promptProcessingMode} onChange={(event) => updatePromptMode(event.target.value as GenerationSettings["promptProcessingMode"])}><option value="community">Community · Japanese</option><option value="raw_en">Raw English</option></select></label>
          </div>
          <details className="audio-settings"><summary>Audio settings · NIKU H STUDIO</summary><div className="audio-settings-grid">
            <label>Standalone audio<select value={settings.standaloneAudioPolicy} disabled={rawPromptMode} onChange={(event) => updateSettings({ standaloneAudioPolicy: event.target.value as GenerationSettings["standaloneAudioPolicy"] })}><option value="dialogue_priority">Dialogue priority</option><option value="full_content">Full content</option></select></label>
            <label>Audio preset<select value={settings.audioPreset} disabled={rawPromptMode} onChange={(event) => updateSettings({ audioPreset: event.target.value as GenerationSettings["audioPreset"] })}><option value="auto">Auto</option><option value="dialogue">Dialogue</option><option value="ambience">Ambience</option><option value="effects">Effects</option><option value="music">Music</option><option value="quiet">Quiet</option></select></label>
            <label>BGM<select value={settings.musicPolicy} disabled={rawPromptMode} onChange={(event) => updateSettings({ musicPolicy: event.target.value as GenerationSettings["musicPolicy"] })}><option value="auto">Auto</option><option value="none">None</option><option value="subtle">Subtle</option><option value="prominent">Prominent</option></select></label>
            <label>Audio gain<select value={settings.audioGainDb} onChange={(event) => updateSettings({ audioGainDb: Number(event.target.value) })}><option value="-12">−12 dB</option><option value="-6">−6 dB</option><option value="0">0 dB</option><option value="3">+3 dB</option><option value="6">+6 dB</option></select></label>
            <label className="audio-text-field">Dialogue<textarea maxLength={800} disabled={rawPromptMode} value={settings.dialogue} onChange={(event) => updateSettings({ dialogue: event.target.value })} /></label>
            <label className="audio-text-field">Soundscape<textarea maxLength={800} disabled={rawPromptMode} value={settings.soundscape} onChange={(event) => updateSettings({ soundscape: event.target.value })} /></label>
          </div></details>
          <label className="continuity-toggle"><input type="checkbox" checked={carryOverEndFrame} onChange={(event) => onCarryOverEndFrameChange(event.target.checked)} /><span>前カットのエンドフレームを次カットの先頭へ引き継ぐ</span><small>{carryOverEndFrame ? (continuityAssetReady ? "終端フレームをi2vの先頭画像へ束縛" : "前カットの生成完了と終端フレーム抽出を待機") : "次カットは独立した先頭フレームで生成"}</small></label>
          <div className="render-action"><div><span>{promptDirty ? "Promptを保存中 / 未保存" : "NIKU H STUDIO既存communityフロー"}</span><small>context assets {assets.length} · H3 uploads {fileAssets.length}</small></div><button type="button" onClick={onRender} disabled={rendering || promptDirty || !selectedShot.id || !prompt.trim()}>{rendering ? "生成中…" : promptDirty ? "Prompt保存後に生成" : "NIKU H STUDIOで新しいTakeを生成"}</button></div>
        </section>
        <section className="player-stage">
          <div className={`cinema-frame ${selectedTake.tone} ${selectedTake.artifactUrl ? "has-media" : ""} ${!hasTake ? "empty" : ""}`}>
            {selectedTake.artifactUrl ? (
              <video className="take-video" src={selectedTake.artifactUrl} controls preload="metadata" playsInline />
            ) : hasTake ? (
              <><div className="frame-moon" /><span className="frame-gantry g1" /><span className="frame-gantry g2" /><span className="frame-console" /><span className="frame-actor a1" /><span className="frame-actor a2" /></>
            ) : <div className="take-empty"><strong>Take未生成</strong><span>実H3ジョブを投入すると結果がここに表示されます。</span></div>}
            {selectedTake.state === "rendering" && <div className="render-overlay"><span className="render-spinner" /><strong>H3 RENDERING</strong><small>Promptと参照素材を固定済み</small></div>}
            {hasTake && <div className="frame-label"><span>{selectedTake.label}</span><em>{stateLabel(selectedTake.state)}</em></div>}
          </div>
          <div className="take-review"><div>{hasTake && <span className={`take-state ${selectedTake.state}`}>{stateLabel(selectedTake.state)}</span>}<strong>{selectedTake.score ? `Review score ${selectedTake.score}` : hasTake ? "解析待ち" : "実生成待ち"}</strong><p>{selectedTake.note}</p></div>{hasTake && (selectedTake.state === "candidate" || selectedTake.state === "selected") && <button className="button primary" type="button" disabled={selectedTake.state === "selected"} onClick={() => onSelectTake(selectedTake.id)}>{selectedTake.state === "selected" ? "採用中" : "このTakeを採用"}</button>}</div>
        </section>
        <aside className="take-rail">
          <div className="take-rail-head"><span>TAKES</span><em>{takes.length}</em></div>
          {takes.map((take) => (
            <div key={take.id} className={`take-card ${selectedTake.id === take.id ? "active" : ""}`}>
              <button className="take-card-main" type="button" onClick={() => onPreviewTake(take.id)}>
                <span className={`take-thumb ${take.tone}`}>
                  {take.state === "rendering" ? <span className="render-spinner small" /> : <TakeThumbnail take={take} className="take-thumb-media" />}
                </span>
                <span className="take-card-copy"><span><strong>{take.label}</strong><em className={take.state}>{stateLabel(take.state)}</em></span><small>seed {take.seed}</small>{take.score && <b>{take.score}</b>}</span>
              </button>
              {take.artifactUrl && take.state !== "rendering" && <button className="take-download" type="button" onClick={() => onDownloadTake(take)} aria-label={`${take.label}をダウンロード`}>↓ DL</button>}
            </div>
          ))}
          {takes.length === 0 && <div className="take-rail-empty">実H3 Takeはまだありません</div>}
        </aside>
      </div>
      <div className="beat-timeline">
        <div className="timeline-title"><span>ACTIVE SCENE</span><small>{shots.length} shots</small></div>
        <div className="timeline-shots">{shots.map((shot, index) => {
          const selectedForShot = selectedTakesByShot[shot.id];
          return (
            <div key={shot.id} className="timeline-shot">
              <button className={`timeline-shot-main ${shot.id === selectedShot.id ? "active" : ""}`} type="button" onClick={() => onSelectShot(shot.id)}>
                <span className={`timeline-thumb t${index + 1}`}><TakeThumbnail take={selectedForShot} className="timeline-thumb-media" /></span>
                <small>{String(shot.order ?? index + 1).padStart(2, "0")}</small>
                <strong>{shot.title}</strong>
                <em>{selectedForShot ? "採用" : "未採用"} · {shot.durationFrames === 345 ? "14.4s" : shot.durationFrames === 243 ? "10.1s" : "5.2s"}</em>
              </button>
              {selectedForShot?.artifactUrl && <button className="timeline-download" type="button" onClick={() => onDownloadTake(selectedForShot)} aria-label={`${shot.title}の採用テイクをダウンロード`}>↓</button>}
            </div>
          );
        })}</div>
      </div>
    </div>
  );
}
