import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { addMaterial } from "../api";
import { plural } from "../components/bits";
import { ProbeLinkPanel, ProbeRun, ProbeTools, useProbe } from "../components/ProbeAgent";
import { useVictim, VictimLinkPanel, VictimRunView } from "../components/VictimAgent";

type Staged = { file: File; path: string };

const AD_LIBRARY = /facebook\.com\/ads\/library\/?\?(?:.*&)?id=\d+/;
const DOMAIN = /^[a-z0-9-]+(\.[a-z0-9-]+)+$/i;

/** What a pasted value is; the server checks again. */
function linkKind(text: string): string | null {
  const t = text.trim();
  if (!t) return null;
  if (AD_LIBRARY.test(t)) return "Meta ad · captured from the Ad Library";
  try {
    if (/^https?:\/\//.test(t) && new URL(t).hostname) return "Webpage · captured live";
  } catch {
    /* not a URL */
  }
  if (DOMAIN.test(t)) return "Domain · captured live";
  return null;
}

/** Every file under a dropped entry, with its path inside the drop (folders keep their structure). */
async function walk(entry: FileSystemEntry): Promise<Staged[]> {
  if (entry.isFile) {
    const file = await new Promise<File>((ok, fail) => (entry as FileSystemFileEntry).file(ok, fail));
    return [{ file, path: entry.fullPath.replace(/^\//, "") }];
  }
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  const children: FileSystemEntry[] = [];
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((ok, fail) => reader.readEntries(ok, fail));
    if (!batch.length) break;
    children.push(...batch);
  }
  return (await Promise.all(children.map(walk))).flat();
}

const SKIP = /(^|\/)(\.DS_Store|Thumbs\.db)$/;

function adLibrarySearch(terms: string) {
  const base = "https://www.facebook.com/ads/library/?active_status=all&ad_type=all";
  return /^\d+$/.test(terms)
    ? `${base}&id=${terms}`
    : `${base}&country=PL&media_type=all&search_type=keyword_unordered&q=${encodeURIComponent(terms)}`;
}

export default function AddMaterial() {
  const navigate = useNavigate();
  const [link, setLink] = useState("");
  const [links, setLinks] = useState<string[]>([]);
  const [files, setFiles] = useState<Staged[]>([]);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [terms, setTerms] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const kind = linkKind(link);
  const agent = useProbe();
  const victimAgent = useVictim();
  // The agent walks websites, so a Meta Ad Library link is not a target for it.
  const probeTargets = [...new Set([...(kind ? [link.trim()] : []), ...links])].filter((l) => !AD_LIBRARY.test(l));

  const addFiles = (more: Staged[]) => {
    setError(null);
    setFiles((now) => {
      const seen = new Set(now.map((f) => f.path));
      return [...now, ...more.filter((f) => !SKIP.test(f.path) && !seen.has(f.path))];
    });
  };
  const addLink = () => {
    if (!kind) {
      setError("Paste a full link or a domain first.");
      return;
    }
    setLinks((now) => (now.includes(link.trim()) ? now : [...now, link.trim()]));
    setLink("");
    setError(null);
  };
  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);
    const entries = [...e.dataTransfer.items].map((i) => i.webkitGetAsEntry()).filter(Boolean) as FileSystemEntry[];
    addFiles((await Promise.all(entries.map(walk))).flat());
  };
  const analyse = async () => {
    const pending = kind && !links.includes(link.trim()) ? [...links, link.trim()] : links;
    if (!pending.length && !files.length) {
      setError("Add a link or choose files first.");
      return;
    }
    setError(null);
    setProgress(0);
    try {
      await addMaterial(files, pending, setProgress);
      navigate("/library");
    } catch (e) {
      setError((e as Error).message);
      setProgress(null);
    }
  };

  // Group staged files by their top folder, so a dropped folder reads as one line.
  const groups = new Map<string, Staged[]>();
  for (const f of files) {
    const top = f.path.includes("/") ? f.path.split("/")[0] + "/" : f.path;
    groups.set(top, [...(groups.get(top) ?? []), f]);
  }
  const count = links.length + groups.size + (kind && !links.includes(link.trim()) ? 1 : 0);

  return (
    <div className="add">
      <h1>What did you find?</h1>
      <div className="card">
        <form
          style={{ display: "flex", gap: 8 }}
          onSubmit={(e) => {
            e.preventDefault();
            addLink();
          }}
        >
          <input
            className="input"
            value={link}
            onChange={(e) => setLink(e.target.value)}
            placeholder="Paste a link to a webpage or a Meta Ad Library ad"
            aria-label="Link"
          />
          <button className="btn" type="submit">
            Add
          </button>
        </form>
        <div className="small" style={{ minHeight: 20, marginTop: 6 }}>
          {link.trim() ? kind ?? "Not a link or a domain" : "A link is opened and captured in a browser, as a visitor would see it."}
        </div>

        <div
          className={`drop ${over ? "over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
        >
          <div>Drop files or a whole folder here</div>
          <div className="small" style={{ margin: "4px 0 12px" }}>
            Saved pages (.html with their _files folder), web archives (.har), screenshots, screen recordings.
            A folder of folders becomes one case per folder.
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
            <button className="btn" onClick={() => fileInput.current?.click()}>
              Choose files
            </button>
            <button className="btn" onClick={() => folderInput.current?.click()}>
              Choose folder
            </button>
          </div>
          <input
            ref={fileInput}
            type="file"
            multiple
            hidden
            onChange={(e) => {
              addFiles([...(e.target.files ?? [])].map((file) => ({ file, path: file.name })));
              e.target.value = "";
            }}
          />
          <input
            ref={folderInput}
            type="file"
            hidden
            {...{ webkitdirectory: "", directory: "" }}
            onChange={(e) => {
              addFiles([...(e.target.files ?? [])].map((file) => ({ file, path: file.webkitRelativePath || file.name })));
              e.target.value = "";
            }}
          />
        </div>
      </div>

      {(links.length > 0 || files.length > 0) && (
        <div className="staged">
          {links.map((l) => (
            <div className="row" key={l}>
              <span>🔗 {l}</span>
              <button className="btn ghost" aria-label="Remove" onClick={() => setLinks((n) => n.filter((x) => x !== l))}>
                ✕
              </button>
            </div>
          ))}
          {[...groups.entries()].map(([top, list]) => (
            <div className="row" key={top}>
              <span>{top.endsWith("/") ? `📁 ${top.slice(0, -1)}` : `📄 ${top}`}</span>
              {top.endsWith("/") && <span className="small">{plural(list.length, "file")}</span>}
              <button
                className="btn ghost"
                aria-label="Remove"
                onClick={() => setFiles((n) => n.filter((f) => !list.includes(f)))}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
      {error && <div className="error">{error}</div>}
      {progress !== null && (
        <div className="progress" aria-label="Uploading">
          <div style={{ width: `${Math.round(progress * 100)}%` }} />
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 14 }}>
        <button className="btn primary" onClick={analyse} disabled={progress !== null}>
          {progress !== null ? "Uploading…" : count > 1 ? `Analyse ${count} items together` : "Analyse"}
        </button>
      </div>
      <ProbeLinkPanel agent={agent} targets={probeTargets} />
      <ProbeRun agent={agent} />
      <VictimLinkPanel agent={victimAgent} targets={probeTargets} />
      <VictimRunView agent={victimAgent} />

      <h2>Or find material with a tool</h2>
      <div className="card">
        <b>Meta Ad Library</b>
        <div className="small" style={{ margin: "2px 0 10px" }}>
          Search ads by keyword, advertiser or ad ID, then paste a found ad's link above.
        </div>
        <form
          style={{ display: "flex", gap: 8 }}
          onSubmit={(e) => {
            e.preventDefault();
            if (terms.trim()) window.open(adLibrarySearch(terms.trim()), "_blank", "noopener");
          }}
        >
          <input
            className="input"
            value={terms}
            onChange={(e) => setTerms(e.target.value)}
            placeholder="inwestycja, an advertiser's name or an ad ID"
            aria-label="Search the Ad Library"
          />
          <button className="btn" type="submit" disabled={!terms.trim()}>
            Search ↗
          </button>
        </form>
      </div>
      <ProbeTools agent={agent} />
    </div>
  );
}
