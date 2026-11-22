import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, compareUrl, type Brief, type LinkKind, type PathData, type ComparisonData } from "../api";
import { Chip, Help, Picture } from "./bits";
import { evidenceValue, PivotButton, SourceButton } from "./evidence";

export const itemUrl = (id: string) => `/item/${encodeURIComponent(id)}`;

/** The full comparison report of two items, fetched on request. */
export function Comparison({ a, b }: { a: string; b: string }) {
  const [data, setData] = useState<ComparisonData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api
      .compare(a, b)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [a, b]);
  if (error) return <p className="error">{error}</p>;
  if (data === null) return <p className="muted">Building the comparison…</p>;
  const seen = new Set<string>();
  const shared = (data.sharedRefs ?? []).map((ref) => data.sources[ref]).filter((row) => {
    if (!row?.pivot) return false;
    const key = `${row.namespace}:${JSON.stringify(row.value)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return (
    <div style={{ textAlign: "left", marginTop: 8 }}>
      <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
        <a className="btn" href={compareUrl(a, b, "md")}>
          Download Markdown
        </a>
        <a className="btn" href={compareUrl(a, b, "json")}>
          Download JSON
        </a>
      </div>
      {shared.length > 0 && <details className="more"><summary>Shared identifiers ({shared.length})</summary>
        <table className="findings"><tbody>{shared.map((row) => <tr key={row.ref}>
          <td>{row.namespace}</td><td className="evidence-value">{evidenceValue(row.value)}</td>
          <td><SourceButton row={row} /><br /><PivotButton row={row} /></td>
        </tr>)}</tbody></table>
      </details>}
      {/* Keep hostile report text escaped; only known source tokens become controls. */}
      <pre className="report">{data.markdown.split(/(\[[ab]:\d+\])/g).map((part, n) => {
        const row = data.sources?.[part.slice(1, -1)];
        return row ? <SourceButton key={n} row={row}>{part}</SourceButton> : part;
      })}</pre>
    </div>
  );
}

function Side({ b, link }: { b: Brief; link: boolean }) {
  const inner = (
    <>
      <Picture id={b.id} has={b.picture} type={b.type} width={156} height={96} />
      {link && <div className="t">{b.title}</div>}
    </>
  );
  return link ? (
    <Link className="other" to={itemUrl(b.id)} title={b.title}>
      {inner}
    </Link>
  ) : (
    <div>{inner}</div>
  );
}

/** Two items side by side with what links them in the middle, and the full comparison on request. */
export function LinkPair({
  a,
  b,
  kind,
  name,
  reason,
  detail,
  linkA = true,
}: {
  a: Brief;
  b: Brief;
  kind: LinkKind;
  name: string;
  reason: string;
  detail: string;
  linkA?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <div className="pair">
        <Side b={a} link={linkA} />
        <div className="mid">
          <Chip kind={kind}>{name}</Chip>
          <Help topic={kind} />
          <div className={`bar ${kind}`} />
          {reason}
          {detail && <div className="small">{detail}</div>}
          <div className="actions">
            <button className="btn ghost small" onClick={() => setOpen((o) => !o)}>
              {open ? "Hide the full comparison" : "Compare in full"}
            </button>
          </div>
        </div>
        <Side b={b} link />
      </div>
      {open && <Comparison a={a.id} b={b.id} />}
    </>
  );
}

/** A journey as pictures: the ad it starts from, each page, and why each leads to the next. */
export function PathView({ path, current }: { path: PathData; current?: string }) {
  return (
    <div className="path">
      {path.ad && (
        <>
          <div className="adbox">
            <b>Facebook ad</b>
            <span>
              ID {path.ad.slice(0, 6)}…{path.ad.slice(-4)}
            </span>
            <span className="muted">not added yet</span>
          </div>
          <div className="arrow">
            <b>→</b>The page address carries this ad's ID
          </div>
        </>
      )}
      {path.steps.map((s, n) => (
        <div key={s.id} style={{ display: "contents" }}>
          <Link className={`step ${s.id === current ? "me" : ""}`} to={itemUrl(s.id)}>
            <Picture id={s.id} has={s.picture} type={s.type} width={176} height={108} />
            <div className="t">
              {n + 1}. {s.title}
            </div>
            <div className="small">
              {n === 0 ? "first page" : "next page"}
              {s.id === current && " · this item"}
            </div>
          </Link>
          {n < path.steps.length - 1 && (
            <div className="arrow">
              <b>→</b>
              {path.reasons[n]}
            </div>
          )}
        </div>
      ))}
      <div className="arrow">
        <b style={{ color: "#c3c2b7" }}>⇢</b>next page not captured
      </div>
    </div>
  );
}
