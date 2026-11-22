// Types and calls for the Follow the Fake API (src/ftf/web/app.py).

export type LinkKind = "campaign" | "kit" | "duplicate" | "possible";

export interface Row {
  id: string;
  title: string;
  type: "Page" | "Image" | "Video";
  host: string;
  case: string;
  caseId: string;
  added: string;
  campaign: string;
  kit: string;
  duplicateOf: string;
  comparable: boolean;
  signs: number;
  checked: boolean;
  links: number;
  possible: number;
  picture: boolean;
  note: string;
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
  id: string;
  names: string[];
  status: "queued" | "running" | "done" | "failed";
  stage: string;
  error: string | null;
}

export interface LibraryData {
  items: Row[];
  cases: number;
  linked: number;
  map: { nodes: MapNode[]; edges: MapEdge[] };
  generation: number;
  busy: boolean;
  jobs: Job[];
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
  groups: { kind: "campaign" | "kit"; name: string; reason: string }[];
  warningSigns: string[];
  unchecked: string[];
  path: { ad: string | null; steps: Brief[]; reasons: string[] } | null;
  matches: MatchInfo[];
  sameCase: Brief[];
  actions: { label: string; url: string; help?: string }[];
  findings: { kind: string; summary: string; source: string }[];
}

export interface Meta {
  groups: Record<"case" | "campaign" | "kit" | "duplicate" | "possible", string>;
  links: Record<LinkKind, string>;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(body.detail ?? res.statusText, res.status);
  }
  return res.json();
}

export const api = {
  meta: () => get<Meta>("/api/meta"),
  library: () => get<LibraryData>("/api/library"),
  item: (id: string) => get<ItemData>(`/api/items/${encodeURIComponent(id)}`),
  compare: (a: string, b: string) =>
    get<{ markdown: string }>(`/api/compare/${encodeURIComponent(a)}/${encodeURIComponent(b)}?format=view`),
};

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
