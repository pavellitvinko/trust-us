import { useState } from "react";
import { api, type ItemProcessing } from "../api";
import ChecksDropdown from "./ChecksDropdown";
import ProgressRing from "./ProgressRing";

function CheckMenu({ processing, id, refresh }: { processing: ItemProcessing; id?: string; refresh?: () => void }) {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const busy = processing.checks.some((c) => c.state === "queued" || c.state === "running");
  async function run(check?: string) {
    if (!id) return;
    setPending(check ?? "all"); setError(null);
    try { await api.runItemChecks(id, check ? [check] : undefined); refresh?.(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setPending(null); }
  }
  return <>
    <div className="checks-menu-head"><strong>Item checks</strong><span>{processing.complete}/{processing.total} complete</span></div>
    <ul className="checks-menu-list">
      {processing.checks.map((check) => <li key={check.id} title={check.updatedAt ? `Latest attempt: ${new Date(check.updatedAt).toLocaleString()}` : undefined}>
        <ProgressRing state={check.state} value={check.state === "running" ? undefined : check.state === "complete" ? 1 : 0}
          label={`${check.label}: ${check.status}`} size={25} text={check.state === "not_run" || check.state === "queued" ? "" : undefined} />
        <div className="checks-menu-copy"><strong>{check.label}</strong><span className={`check-${check.state}`}>{check.status}</span>
          {["partial", "unavailable", "retained", "failed", "deferred", "on_demand"].includes(check.state) && <small>{check.message}</small>}
          {check.hasResult && check.state !== "complete" && <small>{check.state === "partial" ? "Stored results available" : "Earlier results available"}</small>}
        </div>
        {id && processing.canRun !== false && <button className="text-link small" disabled={!!pending || busy}
          onClick={() => run(check.id)} aria-label={`Run ${check.label}`}>{pending === check.id ? "Starting…" : check.hasResult ? "Run again" : "Run"}</button>}
      </li>)}
    </ul>
    {id && processing.canRun !== false && <div className="checks-menu-footer"><button className="btn primary" disabled={!!pending || busy}
      onClick={() => run()}>{pending === "all" ? "Starting…" : "Run all item checks"}</button></div>}
    {error && <p className="error checks-menu-note" role="alert">{error}</p>}
    <p className="checks-menu-note">{id ? "Runs checks for this item only. Required reading runs first. Cached answers are reused; API charges may apply. Pattern checks match the existing library." : "Progress shows check coverage. Skipped and unavailable checks remain incomplete."}</p>
  </>;
}

function Indicator({ processing, compact = false, id, refresh }: { processing: ItemProcessing; compact?: boolean; id?: string; refresh?: () => void }) {
  const label = `${processing.current || processing.status} · ${processing.complete} of ${processing.total} checks complete`;
  return <ChecksDropdown label={label} className={compact ? "item-checks-cell" : "item-checks-control"}
    trigger={<>
      <ProgressRing value={processing.complete / Math.max(1, processing.total)} state={processing.state}
        label={label} size={compact ? 28 : 32} />
      {!compact && <span className="checks-trigger-copy"><strong>Checks</strong><span>{processing.current || processing.status} · {processing.complete}/{processing.total}</span></span>}
    </>}>
    <CheckMenu processing={processing} id={id} refresh={refresh} />
  </ChecksDropdown>;
}

export function ItemChecksCell({ processing }: { id: string; processing: ItemProcessing }) {
  return <Indicator processing={processing} compact />;
}

export default function ItemChecks({ processing, id, refresh }: { processing: ItemProcessing; id: string; refresh: () => void }) {
  return <section id="checks" className="item-checks-compact" aria-label="Item processing and checks">
    <Indicator processing={processing} id={id} refresh={refresh} />
  </section>;
}
