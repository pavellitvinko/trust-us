import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, type EvidenceRow, type LookupData, type SourceData, type ItemData } from "../api";

export const evidenceValue = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value);

function Dialog({ title, close, children }: { title: string; close: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog className="inspector" ref={ref} onCancel={close} aria-label={title}>
      <div className="inspector-head"><h2>{title}</h2><button className="btn" onClick={close}>Close</button></div>
      {children}
    </dialog>
  );
}

function Source({ row, close }: { row: EvidenceRow; close: () => void }) {
  const [data, setData] = useState<SourceData | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    api.source(row).then((r) => live && setData(r)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [row]);
  return (
    <Dialog title="Source evidence" close={close}>
      <p><b>{row.namespace}</b> · {row.state} · {row.extractor}@{row.version}</p>
      <code className="source-label">{row.source || "No file or locator recorded"}</code>
      <pre className="observed-value">{evidenceValue(row.value)}</pre>
      {error && <p className="error">{error}</p>}
      {!data && !error && <p>Loading source…</p>}
      {data && <>
        <p className="small">{data.message}</p>
        {data.download && <a className="btn" href={data.download}>Download original artifact</a>}
        {data.image && <img className="source-image" src={data.image} alt="Original source artifact" />}
        {data.videoTime !== null && (
          <p><Link onClick={close} to={`/item/${encodeURIComponent(row.viewId)}?t=${data.videoTime}`}>Open video at {data.videoTime}s</Link></p>
        )}
        {(data.before || data.highlight || data.after) && (
          <pre className="report source-text">{data.before}<mark>{data.highlight}</mark>{data.after}</pre>
        )}
        {data.truncated && <p className="small">Showing an excerpt. Download the original artifact for the complete file.</p>}
      </>}
    </Dialog>
  );
}

function Pivot({ row, close }: { row: EvidenceRow; close: () => void }) {
  const [all, setAll] = useState(false);
  const [data, setData] = useState<LookupData | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setData(null); setError("");
    api.lookup(row, all).then((r) => live && setData(r)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [row, all]);
  return (
    <Dialog title="Items with this value" close={close}>
      <p><b>{row.namespace}</b></p><pre className="observed-value">{evidenceValue(row.value)}</pre>
      <label><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> All namespaces</label>
      {error && <p className="error">{error}</p>}
      {!data && !error && <p>Looking up stored evidence…</p>}
      {data && <>
        <p className="small">{data.hits.length} items · {data.hits.some((h) => !h.exact) ? "Case-insensitive matches" : "Exact matches"}</p>
        {!data.hits.length && <p>No items carry this value.</p>}
        <ul className="lookup-results">{data.hits.map((hit) => (
          <li key={hit.id}>
            <Link to={`/item/${encodeURIComponent(hit.id)}`} onClick={close}>{hit.title || hit.id}</Link>
            <div className="small">Case {hit.case}{hit.origin && " · this item"} · {hit.namespaces.join(", ")}</div>
          </li>
        ))}</ul>
      </>}
    </Dialog>
  );
}

export function SourceButton({ row, children }: { row: EvidenceRow; children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <>
    <button className="text-link" onClick={() => setOpen(true)} title="Inspect source evidence">{children ?? (row.source || "Inspect evidence")}</button>
    {open && <Source row={row} close={() => setOpen(false)} />}
  </>;
}

export function PivotButton({ row }: { row: EvidenceRow }) {
  const [open, setOpen] = useState(false);
  if (!row.pivot) return null;
  return <>
    <button className="text-link small" onClick={() => setOpen(true)}>Find items with this value</button>
    {open && <Pivot row={row} close={() => setOpen(false)} />}
  </>;
}

export function ItemEvidence({ item }: { item: ItemData }) {
  const [rows, setRows] = useState<EvidenceRow[] | null>(null);
  const [error, setError] = useState("");
  const load = () => {
    if (rows !== null) return;
    setError("");
    api.evidence(item.id).then((r) => setRows(r.rows)).catch((e) => setError(e.message));
  };
  return (
    <details className="more" onToggle={(e) => e.currentTarget.open && load()}>
      <summary>Evidence details{rows && ` (${rows.length} observations)`}</summary>
      {error && <p className="error">{error} <button className="text-link" onClick={load}>Retry</button></p>}
      {!rows && !error && <p className="small">Loading stored evidence…</p>}
      {rows && <>
        {item.findings.length > 0 && <table className="findings"><tbody>{item.findings.map((f, n) => {
          const row = rows.find((r) => r.source === f.source);
          return <tr key={n}><td>{f.kind}</td><td>{f.summary}</td><td>{row ? <SourceButton row={row} /> : f.source}</td></tr>;
        })}</tbody></table>}
        <p className="small">Stored observations, including missing and unavailable checks. Open a source to inspect its recorded location.</p>
        {!rows.length && <p>No stored observations.</p>}
        <div className="evidence-scroll"><table className="findings evidence-table">
          <thead><tr><th>Observation</th><th>Value</th><th>Source</th></tr></thead>
          <tbody>{rows.map((row) => <tr key={row.ref}>
            <td><b>{row.namespace}</b><div className="small">{row.state}<br />{row.extractor}@{row.version}</div></td>
            <td><div className="evidence-value">{evidenceValue(row.value)}</div><PivotButton row={row} /></td>
            <td><SourceButton row={row} /></td>
          </tr>)}</tbody>
        </table></div>
      </>}
    </details>
  );
}
