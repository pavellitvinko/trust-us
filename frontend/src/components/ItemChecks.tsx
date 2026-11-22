import type { ItemProcessing } from "../api";
import ChecksDropdown from "./ChecksDropdown";
import ProgressRing from "./ProgressRing";

function CheckMenu({ processing }: { processing: ItemProcessing }) {
  return <>
    <div className="checks-menu-head"><strong>Item checks</strong><span>{processing.complete}/{processing.total} complete</span></div>
    <ul className="checks-menu-list">
      {processing.checks.map((check) => <li key={check.id} title={check.updatedAt ? `Latest attempt: ${new Date(check.updatedAt).toLocaleString()}` : undefined}>
        <ProgressRing state={check.state} value={check.state === "running" ? undefined : check.state === "complete" ? 1 : 0}
          label={`${check.label}: ${check.status}`} size={25} text={check.state === "not_run" || check.state === "queued" ? "" : undefined} />
        <div className="checks-menu-copy"><strong>{check.label}</strong><span className={`check-${check.state}`}>{check.status}</span>
          {["partial", "unavailable", "retained", "failed"].includes(check.state) && <small>{check.message}</small>}
          {check.hasResult && check.state !== "complete" && <small>{check.state === "partial" ? "Stored results available" : "Earlier results available"}</small>}
        </div>
      </li>)}
    </ul>
    <p className="checks-menu-note">Progress shows check coverage. Skipped and unavailable checks remain incomplete.</p>
  </>;
}

function Indicator({ processing, compact = false }: { processing: ItemProcessing; compact?: boolean }) {
  const label = `${processing.current || processing.status} · ${processing.complete} of ${processing.total} checks complete`;
  return <ChecksDropdown label={label} className={compact ? "item-checks-cell" : "item-checks-control"}
    trigger={<>
      <ProgressRing value={processing.complete / Math.max(1, processing.total)} state={processing.state}
        label={label} size={compact ? 28 : 32} />
      {!compact && <span className="checks-trigger-copy"><strong>Checks</strong><span>{processing.current || processing.status} · {processing.complete}/{processing.total}</span></span>}
    </>}>
    <CheckMenu processing={processing} />
  </ChecksDropdown>;
}

export function ItemChecksCell({ processing }: { id: string; processing: ItemProcessing }) {
  return <Indicator processing={processing} compact />;
}

export default function ItemChecks({ processing }: { processing: ItemProcessing }) {
  return <section id="checks" className="item-checks-compact" aria-label="Item processing and checks">
    <Indicator processing={processing} />
  </section>;
}
