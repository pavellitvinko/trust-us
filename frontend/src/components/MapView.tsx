import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { pictureUrl, type LibraryData, type Row } from "../api";
import { plural } from "./bits";
import { layoutClusters, type GraphCluster, type GraphHull } from "./mapGraph";
import { buildTree, filterTree, shortCase, type TreeNode } from "./mapTree";

const ZOOMS = [1, 1.25, 1.5, 2];
const ITEM_R = 10;
const short = (text: string, n: number) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);
const allRows = (node: TreeNode): Row[] => [...node.rows, ...node.children.flatMap(allRows)];

type Selected = { title: string; note: string; reasons: string[]; href?: string; items: GraphCluster["items"] };

function selection(cluster: GraphCluster | undefined, hull: GraphHull | undefined, clusters: GraphCluster[]): Selected | null {
  if (hull) {
    const ids = new Set(allRows(hull.node).map((r) => r.id));
    return {
      title: hull.node.title,
      note: hull.node.note,
      reasons: hull.node.reasons,
      href: hull.node.href,
      items: clusters.flatMap((c) => c.items).filter((i) => ids.has(i.row.id)),
    };
  }
  if (!cluster) return null;
  return {
    title: cluster.title,
    note: cluster.node?.note ?? "",
    reasons: cluster.node?.reasons ?? [],
    href: cluster.node?.href,
    items: cluster.items,
  };
}

function Details({ selected }: { selected: Selected | null }) {
  if (!selected) {
    return (
      <aside className="cluster-side">
        <b>Pick a cluster</b>
        <p className="small">Click a bubble to see what's in it and why those items belong together.</p>
      </aside>
    );
  }
  return (
    <aside className="cluster-side">
      <b>{selected.title}</b>
      {selected.note && <p className="small">{selected.note}</p>}
      {selected.reasons.length > 0 && <p className="small"><b>Linked by:</b> {selected.reasons.join(", ")}</p>}
      {selected.href && <Link className="btn" to={selected.href}>Open full view</Link>}
      <ul>
        {selected.items.map(({ row, note }) => (
          <li key={row.id}>
            <Link to={`/item/${encodeURIComponent(row.id)}`} title={note?.detail}>
              <span className="t">{row.title}</span>
              <span className="small">
                {shortCase(row.case)}
                {row.signs > 0 && <span className="danger"> · {plural(row.signs, "warning sign")}</span>}
                {row.duplicateOf && <span> · same page also in {shortCase(row.duplicateOf)}</span>}
                {row.links === 0 && row.possible > 0 && <span> · {plural(row.possible, "possible link")}</span>}
                {row.links === 0 && !row.comparable && <span> · too little to compare</span>}
              </span>
              {note && <span className="small cluster-note">{note.text}</span>}
            </Link>
          </li>
        ))}
      </ul>
    </aside>
  );
}

