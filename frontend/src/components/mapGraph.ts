// Places the Library as a graph of clusters: bubbles of items, templates around the campaigns
// that use them, and lines between clusters that share evidence. The layout is deterministic,
// so the picture doesn't move when the data refreshes.

import type { MapEdge, Row } from "../api";
import type { CrossLink, TreeNode } from "./mapTree";

const WIDTH = 1180;
const PAD = 24;
const ITEM_SPACING = 25;
const CLUSTER_MARGIN = 24;
const LABEL_ROOM = 38;
const HULL_LABEL = 26;

export type ClusterKind = "campaign" | "copies" | "single" | "none";
type Note = { text: string; detail: string };

export interface GraphItem {
  row: Row;
  x: number;
  y: number;
  note?: Note;
}

export interface GraphCluster {
  key: string;
  kind: ClusterKind;
  title: string;
  sub: string;
  node?: TreeNode;
  x: number;
  y: number;
  r: number;
  items: GraphItem[];
}

export interface GraphHull {
  key: string;
  node: TreeNode;
  x: number;
  y: number;
  r: number;
}

export interface GraphLink {
  key: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  kind: MapEdge["kind"] | "journey";
  label: string;
  detail: string;
}

export interface ClusterGraph {
  hulls: GraphHull[];
  clusters: GraphCluster[];
  links: GraphLink[];
  inner: GraphLink[];
  width: number;
  height: number;
}

interface Block {
  reach: number;
  top: number;
  place: (x: number, y: number) => void;
}

const allRows = (node: TreeNode): Row[] => [...node.rows, ...node.children.flatMap(allRows)];

function allNotes(node: TreeNode): Record<string, Note> {
  return Object.assign({}, node.linkNotes, ...node.children.map(allNotes));
}

const itemsText = (node: TreeNode) =>
  `${node.count} ${node.count === 1 ? "item" : "items"}${
    node.copies ? ` + ${node.copies} ${node.copies === 1 ? "copy" : "copies"}` : ""
  }`;

function makeCluster(
  key: string,
  kind: ClusterKind,
  title: string,
  sub: string,
  rows: Row[],
  notes: Record<string, Note>,
  node?: TreeNode,
): GraphCluster {
  // A sunflower spiral spreads any number of items evenly inside a circle.
  const golden = Math.PI * (3 - Math.sqrt(5));
  const items = rows.map((row, i) => {
    const distance = rows.length === 1 ? 0 : ITEM_SPACING * Math.sqrt(i + 0.5);
    return { row, x: distance * Math.cos(i * golden), y: distance * Math.sin(i * golden), note: notes[row.id] };
  });
  const r = rows.length === 1 ? 25 : ITEM_SPACING * Math.sqrt(rows.length) + 22;
  return { key, kind, title, sub, node, x: 0, y: 0, r, items };
}

function moveCluster(cluster: GraphCluster, x: number, y: number) {
  const dx = x - cluster.x;
  const dy = y - cluster.y;
  cluster.x = x;
  cluster.y = y;
  for (const item of cluster.items) {
    item.x += dx;
    item.y += dy;
  }
}

const reachOf = (cluster: GraphCluster) => cluster.r + LABEL_ROOM;

function clusterBlock(cluster: GraphCluster): Block {
  return {
    reach: reachOf(cluster),
    top: 0,
    place: (x, y) => moveCluster(cluster, x, y),
  };
}

function hullBlock(node: TreeNode, members: GraphCluster[], hulls: GraphHull[]): Block {
  const ordered = [...members].sort((a, b) => b.r - a.r);
  const widest = Math.max(...ordered.map(reachOf));
  const ring = ordered.length > 1 ? (widest + CLUSTER_MARGIN / 2) / Math.sin(Math.PI / ordered.length) : 0;
  const r = ring + widest + CLUSTER_MARGIN / 2;
  const hull: GraphHull = { key: node.key, node, x: 0, y: 0, r };
  hulls.push(hull);
  return {
    reach: r,
    top: HULL_LABEL,
    place: (x, y) => {
      hull.x = x;
      hull.y = y;
      ordered.forEach((c, i) => {
        const angle = -Math.PI / 2 + (2 * Math.PI * i) / ordered.length;
        // The label sits under each bubble, so the bubble rides slightly high in its slot.
        moveCluster(c, x + ring * Math.cos(angle), y + ring * Math.sin(angle) - LABEL_ROOM / 3);
      });
    },
  };
}

function singles(rows: Row[], notes: Record<string, Note>): GraphCluster[] {
  return rows.map((r) => makeCluster(`single:${r.id}`, "single", r.title, r.case, [r], notes));
}

