import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { victim, type VictimData, type VictimRun, type VictimStep } from "../api";

const RUNNING = new Set(["starting", "running", "paused", "waiting_for_person"]);
const PAGE_KIND: Record<string, string> = {
  advertorial: "Article",
  chat: "Chat",
  quiz: "Quiz",
  form: "Form",
  thank_you: "Thank-you page",
  decoy: "Decoy page",
  other: "Page",
};
const HOW: Record<string, string> = {
  start: "from the link",
  link_click: "link click",
  chat_hand_off: "from the chat",
  form_submit: "form sent",
  button_click: "button click",
  new_tab: "new tab",
};

/** The agent's runs, and the one on show. */
export function useVictim() {
  const [data, setData] = useState<VictimData | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [run, setRun] = useState<VictimRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const refresh = useCallback(() => {
    victim.overview().then(setData).catch((e) => setError((e as Error).message));
  }, []);
  const running = run ? RUNNING.has(run.status) : false;
  useEffect(() => {
    refresh();
  }, [refresh]);
  useEffect(() => {
    if (!selected) return setRun(null);
    let live = true;
    const load = () =>
      victim
        .run(selected)
        .then((r) => live && setRun(r))
        .catch((e) => live && setError((e as Error).message));
    load();
    const timer = running ? setInterval(load, 1500) : undefined;
    return () => {
      live = false;
      if (timer) clearInterval(timer);
    };
  }, [selected, running]);
  useEffect(() => {
    if (run && !running) refresh(); // a run that just ended appears in the list as finished
  }, [run?.status]); // eslint-disable-line react-hooks/exhaustive-deps
  const start = async (request: { url: string; persona: string; approved: boolean; quick: boolean }) => {
    setStarting(true);
    setError(null);
    try {
      const { id } = await victim.start(request);
      if (id) setSelected(id);
      else setError("The run didn't start. Last lines of its output are below.");
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  };
  return { data, run, selected, setSelected, running, starting, error, start, refresh };
}

type Agent = ReturnType<typeof useVictim>;

/** Shown once a link is entered, next to the probe agent. */
export function VictimLinkPanel({ agent, targets }: { agent: Agent; targets: string[] }) {
  const [chosen, setChosen] = useState("");
  const [persona, setPersona] = useState("");
  const [approved, setApproved] = useState(false);
  const [quick, setQuick] = useState(true);
  if (!targets.length || !agent.data) return null;
  const target = targets.includes(chosen) ? chosen : targets[0];
  const busy = agent.starting || agent.running;
  return (
    <div className="card probe-panel victim-panel">
      <div className="probe-demo">
        <div>
          <b>Victim visitor</b>
          <div className="small">
            Goes through the site as one made-up person would: reads, answers the chat, and fills in the lead form with
            fake details. It never pays, uploads or installs anything. A browser window opens on this computer and the
            run is recorded as a video.
          </div>
        </div>
        <button
          className="btn"
          disabled={busy || !agent.data.canStart || !approved}
          onClick={() => agent.start({ url: target, persona, approved, quick })}
        >
          {agent.starting ? "Starting…" : agent.running ? "Running…" : "Run victim visitor"}
        </button>
      </div>
      <div className="victim-options">
        {targets.length > 1 && (
          <select className="input" aria-label="Link for the victim visitor" value={target}
            onChange={(e) => setChosen(e.target.value)}>
            {targets.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        )}
        <select className="input" aria-label="Persona" value={persona} onChange={(e) => setPersona(e.target.value)}>
          <option value="">Persona: suggested from the link</option>
          {agent.data.personas.map((p) => (
            <option key={p.id} value={p.id}>{p.id}: {p.summary}</option>
          ))}
        </select>
      </div>
      <label className="probe-check">
        <input type="checkbox" checked={approved} onChange={(e) => setApproved(e.target.checked)} />
        <span className="small">
          I confirm this site is approved for a test: the agent sends its lead form with a made-up name, a throwaway
          e-mail inbox and, only if the form needs one, a number that can't belong to anyone.
        </span>
      </label>
      <label className="probe-check">
        <input type="checkbox" checked={quick} onChange={(e) => setQuick(e.target.checked)} />
        <span className="small">Short reading pauses, so the video is easy to watch (sites that time visitors may notice)</span>
      </label>
      {!agent.data.canStart && (
        <div className="probe-warn">
          <b>The app runs in Docker, where no browser window can open.</b> Run it on the host instead:
          <pre>{agent.data.command.replace("<link>", target)}</pre>
        </div>
      )}
    </div>
  );
}

function when(iso: string) {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleString([], { dateStyle: "short", timeStyle: "short" });
}

function timestamp(seconds: number) {
  return `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
}

/** Step number -> first time its caption appears in the clip's subtitles. */
function useStepTimes(url: string | null) {
  const [times, setTimes] = useState<Record<number, number>>({});
  useEffect(() => {
    if (!url) return;
    let live = true;
    fetch(url)
      .then((r) => (r.ok ? r.text() : ""))
      .then((text) => {
        const out: Record<number, number> = {};
        const re = /(\d+):(\d+):(\d+)\.(\d+) -->[^\n]*\nStep (\d+) ·/g;
        for (const m of text.matchAll(re)) {
          const step = Number(m[5]);
          if (!(step in out)) out[step] = Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]) + Number(m[4]) / 1000;
        }
        if (live) setTimes(out);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [url]);
  return times;
}

function stepLine(s: VictimStep) {
  if (s.actor !== "agent") return `You, in the window: ${s.action}`;
  const text = s.action.replace(/^\[\d+\]\s*\w+:\s*/, "").replace(/\s*->\s*\S+$/, "");
  const verb: Record<string, string> = {
    link: "Clicks", button: "Clicks", close: "Closes", chat_option: "Answers", text_input: "Types",
    media: "Plays", form: "Fills in and sends", scroll: "Scrolls and reads",
  };
  return s.kind === "scroll" ? verb.scroll : `${verb[s.kind ?? ""] ?? ""} “${text.slice(0, 80)}”`;
}

/** The selected run: live progress while it goes, then the video, steps, pages and form. */
export function VictimRunView({ agent }: { agent: Agent }) {
  const run = agent.run;
  const player = useRef<HTMLVideoElement>(null);
  const clip = run?.clips[0];
  const times = useStepTimes(run && clip?.captions ? victim.file(run.id, clip.captions) : null);
  const runs = agent.data?.runs ?? [];
  const picker = runs.length > 0 && (
    <select className="input victim-pick" aria-label="Earlier runs" value={agent.selected ?? ""}
      onChange={(e) => agent.setSelected(e.target.value || null)}>
      <option value="">{runs.length} earlier run{runs.length > 1 ? "s" : ""}…</option>
      {runs.map((r) => (
        <option key={r.id} value={r.id}>
          {when(r.startedAt)} · {r.startHost || "starting"} · {r.persona} · {r.steps} steps
        </option>
      ))}
    </select>
  );
  if (!run) {
    return (
      <>
        {agent.error && <div className="error">{agent.error}</div>}
        {agent.error && agent.data?.lastLog && <pre className="victim-log">{agent.data.lastLog}</pre>}
        {picker && <div className="victim-earlier">{picker}</div>}
      </>
    );
  }
  const seek = (t: number) => {
    if (!player.current) return;
    player.current.currentTime = t;
    void player.current.play().catch(() => undefined);
  };
  const live = run.live;
  const running = agent.running;
  const agentSteps = run.steps.filter((s) => s.actor === "agent");
  return (
    <div className="card victim-run">
      <div className="victim-head">
        <b>{run.startHost || run.startUrl}</b>
        <span className={`chip ${running ? "on" : ""}`}>{running ? live.status || run.status : run.status}</span>
        <span className="small">
          {run.persona} · {agentSteps.length} steps · {run.pageCount} page{run.pageCount === 1 ? "" : "s"}
          {!running && run.stop && ` · ended: ${run.stopWords}`}
        </span>
        {picker}
      </div>

      {running && (
        <div className="victim-live">
          <div><span className="spinner" /> Step {live.step || "…"} {live.stage && `· page ${live.stage}`}</div>
          {live.next && <div><b>Next:</b> {live.next.replace(/^\[\d+\]\s*/, "")}</div>}
          {live.reason && <div className="small">Why: {live.reason}</div>}
          {live.notice && <div className="probe-warn">{live.notice}</div>}
          <div className="probe-actions" style={{ justifyContent: "flex-start" }}>
            {live.status === "paused" ? (
              <button className="btn" onClick={() => victim.control(run.id, "resume")}>Resume</button>
            ) : (
              <button className="btn" onClick={() => victim.control(run.id, "pause")}>Pause</button>
            )}
            <button className="btn" onClick={() => victim.control(run.id, "stop")}>Stop</button>
            <span className="small">While paused you can act in the window yourself; it's logged as yours.</span>
          </div>
        </div>
      )}

      {!running && (
        <div className="victim-watch">
          {clip ? (
            <video ref={player} controls preload="metadata" className="victim-video"
              style={run.size ? { aspectRatio: `${run.size.width} / ${run.size.height}` } : undefined}
              src={`${victim.file(run.id, clip.video)}#t=0.5`}>
              {clip.captions && <track kind="captions" srcLang="en" label="What the agent does" default
                src={victim.file(run.id, clip.captions)} />}
            </video>
          ) : (
            <div className="victim-novideo small">No video for this run.</div>
          )}
          <ol className="victim-steps">
            {run.steps.map((s, i) => (
              <li key={i} className={s.actor === "agent" ? "" : "user"}>
                <button className="text-link" disabled={!clip || s.step == null || !(s.step in times)}
                  onClick={() => s.step != null && seek(times[s.step])}>
                  {s.step != null && s.step in times ? timestamp(times[s.step]) : ""}
                </button>
                <span><b>{s.step}</b> {stepLine(s)}{s.reason && <span className="small"> — {s.reason}</span>}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {run.pages.length > 0 && (
        <div>
          <b>Pages it reached</b>
          <div className="victim-pages">
            {run.pages.map((p, i) => (
              <div key={p.index} className="victim-page-wrap">
                {i > 0 && (
                  <div className="victim-arrow small">
                    {HOW[p.how] ?? p.how}
                    {p.redirects.length > 0 && <><br />{p.redirects.length} redirect{p.redirects.length > 1 ? "s" : ""}</>}
                    <br />→
                  </div>
                )}
                <div className="victim-page">
                  {p.screenshot && (
                    <a href={victim.file(run.id, p.screenshot)} target="_blank" rel="noreferrer">
                      <img src={victim.file(run.id, p.screenshot)} alt={`Page ${p.index}`} loading="lazy" />
                    </a>
                  )}
                  <b>{p.index} · {p.kinds.map((k) => PAGE_KIND[k] ?? k).join(" and ")}</b>
                  <div className="small" title={p.url}>{p.host}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {(run.forms.length > 0 || run.items.length > 0) && (
        <div className="victim-facts">
          {run.forms.map((f, i) => (
            <div key={i}>
              <b>{f.sent ? "Form it sent" : "Form it filled but didn't send"}</b> <span className="small">(made-up details)</span>
              <div className="small">{Object.values(f.values).join(" · ") || "no fields"}</div>
              {f.sentTo.length > 0 && (
                <div className="small">Sent to {[...new Set(f.sentTo.map((x) => x.host + x.path))].join(", ")}</div>
              )}
              {f.stop && <div className="small">Not sent: {f.stop}</div>}
            </div>
          ))}
          {run.items.length > 0 && (
            <div>
              <b>In the Library</b>
              <div className="small">
                {run.items.length} page{run.items.length > 1 ? "s" : ""} added as one case:{" "}
                {run.items.map((it, i) => (
                  <span key={it.id}>{i > 0 && ", "}<Link to={`/item/${encodeURIComponent(it.id)}`}>page {i + 1}</Link></span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
      {!running && run.stopDetail && run.stop !== "no_actions" && <div className="small">Why it ended: {run.stopDetail}</div>}
      {agent.error && <div className="error">{agent.error}</div>}
    </div>
  );
}
