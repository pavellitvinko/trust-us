import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type Row } from "../api";
import { Chip, Help, Legend, Picture, plural } from "../components/bits";
import MapView from "../components/MapView";
import { campaignUrl, usePolled } from "../hooks";

/** The Library, refreshed every 1.5 s while an analysis runs. */
export const useLibrary = () => usePolled(api.library);

type SortKey = "title" | "case" | "campaign" | "kit" | "signs" | "links";

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
    const value = (r: Row) => (sort.key === "signs" || sort.key === "links" ? r[sort.key] : r[sort.key] || "￿");
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
    <table className="items">
      <thead>
        <tr>
          <th style={{ width: 112 }} />
          {header("title", "Item")}
          {header("case", "Case", "case")}
          {header("campaign", "Campaign", "campaign")}
          {header("kit", "Kit", "kit")}
          {header("signs", "Warning signs")}
          {header("links", "Linked items")}
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => (
          <tr key={r.id} onClick={() => navigate(`/item/${encodeURIComponent(r.id)}`)}>
            <td>
              <Picture id={r.id} has={r.picture} type={r.type} width={96} height={60} />
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
                <Link to={campaignUrl(r.kitId)} onClick={(e) => e.stopPropagation()} title="See this kit">
                  <Chip kind="kit">{r.kit}</Chip>
                </Link>
              )}
            </td>
            <td>{signsText(r)}</td>
            <td>{linksText(r)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Library() {
  const { data, error } = useLibrary();
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "map" ? "map" : "table";
  if (error) return <p className="error">Couldn't load the Library: {error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const empty = !data.items.length && !data.jobs.length;
  return (
    <>
      <div className="head">
        <h1>Library</h1>
        {data.items.length > 0 && (
          <span className="sub">
            {plural(data.items.length, "item")} in {plural(data.cases, "case")} · {data.linked} linked to something
          </span>
        )}
        {!empty && (
          <div className="seg" role="tablist">
            {(["table", "map"] as const).map((v) => (
              <button
                key={v}
                className={view === v ? "on" : ""}
                role="tab"
                aria-selected={view === v}
                onClick={() => setParams(v === "map" ? { view: "map" } : {}, { replace: true })}
              >
                {v === "table" ? "Table" : "Map"}
              </button>
            ))}
          </div>
        )}
      </div>
      {data.jobs.length > 0 && (
        <div className="jobs">
          {data.jobs.map((j) => (
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
          <Legend />
          {view === "table" ? (
            <ItemsTable rows={data.items} />
          ) : (
            <MapView map={data.map} unlinked={data.items.length - data.map.nodes.length} />
          )}
        </>
      )}
    </>
  );
}