const STRENGTH: GraphLink["kind"][] = ["campaign", "duplicate", "journey", "kit", "possible"];

export function layoutClusters(tree: TreeNode[], edges: MapEdge[], crossLinks: CrossLink[]): ClusterGraph {
  const hulls: GraphHull[] = [];
  const clusters: GraphCluster[] = [];
  const blocks: Block[] = [];
  const add = (cluster: GraphCluster) => {
    clusters.push(cluster);
    return cluster;
  };
  const clusterFor = (node: TreeNode, notes: Record<string, Note>) =>
    add(
      makeCluster(
        node.key,
        node.kind === "campaign" ? "campaign" : node.kind === "unlinked" ? "none" : "copies",
        node.title,
        itemsText(node),
        allRows(node),
        notes,
        node,
      ),
    );

  for (const node of tree) {
    const notes = allNotes(node);
    if (node.kind === "kit") {
      const members = node.children.flatMap((child) =>
        child.kind === "loose" ? singles(allRows(child), notes).map(add) : [clusterFor(child, notes)],
      );
      if (members.length) blocks.push(hullBlock(node, members, hulls));
    } else if (node.key === "one-to-one") {
      for (const single of singles(allRows(node), notes)) blocks.push(clusterBlock(add(single)));
    } else {
      blocks.push(clusterBlock(clusterFor(node, notes)));
    }
  }

  // Shelf packing: biggest first keeps the eye on the main clusters.
  let x = PAD;
  let rowTop = PAD;
  let rowHeight = 0;
  for (const block of [...blocks].sort((a, b) => b.reach - a.reach)) {
    const size = block.reach * 2;
    if (x > PAD && x + size > WIDTH - PAD) {
      x = PAD;
      rowTop += rowHeight + CLUSTER_MARGIN;
      rowHeight = 0;
    }
    block.place(x + block.reach, rowTop + block.top + block.reach);
    x += size + CLUSTER_MARGIN;
    rowHeight = Math.max(rowHeight, size + block.top);
  }

  const owner = new Map(clusters.flatMap((c) => c.items.map((i) => [i.row.id, c] as const)));
  const itemAt = new Map(clusters.flatMap((c) => c.items.map((i) => [i.row.id, i] as const)));
  const joined = new Map<string, { a: GraphCluster; b: GraphCluster; kinds: Set<GraphLink["kind"]>; labels: Set<string>; details: Set<string> }>();
  const join = (idA: string, idB: string, kind: GraphLink["kind"], label: string, detail: string) => {
    const first = owner.get(idA);
    const second = owner.get(idB);
    if (!first || !second || first === second) return;
    const [a, b] = first.key < second.key ? [first, second] : [second, first];
    const key = `${a.key}|${b.key}`;
    const link = joined.get(key) ?? { a, b, kinds: new Set(), labels: new Set(), details: new Set() };
    link.kinds.add(kind);
    link.labels.add(label);
    link.details.add(detail);
    joined.set(key, link);
  };

  const inner: GraphLink[] = [];
  for (const e of edges) {
    if (e.kind === "possible") continue;
    const label = e.kind === "duplicate" ? "same page" : e.label;
    join(e.a, e.b, e.kind, label, e.reason);
    const a = itemAt.get(e.a);
    const b = itemAt.get(e.b);
    if (a && b && owner.get(e.a) === owner.get(e.b) && e.kind !== "kit") {
      inner.push({ key: `${e.a}|${e.b}`, x1: a.x, y1: a.y, x2: b.x, y2: b.y, kind: e.kind, label, detail: e.reason });
    }
  }
  for (const link of crossLinks) {
    if (link.labels.includes("same visitor path")) {
      join(link.a, link.b, "journey", "same visitor path", "On the same visitor path");
    }
  }

  const links = [...joined.entries()].map(([key, l]): GraphLink => {
    const dx = l.b.x - l.a.x;
    const dy = l.b.y - l.a.y;
    const length = Math.hypot(dx, dy) || 1;
    const labels = [...l.labels].sort();
    return {
      key,
      x1: l.a.x + (dx / length) * l.a.r,
      y1: l.a.y + (dy / length) * l.a.r,
      x2: l.b.x - (dx / length) * l.b.r,
      y2: l.b.y - (dy / length) * l.b.r,
      kind: STRENGTH.find((k) => l.kinds.has(k)) ?? "kit",
      label: labels.length > 1 ? `${labels[0]} +${labels.length - 1}` : labels[0],
      detail: [...l.details].join("; "),
    };
  });

  return { hulls, clusters, links, inner, width: WIDTH, height: rowTop + rowHeight + PAD };
}
