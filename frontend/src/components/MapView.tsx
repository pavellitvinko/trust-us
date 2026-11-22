import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { LibraryData, Row } from "../api";
import { Picture, plural } from "./bits";
import { COLUMN_HEADS, layoutGraph, type GraphCard } from "./mapGraph";
import { buildTree, filterTree, groupKeys, shortCase, type TreeNode } from "./mapTree";
import { VideoThumbnail } from "./video";

const ZOOMS = [0.6, 0.75, 0.9, 1, 1.15];

function itemDetails(row: Row, note?: GraphCard["note"]) {
  return [
    row.title,
    row.duplicateOf && `Same page also in ${shortCase(row.duplicateOf)}`,
    row.links === 0 && row.possible > 0 && plural(row.possible, "possible link"),
    row.links === 0 && !row.comparable && "Too little to compare",
    note && `${note.text}. ${note.detail}`,
  ]
    .filter(Boolean)
    .join("\n");
}

function ItemCard({ row, note }: { row: Row; note?: GraphCard["note"] }) {
  return (
    <Link className={`graph-item ${note ? "linked" : ""}`} to={`/item/${encodeURIComponent(row.id)}`} title={itemDetails(row, note)}>
      {row.type === "Video" ? (
        <VideoThumbnail id={row.id} width={56} height={40} />
      ) : (
        <Picture id={row.id} has={row.picture} type={row.type} width={56} height={40} />
      )}
      <span className="graph-text">
        <span className="t">{row.title}</span>
        <span className="small">
          {row.signs > 0 ? <span className="danger">{plural(row.signs, "warning sign")}</span> : row.host || row.type}
          {row.duplicateOf && <span className="graph-copy"> · copy</span>}
          {row.links === 0 && row.possible > 0 && ` · ${row.possible} possible`}
        </span>
      </span>
    </Link>
  );
}

function GroupCard({ node, open, toggle }: { node: TreeNode; open: boolean; toggle: () => void }) {
  const why = node.reasons.length ? `Linked by: ${node.reasons.join(", ")}` : node.note;
  return (
    <div className={`graph-group ${node.kind}`} title={[node.note, why].filter(Boolean).join("\n")}>
      <button aria-expanded={open} onClick={toggle}>
        <span className="caret" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        <span className="graph-title">{node.title}</span>
      </button>
      <div className="graph-why small">{why}</div>
      <div className="graph-foot small">
        <span>
          {plural(node.count, "item")}
          {node.copies > 0 && ` + ${node.copies} ${node.copies === 1 ? "copy" : "copies"}`}
        </span>
        {node.name && <span className="muted graph-name">{node.name}</span>}
        {node.href && (
          <Link to={node.href} aria-label={`Open ${node.title} ${node.name}`.trim()}>
            Open
          </Link>
        )}
      </div>
    </div>
  );
}

/** The Library as a graph, read left to right: template, campaign, case, item. */
export default function MapView({ rows, map }: { rows: Row[]; map: LibraryData["map"] }) {
  const [query, setQuery] = useState("");
  const [zoom, setZoom] = useState(3);
  // Stored as a difference from the default, so groups that arrive later open normally.
  const [flipped, setFlipped] = useState<Set<string>>(new Set());
  const { tree, crossLinks } = useMemo(() => buildTree(rows, map), [rows, map]);
  const shown = useMemo(() => filterTree(tree, query, map.repeats), [tree, query, map.repeats]);
  const searching = query.trim().length > 0;
  const keys = useMemo(() => groupKeys(tree), [tree]);
  const closedByDefault = (key: string) => key === "unlinked";
  const closed = (key: string) => !searching && closedByDefault(key) !== flipped.has(key);
  const toggle = (key: string) => {
    if (searching) return; // a search shows every match, so its branches stay open
    setFlipped((now) => {
      const next = new Set(now);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  };
  const setAll = (open: boolean) => setFlipped(new Set(keys.filter((key) => closedByDefault(key) === open)));
  const found = shown.reduce((sum, n) => sum + n.count, 0);
  if (!rows.length) return <div className="empty card">No items to show yet.</div>;

  const graph = layoutGraph(shown, closed, crossLinks);
  const scale = ZOOMS[zoom];
  return (
    <div className="graph-page">
      <div className="tree-tools">
        <input
          className="input"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name, site or case"
          aria-label="Search the map"
        />
        <button className="btn" onClick={() => setAll(true)} disabled={searching}>
          Expand all
        </button>
        <button className="btn" onClick={() => setAll(false)} disabled={searching}>
          Collapse all
        </button>
        <span className="graph-zoom">
          <button className="btn" aria-label="Zoom out" disabled={zoom === 0} onClick={() => setZoom(zoom - 1)}>
            −
          </button>
          <span className="small">{Math.round(scale * 100)}%</span>
          <button className="btn" aria-label="Zoom in" disabled={zoom === ZOOMS.length - 1} onClick={() => setZoom(zoom + 1)}>
            +
          </button>
        </span>
      </div>
      <p className="small tree-help">
        Read it left to right: a <b>template</b> can be used by several <b>campaigns</b>, a campaign is likely run by the
        same people, and a <b>case</b> is what was added together. A dashed line links items across groups.
        <span aria-live="polite">{searching ? ` ${plural(found, "item")} found.` : ""}</span>
      </p>
      {shown.length ? (
        <div className="graph-scroll" role="region" aria-label="Graph of linked items" tabIndex={0}>
          <div style={{ width: graph.width * scale, height: graph.height * scale }}>
            <div
              className="graph-canvas"
              style={{ width: graph.width, height: graph.height, transform: `scale(${scale})` }}
            >
              {COLUMN_HEADS.map((c) => (
                <div key={c.label} className="graph-column" style={{ left: c.x }}>
                  {c.label}
                </div>
              ))}
              <svg width={graph.width} height={graph.height} aria-hidden="true">
                {graph.lines.map((l) => (
                  <path key={l.key} d={l.d} className={`graph-line ${l.kind}`} />
                ))}
                {graph.arcs.map((a) => (
                  <path key={a.key} d={a.d} className="graph-arc">
                    <title>{a.label}</title>
                  </path>
                ))}
              </svg>
              {graph.cards.map((c) => (
                <div key={c.key} className="graph-card" style={{ left: c.x, top: c.y, width: c.width, height: c.height }}>
                  {c.row ? (
                    <ItemCard row={c.row} note={c.note} />
                  ) : c.node!.kind === "case" ? (
                    <div className="graph-case" title={c.node!.title}>
                      {c.node!.title}
                    </div>
                  ) : (
                    <GroupCard node={c.node!} open={!closed(c.key)} toggle={() => toggle(c.key)} />
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="empty card">Nothing matches "{query.trim()}".</div>
      )}
    </div>
  );
}
