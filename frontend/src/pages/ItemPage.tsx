import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, pictureUrl, type AdInfo, type ItemData, type MatchInfo } from "../api";
import { Chip, Help, Picture, useMeta } from "../components/bits";
import { ItemEvidence } from "../components/evidence";
import { VideoInspection } from "../components/video";
import { itemUrl, LinkPair, PathView } from "../components/links";
import { Band, Crumbs } from "../components/pagetop";
import ItemChecks from "../components/ItemChecks";
import { campaignUrl, kitUrl } from "../hooks";

function Pair({ item, match }: { item: ItemData; match: MatchInfo }) {
  const detail = [match.strength && `${match.strength} link`, match.sameCase ? "same case" : `in ${match.case}`]
    .filter(Boolean)
    .join(" · ");
  return (
    <LinkPair a={item} b={match} kind={match.kind} name={match.name} reason={match.reason} detail={detail} linkA={false} />
  );
}

function AdDetails({ ad }: { ad: AdInfo }) {
  return (
    <div className="box">
      <b>Ad details</b>
      {ad.libraryId && (
        <div className="grp">
          <span className="small" style={{ minWidth: 84 }}>Library ID</span>
          <div>
            <code>{ad.libraryId}</code>
            <div className="small">
              {ad.librarySource}
              {ad.via && (
                <>
                  : <Link to={itemUrl(ad.via.id)}>{ad.via.title}</Link>
                </>
              )}
              {ad.libraryEvidence.map((e, i) => (
                <div key={i} title={e.where}>
                  “{e.text}”
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      {ad.advertiser && (
        <div className="grp">
          <span className="small" style={{ minWidth: 84 }}>Advertiser</span>
          <div>
            {ad.advertiser} <span className="small">· as written on the screenshot</span>
          </div>
        </div>
      )}
      {ad.links.map((l) => (
        <div className="grp" key={`${l.kind}${l.id}`}>
          <span className="small" style={{ minWidth: 84 }}>{l.name}</span>
          <div>
            <Link to={itemUrl(l.id)}>{l.title}</Link>
            <div className="small">{l.evidence.join(" · ")}</div>
          </div>
        </div>
      ))}
      {ad.moreAdvertiserPeers > 0 && (
        <div className="small">
          …and {ad.moreAdvertiserPeers} more screenshots with the same advertiser name in this case.
        </div>
      )}
      {ad.links.length > 0 && (
        <div className="small" style={{ marginTop: 6 }}>
          One advertiser can run many ads and campaigns: these links do not put items in one campaign.
        </div>
      )}
    </div>
  );
}

function Path({ item }: { item: ItemData }) {
  if (!item.path) return <p className="small">No proven step to or from this item.</p>;
  return <PathView path={item.path} current={item.id} />;
}

export default function ItemPage() {
  const { id = "" } = useParams();
  const meta = useMeta();
  const [item, setItem] = useState<ItemData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [videoPending, setVideoPending] = useState(false);
  const [videoError, setVideoError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    setItem((current) => current?.id === id ? current : null);
    setError(null);
    function load() {
      api.item(id).then((data) => {
        if (!alive) return;
        setItem(data);
        setError(null);
        timer = window.setTimeout(load, data.busy ? 1500 : 10000);
      }).catch((e) => {
        if (alive) { setError(e.message); timer = window.setTimeout(load, 10000); }
      });
    }
    load();
    if (!window.location.hash) window.scrollTo(0, 0);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [id, revision]);
  useEffect(() => {
    if (item?.id === id && window.location.hash === "#checks") {
      document.getElementById("checks")?.scrollIntoView({ block: "start" });
    }
  }, [id, item?.id]);

  const back = (here?: { case: string; type: string }) => (
    <Crumbs
      parts={[
        { label: "Library", to: "/library" },
        ...(here ? [{ label: here.case, to: "/library" }, { label: here.type.toLowerCase() }] : []),
      ]}
    />
  );
  if (error) return <>{back()}<p className="error">{error}</p></>;
  if (!item) return <>{back()}<p className="muted">Loading…</p></>;
  const campaign = item.groups.find((g) => g.kind === "campaign");

  const firm = item.matches.filter((m) => m.kind !== "possible");
  const possible = item.matches.filter((m) => m.kind === "possible");
  return (
    <>
      {back(item)}
      <div className="dgrid">
        <div>
          {item.type === "Video" ? <VideoInspection key={item.id} id={item.id} /> : item.picture ? (
            <a href={pictureUrl(item.id, "full")} target="_blank" rel="noreferrer" title="Open the full picture">
              <img className="big" src={pictureUrl(item.id, "full")} alt="" />
            </a>
          ) : (
            <Picture id={item.id} has={false} type={item.type} width={520} height={320} />
          )}
        </div>
        <div>
          <Band kind="item" text={`One ${item.type === "Image" ? "picture" : item.type.toLowerCase()}`} />
          {item.type === "Video" && <section className="box video-analysis-controls" aria-label="Video analysis">
            <strong>Video analysis</strong>
            <p className="small">The automatic pass uses file hashes, metadata and available text. Full speech, scene and technique checks run on demand.</p>
            {item.videoAnalysis && <p className="small" role="status">{item.videoAnalysis.status === "running" || item.videoAnalysis.status === "queued"
              ? item.videoAnalysis.stage : item.videoAnalysis.status === "failed" ? item.videoAnalysis.error : "Analysis finished. See check coverage below."}</p>}
            <button className="btn primary" disabled={videoPending} onClick={async () => {
              setVideoPending(true); setVideoError(null);
              try {
                if (item.videoAnalysis && ["queued", "running"].includes(item.videoAnalysis.status)) await api.cancelVideoAnalysis(id);
                else await api.analyzeVideo(id);
                setRevision((r) => r + 1);
              } catch (e) { setVideoError(e instanceof Error ? e.message : String(e)); }
              finally { setVideoPending(false); }
            }}>{videoPending ? "Updating…" : item.videoAnalysis && ["queued", "running"].includes(item.videoAnalysis.status) ? "Cancel analysis" : "Analyze video"}</button>
            {videoError && <p className="error" role="alert">{videoError}</p>}
          </section>}
          <h1 style={{ fontSize: 20, lineHeight: 1.3 }}>{item.title}</h1>
          <div className="small" style={{ margin: "6px 0 6px" }}>
            {item.type}
            {item.host && ` · ${item.host}`} · <Chip kind="case">{item.case}</Chip>
            <Help topic="case" /> · added {item.added}
          </div>
          {campaign && (
            <div className="partof">
              Part of <Link to={campaignUrl(campaign.id)}>{campaign.title}</Link> →
            </div>
          )}
          <div style={{ height: 8 }} />
          {item.groups.length > 0 && (
            <div className="box">
              {item.groups.map((g) => (
                <div className="grp" key={g.name}>
                  <Link
                    to={g.kind === "kit" ? kitUrl(g.id) : campaignUrl(g.id)}
                    title={g.kind === "kit" ? "See this kit" : "See this campaign"}
                  >
                    <Chip kind={g.kind}>{g.name}</Chip>
                  </Link>
                  <div>
                    {g.reason && <>{g.reason}. </>}
                    <span className="small">{meta?.groups[g.kind]}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="box">
            <b>Warning signs</b>
            {item.warningSigns.length ? (
              item.warningSigns.map((s) => (
                <div className="sign" key={s}>
                  {s}
                </div>
              ))
            ) : (
              <div className="small">{item.checked ? "None found." : "Not checked."}</div>
            )}
            {item.unchecked.length > 0 && item.checked && (
              <div className="small" style={{ marginTop: 6 }}>
                Not checked: {item.unchecked.join(", ")}. "None found" says nothing about these.
              </div>
            )}
          </div>
          {item.ad && <AdDetails ad={item.ad} />}
          {item.actions.length > 0 && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {item.actions.map((a) => (
                <a key={a.url} className="btn" href={a.url} target="_blank" rel="noreferrer" title={a.help}>
                  {a.label} ↗
                </a>
              ))}
            </div>
          )}
        </div>
      </div>

      <ItemChecks processing={item.processing} id={item.id} refresh={() => setRevision((r) => r + 1)} />

      <h2>Where the link leads</h2>
      <Path item={item} />

      <h2>
        Linked items {firm.length > 0 && <span className="sub" style={{ fontWeight: 400 }}>· {firm.length}</span>}
      </h2>
      {firm.length ? (
        firm.map((m) => <Pair key={m.id} item={item} match={m} />)
      ) : (
        <p className="small">
          {item.comparable
            ? "Nothing found yet that this item shares with other material."
            : "Too little could be read from this item to compare it with others."}
        </p>
      )}
      {possible.length > 0 && (
        <details className="more">
          <summary>
            Possible links ({possible.length}){" "}
            <span className="small" style={{ fontWeight: 400 }}>
              · similar, but not enough to say they're connected
            </span>
          </summary>
          {possible.map((m) => (
            <Pair key={m.id} item={item} match={m} />
          ))}
        </details>
      )}

      {item.sameCase.length > 0 && (
        <>
          <h2>
            Added in the same case <Help topic="case" />
          </h2>
          <div className="strip">
            {item.sameCase.slice(0, 16).map((s) => (
              <Link key={s.id} className="other" to={itemUrl(s.id)} title={s.title}>
                <Picture id={s.id} has={s.picture} type={s.type} width={124} height={78} />
              </Link>
            ))}
            {item.sameCase.length > 16 && <span className="small">and {item.sameCase.length - 16} more</span>}
          </div>
        </>
      )}

      <ItemEvidence key={item.id} item={item} />
    </>
  );
}
