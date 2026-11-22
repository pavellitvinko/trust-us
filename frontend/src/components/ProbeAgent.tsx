import { useCallback, useEffect, useState } from "react";
import { probe, type InboxReport, type ProbeData, type ProbeJob } from "../api";

const DEMO_TARGET = "Fake test site";
const LEVEL_READ = 2; // reads pages and checkouts, submits nothing
const LEVEL_REGISTER = 4; // also registers when a form needs only email and password
const isActive = (job: ProbeJob) => job.state === "queued" || job.state === "running";

/** The agent's state, shared by the link panel, its result and the tools card. */
export function useProbe() {
  const [data, setData] = useState<ProbeData | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    probe
      .overview()
      .then(setData)
      .catch((e) => setError((e as Error).message));
  }, []);
  const job = data?.jobs.find((j) => j.id === selected);
  const running = job ? isActive(job) : false;
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, running ? 2000 : 6000);
    return () => clearInterval(timer);
  }, [refresh, running]);

  const start = async (request: { url: string; level: number; live: boolean; fake: boolean }) => {
    setStarting(true);
    setError(null);
    try {
      const { id } = await probe.start(request);
      setSelected(id);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  };
  return { data, job, selected, setSelected, busy: starting || running, error, setError, start, refresh };
}

type Agent = ReturnType<typeof useProbe>;

/** Shown once a link is entered. */
export function ProbeLinkPanel({ agent, targets }: { agent: Agent; targets: string[] }) {
  const [chosen, setChosen] = useState("");
  const [register, setRegister] = useState(false);
  if (!targets.length) return null;
  const target = targets.includes(chosen) ? chosen : targets[0];
  const alive = agent.data?.workerAlive;
  return (
    <div className="card probe-panel">
      <div className="probe-demo">
        <div>
          <b>Probe agent</b>
          <div className="small">Goes through the site like a visitor, in an isolated browser.</div>
        </div>
        <button
          className="btn"
          disabled={!alive || agent.busy}
          onClick={() =>
            agent.start({ url: target, level: register ? LEVEL_REGISTER : LEVEL_READ, live: register, fake: false })
          }
        >
          {agent.busy ? "Running…" : "Run probe agent"}
        </button>
      </div>
      {targets.length > 1 && (
        <select className="input" aria-label="Link for the agent" value={target} onChange={(e) => setChosen(e.target.value)}>
          {targets.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      )}
      <label className="probe-check">
        <input type="checkbox" checked={register} onChange={(e) => setRegister(e.target.checked)} />
        <span className="small">
          Also register with a bait email, if the form needs only email and password. I confirm this site is approved for it.
        </span>
      </label>
      {agent.data && !alive && (
        <div className="probe-warn">
          <b>The isolated worker is not running.</b> Start it, and this will update:
          <pre>docker compose --profile probe up -d --build probe-worker</pre>
        </div>
      )}
    </div>
  );
}

/** The selected run: milestones, evidence and, for real sites, adding it to the analysis. */
export function ProbeRun({ agent }: { agent: Agent }) {
  const job = agent.job;
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!job) return agent.error ? <div className="error">{agent.error}</div> : null;
  const add = async () => {
    setAdding(true);
    setError(null);
    try {
      await probe.add(job.id);
      agent.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAdding(false);
    }
  };
  // A read-only run never tries to register, so it doesn't list those steps as missed.
  const account: [string, boolean][] =
    job.level >= 3
      ? [
          ["Created an account", job.registered],
          ["Confirmed the email", job.emailConfirmed],
          ["Logged in", job.loggedIn],
        ]
      : [];
  const milestones: [string, boolean][] = [
    ["Opened the site", job.pages > 0],
    ...account,
    ["Found the payment page, left it empty", job.checkoutSeen],
  ];
  const current = isActive(job) ? milestones.findIndex(([, done]) => !done) : -1;
  return (
    <div className="card probe-result">
      <b>{job.target === DEMO_TARGET ? "Demo: fake investment scam" : job.target}</b>
      <ol className="probe-steps">
        {milestones.map(([label, done], i) => (
          <li key={label} className={done ? "done" : i === current ? "now" : ""}>
            <span className="mark">{done ? "✓" : i === current ? <span className="spinner" /> : i + 1}</span>
            {label}
          </li>
        ))}
      </ol>
      {job.state === "failed" && <div className="error">Failed: {job.error ?? "unknown error"}</div>}
      {job.findings.length > 0 && (
        <div>
          <b>Evidence found ({job.findings.length})</b>
          <table className="findings">
            <tbody>
              {job.findings.map((f, i) => (
                <tr key={i}>
                  <td>{f.found}</td>
                  <td>
                    <code>{f.value}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {job.steps.length > 0 && (
        <details>
          <summary className="small">What the agent did ({job.steps.length} steps)</summary>
          <ol className="small">
            {job.steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
        </details>
      )}
      {job.state === "done" &&
        job.target !== DEMO_TARGET &&
        (job.added ? (
          <div className="small">Already added to the analysis.</div>
        ) : (
          <button className="btn" onClick={add} disabled={adding}>
            {adding ? "Linking the findings…" : "Add to analysis"}
          </button>
        ))}
      {(error || agent.error) && <div className="error">{error ?? agent.error}</div>}
    </div>
  );
}

/** In "find material with a tool": the demo, past runs and the bait inbox. */
export function ProbeTools({ agent }: { agent: Agent }) {
  const [inbox, setInbox] = useState<InboxReport | null>(null);
  const [reading, setReading] = useState(false);
  const jobs = agent.data?.jobs ?? [];
  const readInbox = async () => {
    setReading(true);
    agent.setError(null);
    try {
      setInbox(await probe.inbox());
    } catch (e) {
      agent.setError((e as Error).message);
    } finally {
      setReading(false);
    }
  };
  return (
    <div className="card probe-tools">
      <div className="probe-demo">
        <div>
          <b>Probe agent demo</b>
          <div className="small">Runs on a built-in fake scam site. No real site or inbox is touched.</div>
        </div>
        <div className="probe-actions">
          {agent.data?.inboxEnabled && (
            <button className="btn" onClick={readInbox} disabled={reading}>
              {reading ? "Reading…" : "Check bait inbox"}
            </button>
          )}
          <button
            className="btn"
            disabled={!agent.data?.workerAlive || agent.busy}
            onClick={() => agent.start({ url: "", level: LEVEL_REGISTER, live: false, fake: true })}
          >
            {agent.busy ? "Running…" : "Run demo"}
          </button>
        </div>
      </div>
      {inbox && (
        <div className="small">
          Messages: {inbox.messages}. Matched to a site: {inbox.attributed}. New: {inbox.new}. Proven resale: {inbox.resold.length}.
          {inbox.resold.map((r, i) => (
            <div key={i}>
              Given to {r.site}, mail from {r.sender}: {r.subject}
            </div>
          ))}
        </div>
      )}
      {jobs.length > 0 && (
        <select
          className="input"
          aria-label="Past agent runs"
          value={agent.selected ?? ""}
          onChange={(e) => agent.setSelected(e.target.value || null)}
        >
          <option value="">Open a past agent run…</option>
          {jobs.map((j) => (
            <option key={j.id} value={j.id}>
              {j.created.slice(0, 19).replace("T", " ")} · {j.target.slice(0, 50)} · {j.state}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
