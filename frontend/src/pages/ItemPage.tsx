import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, pictureUrl, type ItemData, type MatchInfo } from "../api";
import { Chip, Help, Picture, plural, useMeta } from "../components/bits";
import { itemUrl, LinkPair, PathView } from "../components/links";
import { campaignUrl, kitUrl } from "../hooks";

function Pair({ item, match }: { item: ItemData; match: MatchInfo }) {
  const detail = [match.strength && `${match.strength} link`, match.sameCase ? "same case" : `in ${match.case}`]
    .filter(Boolean)
    .join(" · ");
  return (
    <LinkPair a={item} b={match} kind={match.kind} name={match.name} reason={match.reason} detail={detail} linkA={false} />
  );
}

function Path({ item }: { item: ItemData }) {
  if (!item.path) return <p className="small">No proven step to or from this item.</p>;
  return <PathView path={item.path} current={item.id} />;
}

export default function ItemPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const meta = useMeta();
  const [item, setItem] = useState<ItemData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setItem(null);
    setError(null);
    api
      .item(id)
      .then(setItem)
      .catch((e) => setError(e.message));
    window.scrollTo(0, 0);
  }, [id]);

  const back = (
    <a
      className="back"
      href="/library"
      onClick={(e) => {
        e.preventDefault();
        if (window.history.state?.idx > 0) navigate(-1);
        else navigate("/library");
      }}
    >
      ← Library
    </a>
  );
  if (error) return <>{back}<p className="error">{error}</p></>;
  if (!item) return <>{back}<p className="muted">Loading…</p></>;

  const firm = item.matches.filter((m) => m.kind !== "possible");
  const possible = item.matches.filter((m) => m.kind === "possible");
  return (
    <>
      {back}
      <div className="dgrid">
        <div>
          {item.picture ? (
            <a href={pictureUrl(item.id, "full")} target="_blank" rel="noreferrer" title="Open the full picture">
              <img className="big" src={pictureUrl(item.id, "full")} alt="" />
            </a>
          ) : (
            <Picture id={item.id} has={false} type={item.type} width={520} height={320} />
          )}
        </div>
        <div>
          <h1 style={{ fontSize: 20, lineHeight: 1.3 }}>{item.title}</h1>
          <div className="small" style={{ margin: "6px 0 14px" }}>
            {item.type}
            {item.host && ` · ${item.host}`} · <Chip kind="case">{item.case}</Chip>
            <Help topic="case" /> · added {item.added}
          </div>
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

      {item.findings.length > 0 && (
        <details className="more">
          <summary>Evidence details</summary>
          <p className="small">
            Each warning sign as the analysis recorded it, with the file and place it was found. {plural(item.findings.length, "finding")}.
          </p>
          <table className="findings">
            <tbody>
              {item.findings.map((f, n) => (
                <tr key={n}>
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
