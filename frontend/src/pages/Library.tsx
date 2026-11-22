import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type Row } from "../api";
import { Chip, Help, Picture, plural } from "../components/bits";
import MapView from "../components/MapView";
import { VideoThumbnail } from "../components/video";
import ProcessingPanel from "../components/ProcessingPanel";
import { ItemChecksCell } from "../components/ItemChecks";
import { campaignUrl, kitUrl, usePolled } from "../hooks";

/** The Library, refreshed every 1.5 s while an analysis runs. */
export const useLibrary = () => usePolled(api.library);

type SortKey = "title" | "case" | "campaign" | "kit" | "signs" | "links" | "processing";

function signsText(r: Row) {
  if (r.signs) return <span className="danger">{plural(r.signs, "warning sign")}</span>;
  return <span className="muted">{r.checked ? "none found" : "not checked"}</span>;
}

function linksText(r: Row) {
  return (
    <>
      {r.links ? r.links : <span className="muted">{r.comparable ? "none yet" : "too little to compare"}</span>}
      {r.possible ? <span className="muted"> +{r.possible} possible</span> : null}
    </>
  );
}

function ItemsTable({ rows }: { rows: Row[] }) {
  const navigate = useNavigate();
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 } | null>(null);
  const sorted = useMemo(() => {
    if (!sort) return rows; // newest case first, journey steps in order
    const value = (r: Row) => sort.key === "processing" ? r.processing.status :
      (sort.key === "signs" || sort.key === "links" ? r[sort.key] : r[sort.key] || "￿");
    return [...rows].sort((a, b) => (value(a) > value(b) ? 1 : value(a) < value(b) ? -1 : 0) * sort.dir);
  }, [rows, sort]);
  const header = (key: SortKey, label: string, help?: "case" | "campaign" | "kit") => (
    <th
      className="sort"
      onClick={() => setSort((s) => ({ key, dir: s?.key === key && s.dir === 1 ? -1 : 1 }))}
      aria-sort={sort?.key === key ? (sort.dir === 1 ? "ascending" : "descending") : undefined}
    >
      {label}
      {help && <Help topic={help} />}
      {sort?.key === key ? (sort.dir === 1 ? " ↑" : " ↓") : ""}
    </th>
  );
  return (
    <div className="items-scroll" role="region" aria-label="Library items" tabIndex={0}>
    <table className="items">
      <colgroup>
        <col style={{ width: 110 }} /><col /><col style={{ width: 115 }} />
        <col style={{ width: 145 }} /><col style={{ width: 105 }} />
        <col style={{ width: 80 }} /><col style={{ width: 115 }} /><col style={{ width: 115 }} />
      </colgroup>
      <thead>
        <tr>
          <th style={{ width: 112 }} />
          {header("title", "Item")}
          {header("case", "Case", "case")}
          {header("campaign", "Campaign", "campaign")}
          {header("kit", "Kit", "kit")}
          {header("processing", "Checks")}
          {header("signs", "Warning signs")}
          {header("links", "Linked items")}
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => (
          <tr key={r.id} onClick={() => navigate(`/item/${encodeURIComponent(r.id)}`)}>
            <td>
              {r.type === "Video" ? <VideoThumbnail id={r.id} /> :
                <Picture id={r.id} has={r.picture} type={r.type} width={96} height={60} />}
            </td>
            <td>
              <Link className="title" to={`/item/${encodeURIComponent(r.id)}`} onClick={(e) => e.stopPropagation()}>
                {r.title}
              </Link>
              <div className="small">
                {r.type}
                {r.host && ` · ${r.host}`}
                {r.duplicateOf && <span style={{ color: "var(--dup)" }}> · same page also in {r.duplicateOf}</span>}
                {r.note && <span style={{ color: "var(--accent)" }}> · {r.note}</span>}
              </div>
            </td>
            <td>
              <Chip kind="case">{r.case}</Chip>
            </td>
            <td>
              {r.campaign && (
                <Link to={campaignUrl(r.campaignId)} onClick={(e) => e.stopPropagation()} title="See this campaign">
                  <Chip kind="campaign">{r.campaign}</Chip>
                </Link>
              )}
            </td>
            <td>
              {r.kit && (
                <Link to={kitUrl(r.kitId)} onClick={(e) => e.stopPropagation()} title="See this kit">
                  <Chip kind="kit">{r.kit}</Chip>
                </Link>
              )}
            </td>
            <td><ItemChecksCell id={r.id} processing={r.processing} /></td>
            <td>{signsText(r)}</td>
            <td>{linksText(r)}</td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

/** Downloads the Library report; a failure is said on the page, not left to the browser. */
function ReportButton({ busy }: { busy: boolean }) {
  const [state, setState] = useState<"idle" | "working">("idle");
  const [error, setError] = useState<string | null>(null);
  const download = async () => {
    setState("working");
    setError(null);
    try {
      const res = await fetch("/api/library/report.pdf");
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? res.statusText);
      }
      const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1];
      const url = URL.createObjectURL(await res.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = name ?? "library-report.pdf";
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(`Couldn't make the report: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setState("idle");
    }
  };
  return (
    <>
      <button className="btn primary report-btn" onClick={download} disabled={busy || state === "working"}
        title={busy ? "Available when the current analysis finishes" : "The state of the whole library, as it is now"}>
        {state === "working" ? <span className="spinner" /> : (
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M8 2v8m0 0L4.5 6.5M8 10l3.5-3.5M2.5 13.5h11" />
          </svg>
        )}
        {state === "working" ? "Preparing report…" : "Download report (PDF)"}
      </button>
      {error && <span className="error report-error" role="alert">{error}</span>}
    </>
  );
}

export default function Library() {
  const { data, error, refresh } = useLibrary();
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "map" ? "map" : "table";
  if (error) return <p className="error">Couldn't load the Library: {error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const empty = !data.items.length && !data.jobs.length;
  const materialJobs = data.jobs.filter((job) => job.kind !== "llm");
  return (
    <>
      <div className="head">
        <h1>Library</h1>
        {data.items.length > 0 && <span className="sub">
          {plural(data.items.length, "item")} in {plural(data.cases, "case")} · {data.linked} linked to something
        </span>}
        {data.items.length > 0 && <ReportButton busy={data.busy} />}
        {!empty && <div className="seg" role="tablist">
          {(["table", "map"] as const).map((v) => <button key={v} className={view === v ? "on" : ""}
            role="tab" aria-selected={view === v}
            onClick={() => setParams(v === "map" ? { view: "map" } : {}, { replace: true })}>
            {v === "table" ? "Table" : "Map"}
          </button>)}
        </div>}
      </div>
      {data.items.length > 0 && <ProcessingPanel data={data} refresh={refresh} />}
      {materialJobs.length > 0 && (
        <div className="jobs">
          {materialJobs.map((j) => (
            <div key={j.id} className={`job ${j.status === "failed" ? "failed" : ""}`}>
              {j.status === "failed" ? <span className="danger">Couldn't analyse</span> : <span className="spinner" />}
              <span style={{ flex: 1 }}>
                {j.names.slice(0, 3).join(", ")}
                {j.names.length > 3 && <span className="muted"> and {j.names.length - 3} more</span>}
              </span>
              <span className="small">{j.status === "failed" ? j.error : j.stage}</span>
            </div>
          ))}
        </div>
      )}
      {empty ? (
        <div className="empty card">
          <p>Nothing has been added yet. Add a link, a saved page, screenshots or a whole folder.</p>
          <Link className="btn primary" to="/add">
            Add material
          </Link>
        </div>
      ) : (
        <>
          {view === "table" ? (
            <ItemsTable rows={data.items} />
          ) : (
            <MapView rows={data.items} map={data.map} />
          )}
        </>
      )}
    </>
  );
}
