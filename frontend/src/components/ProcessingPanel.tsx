import { useEffect, useState } from "react";
import { api, type CheckState, type LibraryData, type ProcessingStep } from "../api";
import ChecksDropdown from "./ChecksDropdown";
import ProgressRing from "./ProgressRing";

function duration(start: string, end: string | null, now: number) {
  const seconds = Math.max(0, Math.floor(((end ? Date.parse(end) : now) - Date.parse(start)) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export default function ProcessingPanel({ data, refresh }: { data: LibraryData; refresh: () => void }) {
  const [configuring, setConfiguring] = useState(false);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const run = data.llmRun;
  const active = run?.status === "running" || run?.status === "queued";
  const setup = !run || (configuring && !active);
  const steps = setup ? data.checkSteps : run.steps ?? [];
  const settled = steps.filter((step) => ["done", "skipped", "failed"].includes(step.status)).length;
  const working = steps.find((step) => step.status === "running");
  const fraction = working?.total ? working.done / working.total : 0;
  const progress = setup ? 0 : (settled + fraction) / Math.max(1, steps.length);
  const state: CheckState = setup ? "not_run" : active ? "running" : run.status === "failed" ? "failed"
    : run.issues.length ? "partial" : "complete";
  const status = setup ? `${data.items.length} items · Ready to run` : active ? run.stage
    : run.status === "failed" ? "Processing stopped" : run.issues.length ? "Finished with coverage gaps" : "Complete";
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);

  async function start() {
    setPending("start"); setError(null);
    try { await api.runLlmChecks(skipped); setConfiguring(false); refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setPending(null); }
  }
  async function skip(step: ProcessingStep) {
    if (!run) return;
    setPending(step.id); setError(null);
    try { await api.skipLlmStep(run.id, step.id); refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setPending(null); }
  }

  return <ChecksDropdown className="library-processing-compact" label={`Library processing: ${status}`}
    trigger={<>
      <ProgressRing value={progress} state={state} label={`Library processing: ${Math.round(progress * 100)}% of steps resolved. ${status}`} size={32} />
      <span className="checks-trigger-copy"><strong>Library processing</strong><span>{status}</span></span>
      {!setup && <span className="processing-elapsed">{duration(run.createdAt, run.finishedAt, now)}</span>}
    </>}>
    <div className="checks-menu-head"><strong>{setup ? "Choose checks" : "Processing steps"}</strong><span>{setup ? `${steps.filter((s) => !skipped.includes(s.id)).length} selected` : `${settled}/${steps.length} resolved`}</span></div>
    <ul className="checks-menu-list">
      {steps.map((step) => {
        const status = setup ? skipped.includes(step.id) ? "skipped" : "pending" : step.status;
        const canSkip = active && step.skippable && !step.skipRequested &&
          (status === "pending" || (status === "running" && step.itemwise));
        const outcome = run?.results[step.id] as Record<string, number> | undefined;
        const incomplete = outcome?.not_observable || outcome?.kept || outcome?.reason;
        const ringState: CheckState = status === "done" ? incomplete ? "partial" : "complete"
          : status === "pending" ? "queued" : status;
        const value = status === "done" ? 1 : status === "running" ? step.total ? step.done / step.total : undefined : 0;
        const label = setup ? status === "skipped" ? "Excluded" : "Ready"
          : step.skipRequested && status === "running" ? "Stopping after current item"
          : status === "done" ? incomplete ? "Finished with gaps" : "Complete"
          : status === "pending" ? run?.status === "failed" ? "Not run" : "Waiting"
          : status === "running" ? step.total !== null ? `${step.done}/${step.total} processed` : "In progress"
          : status === "skipped" ? "Skipped" : "Failed";
        return <li key={step.id}>
          <ProgressRing value={value} state={ringState} label={`${step.label}: ${label}`} size={25}
            text={status === "pending" ? "" : undefined} />
          <div className="checks-menu-copy"><strong>{step.label}</strong><span className={`check-${ringState}`}>{label}</span></div>
          {setup && step.skippable ? <input type="checkbox" checked={!skipped.includes(step.id)} aria-label={`Include ${step.label}`}
            onChange={(e) => setSkipped((s) => e.target.checked ? s.filter((id) => id !== step.id) : [...s, step.id])} />
            : canSkip ? <button className="text-link small" disabled={!!pending} onClick={() => skip(step)}>{pending === step.id ? "Skipping…" : "Skip"}</button>
            : !step.skippable ? <span className="checks-required">Required</span> : null}
        </li>;
      })}
    </ul>
    <div className="checks-menu-footer">
      {setup ? <button className="btn primary" disabled={!!pending || data.busy} onClick={start}>{pending === "start" ? "Starting…" : "Run selected checks"}</button>
        : !active && <button className="btn" onClick={() => { setConfiguring(true); setError(null); }}>Configure another run</button>}
      {setup && run && <button className="text-link small" onClick={() => setConfiguring(false)}>View last run</button>}
    </div>
    {(error || (!setup && run.error)) && <p className="error checks-menu-note" role="alert">{error || run?.error}</p>}
    {!setup && run.issues.length > 0 && <details className="checks-menu-notes"><summary>{run.issues.length} coverage notes</summary><ul>{run.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul></details>}
    <p className="checks-menu-note">{setup ? "Uses OpenAI and Gemini with source material; API charges may apply. Cached answers are reused."
      : "Progress measures resolved steps, not time remaining. Reading steps stop after the current item; batch steps can be skipped before starting."}</p>
  </ChecksDropdown>;
}
