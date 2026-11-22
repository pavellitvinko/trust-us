import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type GroupDetail } from "../api";
import { Chip, Help, Picture, plural, useMeta } from "../components/bits";
import { itemUrl, LinkPair, PathView } from "../components/links";
import { Band, Crumbs } from "../components/pagetop";
import { campaignUrl, kitUrl } from "../hooks";
import { GroupRow, GroupTable } from "./Campaigns";

/** A group looks like a group: its pages as a grid, never one big screenshot like an item. */
function Mosaic({ group }: { group: GroupDetail }) {
  const shown = group.pictures.slice(0, 4);
  const more = group.pictures.length - shown.length;
  return (
    <div className={`mosaic mosaic-${group.kind}`}>
      {shown.map((p) => (
        <Link key={p.id} to={itemUrl(p.id)} title={p.title}>
          <Picture id={p.id} has={p.picture} type={p.type} width={250} height={156} />
        </Link>
      ))}
      {more > 0 && <span className="small">and {more} more below</span>}
    </div>
  );
}

/** A campaign's page, or a kit's (route /campaigns/kit/:id): one layout for both. */
export default function CampaignPage({ kit = false }: { kit?: boolean }) {
  const { id = "" } = useParams();
  const meta = useMeta();
  const [group, setGroup] = useState<GroupDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setGroup(null);
    setError(null);
    (kit ? api.kit(id) : api.campaign(id)).then(setGroup).catch((e) => setError(e.message));
    window.scrollTo(0, 0);
  }, [id, kit]);

  const top = (here: string) => (
    <Crumbs
      parts={[
        { label: "Campaigns", to: "/campaigns" },
        ...(kit ? [{ label: "Shared kits", to: "/campaigns" }] : []),
        { label: here },
      ]}
    />
  );
  if (error) return <>{top(kit ? "Kit" : "Campaign")}<p className="error">{error}</p></>;
  if (!group) return <>{top("…")}<p className="muted">Loading…</p></>;

  const isKit = group.kind === "kit";
  return (
    <div className={`page-${group.kind}`}>
      {top(group.short)}
      <div className="dgrid">
        <Mosaic group={group} />
        <div>
          <Band kind={group.kind} />
          <h1 style={{ fontSize: 20, lineHeight: 1.3 }}>{group.title}</h1>
          <div className="small" style={{ margin: "6px 0 14px" }}>
            {group.summary} · <Chip kind={group.kind}>{group.name}</Chip> <Help topic={group.kind} />
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
    </div>
  );
}
