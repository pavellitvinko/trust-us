// Types and calls for the Follow the Fake API (src/ftf/web/app.py).

export type LinkKind = "campaign" | "kit" | "duplicate" | "possible";

export type CheckState = "not_run" | "queued" | "running" | "complete" | "partial" | "unavailable" | "retained" | "skipped" | "failed" | "deferred" | "on_demand" | "not_ready" | "not_applicable";
export interface ItemCheck {
  canRun?: boolean;
  id: string; label: string; state: CheckState; status: string; message: string;
  hasResult: boolean; updatedAt: string | null; runId: string | null; observations: number;
}
export interface ItemProcessing {
  canRun?: boolean;
  state: CheckState; status: string; complete: number; total: number; current: string | null;
  checks: ItemCheck[];
}

export interface Row {
  id: string;
  title: string;
  type: "Page" | "Image" | "Video";
  host: string;
  case: string;
  caseId: string;
  added: string;
  campaign: string;
  campaignId: string;
  kit: string;
  kitId: string;
  duplicateOf: string;
  comparable: boolean;
  signs: number;
  checked: boolean;
  links: number;
  possible: number;
  picture: boolean;
  note: string;
  processing: ItemProcessing;
}

export interface MapNode {
  id: string;
  x: number;
  y: number;
  title: string;
  case: string;
  signs: number;
  picture: boolean;
}

export interface MapEdge {
  a: string;
  b: string;
  kind: LinkKind;
  reason: string;
  label: string;
}

export interface Job {
  kind?: "llm" | "material" | "video" | "item";
  id: string;
  names: string[];
  status: "queued" | "running" | "done" | "failed";
  stage: string;
  error: string | null;
}

export interface ProcessingStep {
  phase?: "core" | "additional" | "on_demand";
  id: string; label: string; description: string; skippable: boolean; itemwise: boolean;
  status: "pending" | "running" | "done" | "skipped" | "failed";
  done: number; total: number | null; current: string | null; skipRequested: boolean;
}

export interface LlmRun {
  core?: { status: string; total: number; done: number; startedAt: string; finishedAt: string | null;
    targetSeconds: number; targetMet: boolean | null; elapsedSeconds?: number };
  id: string; status: Job["status"]; stage: string; error: string | null;
  results: Record<string, unknown>; issues: string[]; createdAt: string; finishedAt: string | null;
  steps: ProcessingStep[];
}

export interface LibraryData {
  items: Row[];
  cases: number;
  linked: number;
  map: { nodes: MapNode[]; edges: MapEdge[] };
  generation: number;
  busy: boolean;
  jobs: Job[];
  llmRun: LlmRun | null;
  checkSteps: ProcessingStep[];
}

export interface Brief {
  id: string;
  title: string;
  type: string;
  case: string;
  picture: boolean;
}

export interface MatchInfo extends Brief {
  kind: LinkKind;
  name: string;
  reason: string;
  strength: string;
  sameCase: boolean;
}

export interface ItemData extends Row {
  videoAnalysis?: (Job & { steps: ProcessingStep[]; results: Record<string, unknown>; issues: string[] }) | null;
  busy: boolean;
  groups: { kind: "campaign" | "kit"; id: string; name: string; reason: string; title: string }[];
  warningSigns: string[];
  unchecked: string[];
  path: PathData | null;
  matches: MatchInfo[];
  sameCase: Brief[];
  actions: { label: string; url: string; help?: string }[];
  findings: { kind: string; summary: string; source: string }[];
}

export interface PathData {
  ad: string | null;
  steps: Brief[];
  reasons: string[];
}

export interface SignsCell {
  count: number;
  text: string;
}

export interface CampaignRow {
  id: string;
  name: string;
  title: string;
  sub: string;
  picture: Brief;
  linkedBy: string;
  strength: string;
  cases: string;
  signs: SignsCell;
  kit: string;
  items: number;
}

export interface KitRow {
  id: string;
  name: string;
  title: string;
  sub: string;
  picture: Brief;
  linkedBy: string;
  cases: string;
  signs: SignsCell;
}

export interface CampaignsData {
  count: string;
  campaigns: CampaignRow[];
  kits: KitRow[];
  unlinked: string;
  repeats: string;
  generation: number;
  busy: boolean;
}

export interface PairInfo {
  a: Brief;
  b: Brief;
  kind: LinkKind;
  name: string;
  reason: string;
  strength: string;
}

export interface GroupDetail {
  kind: "campaign" | "kit";
  id: string;
  name: string;
  title: string;
  short: string;
  summary: string;
  pictures: Brief[];
  facts: { label: string; value: string }[];
  why: { text: string; label: string; strength: string; note: string };
  signs: { text: string; where: string }[];
  empty: string;
  actions: { label: string; url: string; help?: string }[];
  path: PathData | null;
  members: (Brief & { host: string })[];
  pairs: PairInfo[];
  kit: { id: string; name: string; why: string; text: string; others: Brief[] } | null;
  campaigns: CampaignRow[];
  possible: PairInfo[];
  evidence: { item: string; kind: string; summary: string; source: string }[];
}

