import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, pictureUrl, type GroupDetail } from "../api";
import { Chip, Help, Picture, plural, useMeta } from "../components/bits";
import { itemUrl, LinkPair, PathView } from "../components/links";
import { campaignUrl, kitUrl } from "../hooks";
import { GroupRow, GroupTable } from "./Campaigns";

/** Up to four member pictures, the first one large. */
function Pictures({ group }: { group: GroupDetail }) {
  const [first, ...rest] = group.pictures;
  if (!first) return null;
  return (
    <div>
      <Link to={itemUrl(first.id)} title={first.title}>
        {first.picture ? (
          <img className="big" src={pictureUrl(first.id, "full")} alt="" />
        ) : (
          <Picture id={first.id} has={false} type={first.type} width={520} height={320} />
        )}
      </Link>
      {rest.length > 0 && (
        <div className="strip" style={{ marginTop: 10 }}>
          {rest.slice(0, 3).map((p) => (
            <Link key={p.id} className="other" to={itemUrl(p.id)} title={p.title}>
              <Picture id={p.id} has={p.picture} type={p.type} width={124} height={78} />
            </Link>
          ))}
          {rest.length > 3 && <span className="small">and {rest.length - 3} more below</span>}
        </div>
      )}
    </div>
  );
}

/** A campaign's page, or a kit's (route /campaigns/kit/:id): one layout for both. */
export default function CampaignPage({ kit = false }: { kit?: boolean }) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const meta = useMeta();
  const [group, setGroup] = useState<GroupDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setGroup(null);
    setError(null);
    (kit ? api.kit(id) : api.campaign(id)).then(setGroup).catch((e) => setError(e.message));
    window.scrollTo(0, 0);
  }, [id, kit]);

  const back = (
    <a
      className="back"
      href="/campaigns"
      onClick={(e) => {
        e.preventDefault();
        if (window.history.state?.idx > 0) navigate(-1);
        else navigate("/campaigns");
      }}
    >
      ← Campaigns
    </a>
  );
  if (error) return <>{back}<p className="error">{error}</p></>;
  if (!group) return <>{back}<p className="muted">Loading…</p></>;

  const isKit = group.kind === "kit";
  return (
    <>
      {back}
      <div className="dgrid">
        <Pictures group={group} />
        <div>
          <h1 style={{ fontSize: 20, lineHeight: 1.3 }}>{group.title}</h1>
          <div className="small" style={{ margin: "6px 0 14px" }}>
            <Chip kind={group.kind}>{group.name}</Chip> <Help topic={group.kind} />
          </div>

          <div className="box">
            <b>Facts</b>
            <table className="facts">
              <tbody>
                {group.facts.map((f) => (
                  <tr key={f.label}>
                    <td>{f.label}</td>
                    <td>{f.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="box why">
            <b>{isKit ? "What the kit is" : "Why we think it's one operation"}</b>
            {group.why.text}
            {group.why.strength && (
              <span className={`strength s-${group.why.strength.toLowerCase()}`}>{group.why.strength}</span>
            )}
            <div className="small">{isKit ? meta?.groups.kit : group.why.note}</div>
          </div>

          <div className="box">
            <b>Warning signs</b>
            {group.signs.length ? (
              group.signs.map((s) => (
                <div className="sign" key={s.text}>
                  <span>
                    {s.text}
                    {s.where && <span className="small"> · {s.where}</span>}
                  </span>
                </div>
              ))
            ) : (
              <div className="small">{group.empty}</div>
            )}
          </div>

          {group.actions.length > 0 && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {group.actions.map((a) => (
                <a key={a.url} className="btn" href={a.url} target="_blank" rel="noreferrer" title={a.help}>
                  {a.label} ↗
                </a>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* A journey shows every page in order, so the plain strip is only for the rest. */}
      {group.path ? (
        <>
          <h2>Where the link leads</h2>
          <PathView path={group.path} />
        </>
      ) : (
        <>
          <h2>
            {isKit ? "Pages built with this kit" : "Pages in this campaign"}{" "}
            <span className="sub" style={{ fontWeight: 400 }}>
              · {group.members.length}
            </span>
          </h2>
          <div className="strip">
            {group.members.map((m) => (
              <Link key={m.id} className="other member" to={itemUrl(m.id)} title={m.title}>
                <Picture id={m.id} has={m.picture} type={m.type} width={176} height={108} />
                <div className="t">{m.title}</div>
                <div className="small">
                  {m.case}
                  {m.host && ` · ${m.host}`}
                </div>
              </Link>
            ))}
          </div>
        </>
      )}

      {isKit && group.campaigns.length > 0 && (
        <>
          <h2>
            Campaigns built with this kit <Help topic="campaign" />
          </h2>
          <GroupTable first="Campaign">
            {group.campaigns.map((c) => (
              <GroupRow
                key={c.id}
                url={campaignUrl(c.id)}
                picture={c.picture}
                title={c.title}
                chip={<Chip kind="campaign">{c.name}</Chip>}
                sub={c.sub}
                linkedBy={c.linkedBy}
                strength={c.strength}
                cases={c.cases}
                signs={c.signs}
              />
            ))}
          </GroupTable>
        </>
      )}

      {group.pairs.length > 0 && (
        <>
          <h2>How the pages are linked</h2>
          {group.pairs.map((p) => (
            <LinkPair
              key={`${p.a.id}|${p.b.id}`}
              a={p.a}
              b={p.b}
              kind={p.kind}
              name={p.name}
              reason={p.reason}
              detail={p.strength && `${p.strength} link`}
            />
          ))}
        </>
      )}

      {group.kit && (
        <>
          <h2>
            Shared kit <Help topic="kit" />
          </h2>
          <div className="kitbox">
            <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
              <Link to={kitUrl(group.kit.id)}>
                <Chip kind="kit">{group.kit.name}</Chip>
              </Link>
              <span>{group.kit.text}</span>
            </div>
            {group.kit.why && <div className="small" style={{ margin: "4px 0 8px" }}>{group.kit.why}</div>}
            <div className="strip">
              {group.kit.others.map((o) => (
                <Link key={o.id} className="other" to={itemUrl(o.id)} title={o.title}>
                  <Picture id={o.id} has={o.picture} type={o.type} width={124} height={78} />
                </Link>
              ))}
            </div>
            <Link className="small" to={kitUrl(group.kit.id)} style={{ display: "inline-block", marginTop: 8 }}>
              See the whole kit
            </Link>
          </div>
        </>
      )}

      {group.possible.length > 0 && (
        <details className="more">
          <summary>
            Possible links to other material ({group.possible.length}){" "}
            <span className="small" style={{ fontWeight: 400 }}>
              · similar, but not enough to say they're connected
            </span>
          </summary>
          {group.possible.map((p) => (
            <LinkPair key={`${p.a.id}|${p.b.id}`} a={p.a} b={p.b} kind={p.kind} name={p.name} reason={p.reason} detail="" />
          ))}
        </details>
      )}

      {group.evidence.length > 0 && (
        <details className="more">
          <summary>Evidence details</summary>
          <p className="small">
            Each warning sign as the analysis recorded it, page by page, with the file and place it was found.{" "}
            {plural(group.evidence.length, "finding")}.
          </p>
          <table className="findings">
            <tbody>
              {group.evidence.map((f, n) => (
                <tr key={n}>
                  <td>{f.item}</td>
                  <td>{f.kind}</td>
                  <td>{f.summary}</td>
                  <td>
                    <code>{f.source}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </>
  );
}