/** The Library as a graph of clusters: bubbles of items, joined where they share evidence. */
export default function MapView({ rows, map }: { rows: Row[]; map: LibraryData["map"] }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [zoom, setZoom] = useState(0);
  const [picked, setPicked] = useState<string | null>(null);
  const { tree, crossLinks } = useMemo(() => buildTree(rows, map), [rows, map]);
  const graph = useMemo(() => layoutClusters(tree, map.edges, crossLinks), [tree, map.edges, crossLinks]);
  const shown = useMemo(() => filterTree(tree, query, map.repeats), [tree, query, map.repeats]);
  const searching = query.trim().length > 0;
  const matches = useMemo(() => new Set(shown.flatMap(allRows).map((r) => r.id)), [shown]);
  const found = shown.reduce((sum, n) => sum + n.count, 0);
  if (!rows.length) return <div className="empty card">No items to show yet.</div>;

  const dim = (ids: string[]) => searching && !ids.some((id) => matches.has(id));
  const pickKeys = (key: string) => (event: React.KeyboardEvent) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setPicked(key);
    }
  };
  const selected = selection(
    graph.clusters.find((c) => c.key === picked),
    graph.hulls.find((h) => h.key === picked),
    graph.clusters,
  );
  const open = (id: string) => navigate(`/item/${encodeURIComponent(id)}`);

  return (
    <div className="cluster-page">
      <div className="tree-tools">
        <input
          className="input"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name, site or case"
          aria-label="Search the map"
        />
        <span className="graph-zoom">
          <button className="btn" aria-label="Zoom out" disabled={zoom === 0} onClick={() => setZoom(zoom - 1)}>
            −
          </button>
          <span className="small">{Math.round(ZOOMS[zoom] * 100)}%</span>
          <button className="btn" aria-label="Zoom in" disabled={zoom === ZOOMS.length - 1} onClick={() => setZoom(zoom + 1)}>
            +
          </button>
        </span>
      </div>
      <div className="cluster-key small">
        <span><i className="dot campaign" />Campaign: likely the same people</span>
        <span><i className="dot template" />Template: reused, maybe different people</span>
        <span><i className="dot copies" />Same page added again</span>
        <span><i className="dot none" />No firm link yet</span>
        <span aria-live="polite">{searching ? `${plural(found, "item")} found` : ""}</span>
      </div>
      <div className="cluster-layout">
        <div className="cluster-scroll" role="region" aria-label="Graph of clusters" tabIndex={0}>
          <svg
            viewBox={`0 0 ${graph.width} ${graph.height}`}
            style={{ width: `${ZOOMS[zoom] * 100}%` }}
            role="group"
            aria-label="Clusters of linked items"
            onClick={() => setPicked(null)}
          >
            {graph.hulls.map((h) => (
              <g
                key={h.key}
                className={`cluster-hull ${picked === h.key ? "on" : ""} ${dim(allRows(h.node).map((r) => r.id)) ? "dim" : ""}`}
                onClick={(e) => {
                  e.stopPropagation();
                  setPicked(h.key);
                }}
                onKeyDown={pickKeys(h.key)}
                tabIndex={0}
                role="button"
                aria-label={`${h.node.title}, ${plural(h.node.count, "item")}`}
              >
                <circle cx={h.x} cy={h.y} r={h.r} />
                <text x={h.x} y={h.y - h.r - 8} textAnchor="middle">
                  {short(h.node.title, 44)} · {plural(h.node.count, "item")}
                </text>
              </g>
            ))}
            {graph.links.map((l) => (
              <g key={l.key} className={`cluster-link ${l.kind}`}>
                <line x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2}>
                  <title>{`${l.label}: ${l.detail}`}</title>
                </line>
                <text x={(l.x1 + l.x2) / 2} y={(l.y1 + l.y2) / 2 + 4} textAnchor="middle">
                  {short(l.label, 24)}
                </text>
              </g>
            ))}
            {graph.clusters.map((c) => (
              <g
                key={c.key}
                className={`cluster ${c.kind} ${picked === c.key ? "on" : ""} ${dim(c.items.map((i) => i.row.id)) ? "dim" : ""}`}
                onClick={(e) => {
                  e.stopPropagation();
                  setPicked(c.key);
                }}
                onKeyDown={pickKeys(c.key)}
                tabIndex={0}
                role="button"
                aria-label={`${c.title}, ${c.sub}`}
              >
                <title>{c.title}</title>
                <circle className="bubble" cx={c.x} cy={c.y} r={c.r} />
                <text className="name" x={c.x} y={c.y + c.r + 15} textAnchor="middle">
                  {short(c.title, 32)}
                </text>
                <text className="sub" x={c.x} y={c.y + c.r + 29} textAnchor="middle">
                  {short(shortCase(c.sub), 30)}
                </text>
              </g>
            ))}
            {graph.inner.map((l) => (
              <line key={l.key} className="cluster-inner" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} />
            ))}
            <defs>
              <clipPath id="cluster-dot" clipPathUnits="objectBoundingBox">
                <circle cx={0.5} cy={0.5} r={0.5} />
              </clipPath>
            </defs>
            {graph.clusters.flatMap((c) =>
              c.items.map(({ row, x, y }) => (
                <g
                  key={row.id}
                  className={`cluster-item ${row.signs ? "warn" : ""} ${searching && !matches.has(row.id) ? "dim" : ""} ${searching && matches.has(row.id) ? "hit" : ""}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    open(row.id);
                  }}
                  onKeyDown={(e) => e.key === "Enter" && open(row.id)}
                  tabIndex={0}
                  role="link"
                  aria-label={row.title}
                >
                  <title>{`${row.title}${row.signs ? ` · ${plural(row.signs, "warning sign")}` : ""}`}</title>
                  <circle cx={x} cy={y} r={ITEM_R} />
                  {row.picture && (
                    <image
                      href={pictureUrl(row.id)}
                      x={x - ITEM_R}
                      y={y - ITEM_R}
                      width={ITEM_R * 2}
                      height={ITEM_R * 2}
                      preserveAspectRatio="xMidYMid slice"
                      clipPath="url(#cluster-dot)"
                    />
                  )}
                </g>
              )),
            )}
          </svg>
        </div>
        <Details selected={selected} />
      </div>
    </div>
  );
}
