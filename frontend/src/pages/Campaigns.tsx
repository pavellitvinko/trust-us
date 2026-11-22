import { Fragment, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, type CampaignCard, type KitBox } from "../api";
import { Chip, Help, Picture, useMeta } from "../components/bits";
import { campaignUrl, usePolled } from "../hooks";

const itemUrl = (id: string) => `/item/${encodeURIComponent(id)}`;
const SHOWN = 4; // pictures on a card before "+N more"

/** The card or kit the address points at, scrolled into view and briefly marked. */
function useScrollToHash(ready: boolean) {
  const { hash } = useLocation();
  useEffect(() => {
    if (!ready || !hash) return;
    const el = document.getElementById(decodeURIComponent(hash.slice(1)));
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("flash");
    const timer = window.setTimeout(() => el.classList.remove("flash"), 1600);
    return () => window.clearTimeout(timer);
  }, [ready, hash]);
}

function Card({ c }: { c: CampaignCard }) {
  const [all, setAll] = useState(false);
  const shown = all ? c.members : c.members.slice(0, SHOWN);
  return (
    <article className="ccard" id={c.id}>
      <div className="strip">
        {shown.map((m, n) => (
          <Fragment key={m.id}>
            {c.journey && n > 0 && (
              <span className="arrow" aria-label="then">
                →
              </span>
            )}
            <Link to={itemUrl(m.id)} title={m.title}>
              <Picture id={m.id} has={m.picture} type={m.type} width={168} height={105} />
            </Link>
          </Fragment>
        ))}
        {c.members.length > SHOWN && !all && (
          <button className="btn ghost" onClick={() => setAll(true)}>
            +{c.members.length - SHOWN} more
          </button>
        )}
      </div>
      <h2 className="ctitle">{c.title}</h2>
      <div className="small">
        <Chip kind="campaign">{c.name}</Chip> {c.facts}
      </div>
      <div className="why">
        <div className="label">Why we think it's one operation</div>
        {c.why.text}
        {c.why.strength && <span className={`strength s-${c.why.strength.toLowerCase()}`}>{c.why.strength}</span>}
        {c.why.note && <div className="small">{c.why.note}</div>}
      </div>
      <div>
        {c.signs.count ? (
          <>
            <span className="danger">{c.signs.text}</span>
            <span className="small">, for example: {c.signs.examples.map((x) => x[0].toLowerCase() + x.slice(1)).join("; ")}</span>
          </>
        ) : (
          <span className="muted">{c.signs.text}</span>
        )}
      </div>
      {c.kit && (
        <div className="small" style={{ marginTop: 6 }}>
          <Link to={campaignUrl(c.kit.id)}>{c.kit.text}</Link> <Chip kind="kit">{c.kit.name}</Chip>
        </div>
      )}
    </article>
  );
}

function Kit({ k }: { k: KitBox }) {
  const meta = useMeta();
  return (
    <section className="kitbox" id={k.id}>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
        <Chip kind="kit">{k.name}</Chip>
        <span className="small">{k.facts}</span>
      </div>
      {k.why && <div style={{ margin: "6px 0 2px" }}>{k.why}</div>}
      <div className="small">{meta?.groups.kit}</div>
      <div className="kitmembers">
        {k.members.map((m) => (
          <Link key={m.id} to={itemUrl(m.id)} className="kitmember">
            <Picture id={m.id} has={m.picture} type={m.type} width={96} height={60} />
            <span>
              <span className="title">{m.title}</span>
              <span className="small">
                {" "}
                {m.case}
                {m.campaign && " · "}
                {m.campaign && <Chip kind="campaign">{m.campaign}</Chip>}
              </span>
            </span>
          </Link>
        ))}
      </div>
    </section>
  );
}

export default function Campaigns() {
  const meta = useMeta();
  const { data, error } = usePolled(api.campaigns);
  useScrollToHash(!!data);
  if (error) return <p className="error">Couldn't load the campaigns: {error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const empty = !data.campaigns.length && !data.kits.length;
  return (
    <>
      <div className="head">
        <h1>Campaigns</h1>
        {data.count && <span className="sub">{data.count}</span>}
        {data.busy && (
          <span className="small" style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span className="spinner" /> Updating while the analysis runs
          </span>
        )}
      </div>
      {meta && <p className="lead">{meta.groups.campaign}</p>}
      {empty ? (
        <div className="empty card">
          <p>No campaigns yet: nothing links two items through something only the scammer controls.</p>
          <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
            <Link className="btn primary" to="/add">
              Add material
            </Link>
            <Link className="btn" to="/library">
              Open the Library
            </Link>
          </div>
        </div>
      ) : (
        <>
          <div className="ccards">
            {data.campaigns.map((c) => (
              <Card key={c.id} c={c} />
            ))}
          </div>
          {data.kits.length > 0 && (
            <>
              <h2>
                Shared kits <Help topic="kit" />
              </h2>
              {data.kits.map((k) => (
                <Kit key={k.id} k={k} />
              ))}
            </>
          )}
        </>
      )}
      {(data.unlinked || data.repeats) && (
        <div className="foot small">
          {data.unlinked && (
            <p>
              {data.unlinked} <Link to="/library">See them in the Library</Link>
            </p>
          )}
          {data.repeats && <p>{data.repeats}</p>}
        </div>
      )}
    </>
  );
}
