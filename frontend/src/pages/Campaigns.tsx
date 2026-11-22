import { Link, useNavigate } from "react-router-dom";
import { api, type Brief, type SignsCell } from "../api";
import { Chip, Help, Picture, useMeta } from "../components/bits";
import { campaignUrl, kitUrl, usePolled } from "../hooks";

export function SignsText({ signs }: { signs: SignsCell }) {
  return signs.count ? <span className="danger">{signs.text}</span> : <span className="muted">{signs.text}</span>;
}

/** One row of a Campaigns table: the whole row opens the campaign or kit. */
export function GroupRow({
  url,
  picture,
  title,
  chip,
  sub,
  linkedBy,
  strength,
  cases,
  signs,
}: {
  url: string;
  picture: Brief;
  title: string;
  chip: React.ReactNode;
  sub: string;
  linkedBy: string;
  strength?: string;
  cases: string;
  signs: SignsCell;
}) {
  const navigate = useNavigate();
  return (
    <tr onClick={() => navigate(url)}>
      <td>
        <Picture id={picture.id} has={picture.picture} type={picture.type} width={96} height={60} />
      </td>
      <td>
        <Link className="title" to={url} onClick={(e) => e.stopPropagation()}>
          {title}
        </Link>
        <div className="small">
          {chip} {sub}
        </div>
      </td>
      <td>
        {linkedBy}
        {strength && <span className="small"> · {strength.toLowerCase()}</span>}
      </td>
      <td>{cases}</td>
      <td>
        <SignsText signs={signs} />
      </td>
    </tr>
  );
}

export function GroupTable({ first, children }: { first: React.ReactNode; children: React.ReactNode }) {
  return (
    <table className="items groups">
      <thead>
        <tr>
          <th style={{ width: 112 }} />
          <th>{first}</th>
          <th style={{ width: 230 }}>Linked by</th>
          <th style={{ width: 130 }}>
            Cases <Help topic="case" />
          </th>
          <th style={{ width: 130 }}>Warning signs</th>
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  );
}

export default function Campaigns() {
  const meta = useMeta();
  const { data, error } = usePolled(api.campaigns);
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
          {data.campaigns.length > 0 && (
            <GroupTable first="Campaign">
              {data.campaigns.map((c) => (
                <GroupRow
                  key={c.id}
                  url={campaignUrl(c.id)}
                  picture={c.picture}
                  title={c.title}
                  chip={
                    <>
                      <Chip kind="campaign">{c.name}</Chip>
                      {c.kit && <Chip kind="kit">Kit</Chip>}
                    </>
                  }
                  sub={c.sub}
                  linkedBy={c.linkedBy}
                  strength={c.strength}
                  cases={c.cases}
                  signs={c.signs}
                />
              ))}
            </GroupTable>
          )}
          {data.kits.length > 0 && (
            <>
              <h2>
                Shared kits <Help topic="kit" />
              </h2>
              <GroupTable first="Kit">
                {data.kits.map((k) => (
                  <GroupRow
                    key={k.id}
                    url={kitUrl(k.id)}
                    picture={k.picture}
                    title={k.title}
                    chip={<Chip kind="kit">{k.name}</Chip>}
                    sub={k.sub}
                    linkedBy={k.linkedBy}
                    cases={k.cases}
                    signs={k.signs}
                  />
                ))}
              </GroupTable>
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
