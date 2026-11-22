// Places the map's tree as a left-to-right graph: template, campaign, case, item.

import type { Row } from "../api";
import type { CrossLink, TreeNode } from "./mapTree";

const COLUMNS = { group0: 0, group1: 280, case: 570, item: 720 };
const WIDTH = { kit: 240, group: 250, case: 110, item: 290 };
const HEIGHT = { group: 86, case: 34, item: 58 };
const GAP = 10;
const TREE_GAP = 26;
const TOP = 40;
const ARC_ROOM = 150;

export const COLUMN_HEADS = [
  { x: COLUMNS.group0, label: "Template" },
  { x: COLUMNS.group1, label: "Campaign or group" },
  { x: COLUMNS.case, label: "Case" },
  { x: COLUMNS.item, label: "Item" },
];

export interface GraphCard {
  key: string;
  x: number;
  y: number;
  width: number;
  height: number;
  node?: TreeNode;
  row?: Row;
  note?: { text: string; detail: string };
}

export interface GraphLine {
  key: string;
  d: string;
  kind: string;
}

export interface GraphArc extends GraphLine {
  label: string;
}

export interface Graph {
  cards: GraphCard[];
  lines: GraphLine[];
  arcs: GraphArc[];
  width: number;
  height: number;
}

const allRows = (node: TreeNode): Row[] => [...node.rows, ...node.children.flatMap(allRows)];

export function layoutGraph(tree: TreeNode[], closed: (key: string) => boolean, crossLinks: CrossLink[]): Graph {
  const cards: GraphCard[] = [];
  const lines: GraphLine[] = [];
  const owner = new Map<string, GraphCard>(); // item id -> the card showing it

  const isOpen = (node: TreeNode) => node.kind === "case" || !closed(node.key);
  const ownHeight = (node: TreeNode) => (node.kind === "case" ? HEIGHT.case : HEIGHT.group);
  const stack = (sizes: number[]) => sizes.reduce((sum, s) => sum + s, 0) + GAP * Math.max(0, sizes.length - 1);
  const contentHeight = (node: TreeNode): number => {
    if (!isOpen(node)) return 0;
    return node.kind === "case" ? stack(node.rows.map(() => HEIGHT.item)) : stack(node.children.map(fullHeight));
  };
  const fullHeight = (node: TreeNode) => Math.max(ownHeight(node), contentHeight(node));

  const wire = (from: GraphCard, to: GraphCard, kind: string) => {
    const x1 = from.x + from.width;
    const y1 = from.y + from.height / 2;
    const y2 = to.y + to.height / 2;
    const middle = (x1 + to.x) / 2;
    lines.push({ key: `${from.key}>${to.key}`, kind, d: `M${x1},${y1} C${middle},${y1} ${middle},${y2} ${to.x},${y2}` });
  };

  /** Lays a node out inside the band starting at `top` and returns its card. */
  const place = (node: TreeNode, top: number): GraphCard => {
    const band = fullHeight(node);
    const height = ownHeight(node);
    const x = node.kind === "kit" ? COLUMNS.group0 : node.kind === "case" ? COLUMNS.case : COLUMNS.group1;
    const width = node.kind === "kit" ? WIDTH.kit : node.kind === "case" ? WIDTH.case : WIDTH.group;
    const card: GraphCard = { key: node.key, x, y: top + (band - height) / 2, width, height, node };
    cards.push(card);

    if (!isOpen(node)) {
      for (const r of allRows(node)) owner.set(r.id, card);
      return card;
    }
    let y = top + (band - contentHeight(node)) / 2;
    const placed: GraphCard[] = [];
    if (node.kind === "case") {
      for (const row of node.rows) {
        const item: GraphCard = {
          key: `item:${row.id}`,
          x: COLUMNS.item,
          y,
          width: WIDTH.item,
          height: HEIGHT.item,
          row,
          note: node.linkNotes?.[row.id],
        };
        cards.push(item);
        owner.set(row.id, item);
        placed.push(item);
        y += HEIGHT.item + GAP;
      }
    } else {
      for (const child of node.children) {
        placed.push(place(child, y));
        y += fullHeight(child) + GAP;
      }
    }
    if (placed.length) {
      const first = placed[0].y + placed[0].height / 2;
      const last = placed[placed.length - 1].y + placed[placed.length - 1].height / 2;
      card.y = Math.min(Math.max((first + last) / 2 - height / 2, top), top + band - height);
    }
    for (const child of placed) wire(card, child, child.node?.kind ?? node.kind);
    return card;
  };

  let top = TOP;
  for (const node of tree) {
    place(node, top);
    top += fullHeight(node) + TREE_GAP;
  }

  // Links between groups, bowed out to the right of the cards so they never cross the text.
  const seen = new Set<string>();
  const arcs: GraphArc[] = [];
  for (const link of crossLinks) {
    const a = owner.get(link.a);
    const b = owner.get(link.b);
    if (!a || !b || a === b) continue;
    const key = [a.key, b.key].sort().join("|");
    if (seen.has(key)) continue;
    seen.add(key);
    const x1 = a.x + a.width;
    const x2 = b.x + b.width;
    const y1 = a.y + a.height / 2;
    const y2 = b.y + b.height / 2;
    const bow = Math.max(x1, x2) + Math.min(ARC_ROOM - 16, 34 + Math.abs(y2 - y1) / 5);
    arcs.push({
      key,
      kind: "cross",
      label: `${link.labels.join(", ")}: ${link.detail}`,
      d: `M${x1},${y1} C${bow},${y1} ${bow},${y2} ${x2},${y2}`,
    });
  }

  return {
    cards,
    lines,
    arcs,
    width: COLUMNS.item + WIDTH.item + ARC_ROOM,
    height: Math.max(top, TOP + HEIGHT.group),
  };
}
