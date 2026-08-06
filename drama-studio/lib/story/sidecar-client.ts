const DEFAULT_STORY_SIDECAR_ORIGIN = "http://127.0.0.1:7864";
const STORY_MUTATION_HEADER = "X-H3-Story-Request";

export type StorySidecarHealth = {
  readonly status: "ok" | "degraded";
  readonly version: string;
  readonly projectCore: "ready" | "unavailable";
  readonly h3: "ready" | "busy" | "offline" | "incompatible";
};

export type StoryProjectSummary = {
  readonly projectId: string;
  readonly code: string;
  readonly title: string;
  readonly name?: string;
  readonly projectRoot: string;
  readonly revision: number;
  readonly stateDigest: string;
  readonly focus: Readonly<Record<string, unknown>>;
  readonly metadata?: Readonly<Record<string, unknown>>;
  readonly createdAt?: string;
  readonly updatedAt?: string;
};

export type StoryCreateProjectRequest = {
  readonly title: string;
  readonly code: string;
  readonly subtitle: string;
  readonly episodeTitle: string;
  readonly sceneTitle: string;
  readonly shotTitle: string;
  readonly logline: string;
  readonly summary: string;
};

export type StoryCreateProjectResult = {
  readonly projectId: string;
  readonly projectRoot: string;
  readonly code: string;
  readonly title: string;
  readonly revision: number;
  readonly stateDigest: string;
  readonly focus: Readonly<Record<string, unknown>>;
  readonly metadata?: Readonly<Record<string, unknown>>;
};

export type StoryAssetCategory = "character" | "environment" | "prop" | "reference";

export type StoryCreateAssetFields = {
  readonly name: string;
  readonly category: StoryAssetCategory;
  readonly role: string;
  readonly caption: string;
  readonly promptCaption: string;
  readonly locked: boolean;
  readonly source: "human" | "codex_gpt_image_2";
};

export type StoryAssetMutationResult = {
  readonly projectId: string;
  readonly assetId: string;
  readonly revision: number;
  readonly stateDigest: string;
  readonly asset: Readonly<Record<string, unknown>>;
};

export type StorySessionBinding = {
  readonly sessionId: string;
  readonly projectId: string;
  readonly agentId: string;
  readonly boundRevision: number;
  readonly stateDigest: string;
  readonly focus: Readonly<Record<string, unknown>>;
  readonly activatedAt: string;
};

export type StoryPatchOperation = {
  readonly op: "upsert_entity" | "delete_entity" | "set_focus" | "set_approval";
  readonly entityType?: string;
  readonly entityId?: string;
  readonly payload?: Readonly<Record<string, unknown>>;
};

export type StoryCommitRequest = {
  readonly sessionId: string;
  readonly expectedRevision: number;
  readonly message: string;
  readonly operations: readonly StoryPatchOperation[];
};

export type StoryCommitResult = {
  readonly projectId: string;
  readonly previousRevision: number;
  readonly revision: number;
  readonly stateDigest: string;
  readonly eventId: string;
};

export type StoryHandoffResult = {
  readonly handoffId: string;
  readonly projectId: string;
  readonly projectRevision: number;
  readonly stateDigest: string;
  readonly path: string;
  readonly capsule: Readonly<Record<string, unknown>>;
};

export class StorySidecarError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: unknown;

  constructor(message: string, status: number, code = "STORY_SIDECAR_ERROR", details?: unknown) {
    super(message);
    this.name = "StorySidecarError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export type StoryFetch = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>;

function validateOrigin(origin: string): string {
  const url = new URL(origin);
  if (
    url.protocol !== "http:" ||
    url.hostname !== "127.0.0.1" ||
    url.port !== "7864" ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error("Story sidecar origin must be exactly http://127.0.0.1:7864");
  }
  return url.origin;
}

function safePathSegment(value: string, label: string): string {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) {
    throw new Error(`${label} contains unsafe characters`);
  }
  return value;
}

export class StorySidecarClient {
  readonly origin: string;
  readonly timeoutMilliseconds: number;
  private readonly fetchImpl: StoryFetch;

