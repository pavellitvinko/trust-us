import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { LibraryData, Row } from "../api";
import { Picture, plural } from "./bits";
import { buildTree, filterTree, groupKeys, shortCase, type TreeNode } from "./mapTree";
import { VideoThumbnail } from "./video";

function ItemRow({ row, linkNote }: { row: Row; linkNote?: string }) {
  return (
    <li className="tree-item">
      <Link to={`/item/${encodeURIComponent(row.id)}`}>
        {row.type === "Video" ? (
          <VideoThumbnail id={row.id} width={72} height={46} />
        ) : (
          <Picture id={row.id} has={row.picture} type={row.type} width={72} height={46} />
        )}
        <span className="tree-item-text">
          <span className="t">{row.title}</span>
          <span className="small">
            {row.type}
            {row.host && ` · ${row.host}`}
            {row.signs > 0 && <span className="danger"> · {plural(row.signs, "warning sign")}</span>}
            {row.duplicateOf && <span className="tree-copy"> · same page also in {shortCase(row.duplicateOf)}</span>}
            {row.links === 0 && row.possible > 0 && <span> · {plural(row.possible, "possible link")}</span>}
            {row.links === 0 && !row.comparable && <span> · too little to compare</span>}
            {linkNote && <span className="tree-link"> · {linkNote}</span>}
          </span>
        </span>
      </Link>
    </li>
  );
}

function Branch({
  node,
  closed,
  toggle,
}: {
  node: TreeNode;
  closed: (key: string) => boolean;
  toggle: (key: string) => unknown;
}) {
  if (node.kind === "case") {
    return (
      <li className="tree-case">
        <div className="tree-case-name">{node.title}</div>
        <ul>
          {node.rows.map((r) => (
            <ItemRow key={r.id} row={r} linkNote={node.linkNotes?.[r.id]} />
          ))}
        </ul>
      </li>
    );
  }
  const open = !closed(node.key);
  return (
    <li className={`tree-group ${node.kind}`}>
      <div className="tree-head">
        <button aria-expanded={open} onClick={() => toggle(node.key)}>
          <span className="caret" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
          <span className="tree-title">{node.title}</span>
          <span className="tree-count">
            {plural(node.count, "item")}
            {node.copies > 0 && ` + ${node.copies} ${node.copies === 1 ? "copy" : "copies"}`}
          </span>
        </button>
        {node.href && (
          <Link className="tree-open" to={node.href} aria-label={`Open ${node.title}`}>
            Open
          </Link>
        )}
      </div>
      {open && (
        <>
          <div className="tree-why small">
            {node.note}
            {node.reasons.length > 0 && <b> Linked by: {node.reasons.join(", ")}.</b>}
            {node.name && <span className="muted"> {node.name}</span>}
          </div>
          <ul>
            {node.children.map((c) => (
              <Branch key={c.key} node={c} closed={closed} toggle={toggle} />
            ))}
          </ul>
        </>
      )}
    </li>
  );
}

/** The Library as a tree: template, campaign, case, item. */
export default function MapView({ rows, map }: { rows: Row[]; map: LibraryData["map"] }) {
  const [query, setQuery] = useState("");
  // Stored as a difference from the default, so groups that arrive later open normally.
  const [flipped, setFlipped] = useState<Set<string>>(new Set());
  const tree = useMemo(() => buildTree(rows, map), [rows, map]);
  const shown = useMemo(() => filterTree(tree, query, map.repeats), [tree, query, map.repeats]);
  const searching = query.trim().length > 0;
  const keys = useMemo(() => groupKeys(tree), [tree]);
  const closedByDefault = (key: string) => key === "unlinked";
  const closed = (key: string) => !searching && closedByDefault(key) !== flipped.has(key);
  const toggle = (key: string) =>
    !searching && // a search shows every match, so its branches stay open
    setFlipped((now) => {
      const next = new Set(now);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  const setAll = (open: boolean) =>
    setFlipped(new Set(keys.filter((key) => closedByDefault(key) === open)));
  const found = shown.reduce((sum, n) => sum + n.count, 0);
  if (!rows.length) return <div className="empty card">No items to show yet.</div>;

  return (
    <div className="tree-page">
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
      </div>
      <p className="small tree-help">
        Read it from the top: a <b>template</b> can be used by several <b>campaigns</b>, a campaign is likely run by the
        same people, and a <b>case</b> is what was added together.
        <span aria-live="polite">{searching ? ` ${plural(found, "item")} found.` : ""}</span>
      </p>
      {shown.length ? (
        <ul className="tree">
          {shown.map((n) => (
            <Branch key={n.key} node={n} closed={closed} toggle={toggle} />
          ))}
        </ul>
      ) : (
        <div className="empty card">Nothing matches "{query.trim()}".</div>
      )}
    </div>
  );
}
