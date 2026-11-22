import type { CheckState } from "../api";

/** Shared coverage/progress indicator for a run, an item, or one check. */
export default function ProgressRing({ value, state, label, size = 30, text }: {
  value?: number; state: CheckState; label: string; size?: number; text?: string;
}) {
  const fraction = value === undefined ? undefined : Math.max(0, Math.min(1, value));
  const circumference = 2 * Math.PI * 14;
  const symbol = text ?? (state === "complete" ? "✓" : state === "failed" || state === "unavailable" ? "!"
    : state === "skipped" ? "−" : state === "retained" ? "↶"
    : fraction === undefined ? "" : `${Math.round(fraction * 100)}%`);
  return <span className={`progress-ring check-${state} ${fraction === undefined ? "ring-indeterminate" : ""}`}
    style={{ width: size, height: size }} role="progressbar" aria-label={label}
    aria-valuemin={0} aria-valuemax={100} aria-valuenow={fraction === undefined ? undefined : Math.round(fraction * 100)}
    aria-valuetext={label} title={label}>
    <svg viewBox="0 0 36 36" aria-hidden="true">
      <circle className="ring-track" cx="18" cy="18" r="14" />
      <circle className="ring-fill" cx="18" cy="18" r="14" strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - (fraction ?? 0.25))} />
      {state === "running" && fraction !== undefined && <circle className="ring-working" cx="18" cy="18" r="14" />}
    </svg>
    <span className="ring-text" aria-hidden="true">{symbol}</span>
  </span>;
}