  constructor(options: {
    readonly origin?: string;
    readonly timeoutMilliseconds?: number;
    readonly fetchImpl?: StoryFetch;
  } = {}) {
    this.origin = validateOrigin(options.origin ?? DEFAULT_STORY_SIDECAR_ORIGIN);
    this.timeoutMilliseconds = options.timeoutMilliseconds ?? 15_000;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  async health(): Promise<StorySidecarHealth> {
    return this.request("/api/health");
  }

  async listProjects(): Promise<readonly StoryProjectSummary[]> {
    return this.request("/api/projects");
  }

  async createProject(request: StoryCreateProjectRequest): Promise<StoryCreateProjectResult> {
    return this.request("/api/projects", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  async activateProject(projectId: string, agentId: string): Promise<StorySessionBinding> {
    const id = safePathSegment(projectId, "projectId");
    return this.request(`/api/projects/${id}/activate`, {
      method: "POST",
      body: JSON.stringify({ agentId }),
    });
  }

  async getContext(projectId: string, sessionId: string): Promise<Readonly<Record<string, unknown>>> {
    const id = safePathSegment(projectId, "projectId");
    const session = safePathSegment(sessionId, "sessionId");
    return this.request(`/api/projects/${id}/context?sessionId=${encodeURIComponent(session)}`);
  }

  async commitPatch(projectId: string, request: StoryCommitRequest): Promise<StoryCommitResult> {
    const id = safePathSegment(projectId, "projectId");
    return this.request(`/api/projects/${id}/patches`, {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  async createHandoff(projectId: string, sessionId: string): Promise<StoryHandoffResult> {
    const id = safePathSegment(projectId, "projectId");
    return this.request(`/api/projects/${id}/handoffs`, {
      method: "POST",
      body: JSON.stringify({ sessionId }),
    });
  }

  async createAsset(
    projectId: string,
    sessionId: string,
    expectedRevision: number,
    fields: StoryCreateAssetFields,
  ): Promise<StoryAssetMutationResult> {
    const id = safePathSegment(projectId, "projectId");
    return this.request(`/api/projects/${id}/assets`, {
      method: "POST",
      body: JSON.stringify({ sessionId, expectedRevision, ...fields }),
    });
  }

  async uploadAsset(
    projectId: string,
    sessionId: string,
    expectedRevision: number,
    fields: StoryCreateAssetFields,
    file: File,
  ): Promise<StoryAssetMutationResult> {
    const id = safePathSegment(projectId, "projectId");
    const form = new FormData();
    form.set("sessionId", sessionId);
    form.set("expectedRevision", String(expectedRevision));
    form.set("name", fields.name);
    form.set("category", fields.category);
    form.set("role", fields.role);
    form.set("caption", fields.caption);
    form.set("promptCaption", fields.promptCaption);
    form.set("locked", fields.locked ? "true" : "false");
    form.set("source", fields.source);
    form.set("file", file, file.name);
    return this.request(`/api/projects/${id}/assets`, {
      method: "POST",
      body: form,
    });
  }

  assetContentUrl(projectId: string, assetId: string, sessionId: string): string {
    const project = safePathSegment(projectId, "projectId");
    const asset = safePathSegment(assetId, "assetId");
    const session = safePathSegment(sessionId, "sessionId");
    return `${this.origin}/api/projects/${project}/assets/${asset}/content?sessionId=${encodeURIComponent(session)}`;
  }

  async getH3State(): Promise<Readonly<Record<string, unknown>>> {
    return this.request("/api/h3/state");
  }

  async getH3Job(jobId: string): Promise<Readonly<Record<string, unknown>>> {
    if (!/^[a-f0-9]{12}$/.test(jobId)) {
      throw new Error("h3JobId must be exactly 12 lowercase hexadecimal characters");
    }
    return this.request(`/api/h3/jobs/${jobId}`);
  }

  async submitRender(
    projectId: string,
    shotId: string,
    body: Readonly<Record<string, unknown>>,
  ): Promise<Readonly<Record<string, unknown>>> {
    const project = safePathSegment(projectId, "projectId");
    const shot = safePathSegment(shotId, "shotId");
    return this.request(`/api/projects/${project}/shots/${shot}/renders`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  async syncRender(
    projectId: string,
    localJobId: string,
    body: {
      readonly sessionId: string;
      readonly expectedRevision: number;
      readonly downloadResult?: boolean;
      readonly extractEndFrame?: boolean;
    },
  ): Promise<Readonly<Record<string, unknown>>> {
    const project = safePathSegment(projectId, "projectId");
    const job = safePathSegment(localJobId, "localJobId");
    return this.request(`/api/projects/${project}/renders/${job}/sync`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMilliseconds);
    const method = (init.method ?? "GET").toUpperCase();
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body !== undefined && !(init.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
    if (method !== "GET" && method !== "HEAD") headers.set(STORY_MUTATION_HEADER, "1");

    try {
      const response = await this.fetchImpl(`${this.origin}${path}`, {
        ...init,
        method,
        headers,
        signal: controller.signal,
      });
      const contentType = response.headers.get("content-type") ?? "";
      const payload = contentType.includes("application/json")
        ? await response.json()
        : await response.text();

      if (!response.ok) {
        const errorPayload = payload as {
          readonly error?: { readonly code?: string; readonly message?: string; readonly details?: unknown };
        };
        throw new StorySidecarError(
          errorPayload?.error?.message ?? `Story sidecar request failed (${response.status})`,
          response.status,
          errorPayload?.error?.code,
          errorPayload?.error?.details,
        );
      }
      return payload as T;
    } catch (error) {
      if (error instanceof StorySidecarError) throw error;
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new StorySidecarError("Story sidecar request timed out", 408, "STORY_SIDECAR_TIMEOUT");
      }
      throw new StorySidecarError(
        error instanceof Error ? error.message : "Story sidecar is unavailable",
        0,
        "STORY_SIDECAR_UNAVAILABLE",
      );
    } finally {
      clearTimeout(timer);
    }
  }
}

export { DEFAULT_STORY_SIDECAR_ORIGIN, STORY_MUTATION_HEADER };