export interface Meta {
  groups: Record<"case" | "campaign" | "kit" | "duplicate" | "possible", string>;
  links: Record<LinkKind, string>;
}

export interface EvidenceRow {
  ref: string;
  viewId: string;
  namespace: string;
  value: unknown;
  state: string;
  extractor: string;
  version: string;
  source: string;
  pivot: boolean;
}

export interface SourceData {
  record: EvidenceRow;
  before: string;
  highlight: string;
  after: string;
  resolution: string;
  message: string;
  truncated: boolean;
  download: string | null;
  image: string | null;
  videoTime: number | null;
}

export interface LookupData {
  record: EvidenceRow;
  hits: { id: string; title: string; case: string; namespaces: string[]; exact: boolean; origin: boolean }[];
}

export interface ComparisonData {
  markdown: string;
  sources: Record<string, EvidenceRow>;
  sharedRefs: string[];
}

export interface MediaData {
  available: boolean;
  error: string | null;
  compatible: boolean;
  prepared: boolean;
  duration: number | null;
  download: string | null;
  analysis: "not_run" | "available" | "unavailable";
  analysisReason: string;
  transcript: string;
  onScreen: string[];
  scenes: { seconds: number; text: string }[];
  frames: { seconds: number; sampled: boolean }[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function get<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(body.detail ?? res.statusText, res.status);
  }
  return res.json();
}

export const api = {
  meta: () => get<Meta>("/api/meta"),
  library: () => get<LibraryData>("/api/library"),
  runLlmChecks: (skipped: string[]) => get<Job>("/api/library/llm-checks", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(skipped),
  }),
  skipLlmStep: (job: string, step: string) => get<LlmRun>(
    `/api/library/llm-checks/${encodeURIComponent(job)}/skip/${encodeURIComponent(step)}`, { method: "POST" }),
  campaigns: () => get<CampaignsData>("/api/campaigns"),
  campaign: (id: string) => get<GroupDetail>(`/api/campaigns/${encodeURIComponent(id)}`),
  kit: (id: string) => get<GroupDetail>(`/api/kits/${encodeURIComponent(id)}`),
  item: (id: string) => get<ItemData>(`/api/items/${encodeURIComponent(id)}`),
  runItemChecks: (id: string, checks?: string[]) => get<Job>(`${itemApi(id)}/checks`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(checks ?? null),
  }),
  analyzeVideo: (id: string) => get<Job>(`${itemApi(id)}/video-analysis`, { method: "POST" }),
  cancelVideoAnalysis: (id: string) => get<Job>(`${itemApi(id)}/video-analysis/cancel`, { method: "POST" }),
  compare: (a: string, b: string) =>
    get<ComparisonData>(`/api/compare/${encodeURIComponent(a)}/${encodeURIComponent(b)}?format=view`),
  evidence: (id: string) => get<{ rows: EvidenceRow[] }>(`${itemApi(id)}/evidence`),
  source: (row: EvidenceRow) => get<SourceData>(`${itemApi(row.viewId)}/evidence/${row.ref}`),
  lookup: (row: EvidenceRow, allNamespaces: boolean) => get<LookupData>("/api/lookup", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ viewId: row.viewId, ref: row.ref, allNamespaces }),
  }),
  media: (id: string) => get<MediaData>(`${itemApi(id)}/media`),
  prepareVideo: (id: string) => get<{ prepared: boolean }>(`${itemApi(id)}/media/prepare`, { method: "POST" }),
};

export const itemApi = (id: string) => `/api/items/${encodeURIComponent(id)}`;

export const pictureUrl = (id: string, size: "card" | "full" = "card") =>
  `/api/pictures/${encodeURIComponent(id)}?size=${size}`;

export const compareUrl = (a: string, b: string, format: "md" | "json") =>
  `/api/compare/${encodeURIComponent(a)}/${encodeURIComponent(b)}?format=${format}`;

/** Upload files (with their folder paths) and links; reports progress from 0 to 1. */
export function addMaterial(
  files: { file: File; path: string }[],
  links: string[],
  onProgress: (fraction: number) => void,
): Promise<Job> {
  const form = new FormData();
  for (const { file, path } of files) form.append("files", file, path);
  for (const link of links) form.append("links", link);
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/material");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      const body = JSON.parse(xhr.responseText || "{}");
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new ApiError(body.detail ?? "Upload failed", xhr.status));
    };
    xhr.onerror = () => reject(new ApiError("The upload didn't reach the server.", 0));
    xhr.send(form);
  });
}
