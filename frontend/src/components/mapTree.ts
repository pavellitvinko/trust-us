// Turns the Library's rows and links into one tree: template > campaign > case > item.
// Every row appears exactly once.

import type { MapEdge, Row } from "../api";
import { campaignUrl, kitUrl } from "../hooks";

export type TreeKind = "kit" | "campaign" | "loose" | "links" | "unlinked" | "case";

export interface TreeNode {
  key: string;
  kind: TreeKind;
  title: string;
  /** The group's name in the table, so the two views can be matched up. */
  name: string;
  note: string;
  href?: string;
  /** Why these items belong together, from the links between them. */
  reasons: string[];
  children: TreeNode[];
  rows: Row[];
  count: number;
}

const compare = new Intl.Collator("pl", { numeric: true, sensitivity: "base" }).compare;

/** A case named by a long run id reads better shortened. */
export const shortCase = (name: string) => (/^[0-9a-f]{32}$/.test(name) ? `Case ${name.slice(0, 8)}` : name);

/** Where a group was seen: its sites when known, otherwise its cases. */
function place(rows: Row[]) {
  const hosts = [...new Set(rows.map((r) => r.host).filter(Boolean))].sort(compare);
  if (hosts.length) return `on ${hosts[0]}${hosts.length > 1 ? ` and ${hosts.length - 1} more` : ""}`;
  const cases = [...new Set(rows.map((r) => shortCase(r.case).replace(/^Case /, "")))].sort(compare);
  if (cases.length === 1) return `in Case ${cases[0]}`;
  if (cases.length > 4) return `in ${cases.length} cases`;
  return `in Cases ${cases.slice(0, -1).join(", ")} and ${cases[cases.length - 1]}`;
}

function reasons(rows: Row[], edges: MapEdge[], kind: MapEdge["kind"]) {
  const ids = new Set(rows.map((r) => r.id));
  const labels = edges.filter((e) => e.kind === kind && ids.has(e.a) && ids.has(e.b)).map((e) => e.label);
  return [...new Set(labels)].sort(compare);
}

function caseNodes(parent: string, rows: Row[]): TreeNode[] {
  const cases = new Map<string, Row[]>();
  for (const r of rows) cases.set(r.caseId, [...(cases.get(r.caseId) ?? []), r]);
  return [...cases.entries()]
    .map(([id, members]) => ({
      key: `${parent}/case:${id}`,
      kind: "case" as const,
      title: shortCase(members[0].case),
      name: "",
      note: "",
      reasons: [],
      children: [],
      rows: members,
      count: members.length,
    }))
    .sort((a, b) => compare(a.title, b.title));
}

function campaignNode(id: string, rows: Row[], edges: MapEdge[]): TreeNode {
  const key = `campaign:${id}`;
  return {
    key,
    kind: "campaign",
    title: `Campaign ${place(rows)}`,
    name: rows[0].campaign,
    note: "Likely run by the same people.",
    href: campaignUrl(id),
    reasons: reasons(rows, edges, "campaign"),
    children: caseNodes(key, rows),
    rows: [],
    count: rows.length,
  };
}

const biggestFirst = (a: TreeNode, b: TreeNode) => b.count - a.count || compare(a.title, b.title);

/** Items linked to each other without sharing a campaign or template. */
function linkedGroups(rows: Row[], edges: MapEdge[]): Row[][] {
  const parent = new Map(rows.map((r) => [r.id, r.id]));
  const find = (x: string): string => {
    while (parent.get(x) !== x) x = parent.get(x)!;
    return x;
  };
  for (const e of edges) {
    if (e.kind !== "possible" && parent.has(e.a) && parent.has(e.b)) parent.set(find(e.a), find(e.b));
  }
  const groups = new Map<string, Row[]>();
  for (const r of rows) groups.set(find(r.id), [...(groups.get(find(r.id)) ?? []), r]);
  return [...groups.values()];
}

export function buildTree(rows: Row[], edges: MapEdge[]): TreeNode[] {
  const campaigns = new Map<string, Row[]>();
  for (const r of rows) {
    if (r.campaignId) campaigns.set(r.campaignId, [...(campaigns.get(r.campaignId) ?? []), r]);
  }

  // A campaign sits under the template most of its items use, so it is never shown twice.
  const kitCampaigns = new Map<string, string[]>();
  const standalone: string[] = [];
  for (const [id, members] of campaigns) {
    const votes = new Map<string, number>();
    for (const r of members) if (r.kitId) votes.set(r.kitId, (votes.get(r.kitId) ?? 0) + 1);
    const kit = [...votes.entries()].sort((a, b) => b[1] - a[1] || compare(a[0], b[0]))[0]?.[0];
    if (kit) kitCampaigns.set(kit, [...(kitCampaigns.get(kit) ?? []), id]);
    else standalone.push(id);
  }

  const kitRows = new Map<string, Row[]>();
  for (const r of rows) if (r.kitId) kitRows.set(r.kitId, [...(kitRows.get(r.kitId) ?? []), r]);
  for (const id of kitCampaigns.keys()) if (!kitRows.has(id)) kitRows.set(id, []);

  const kits = [...kitRows.entries()].map(([id, members]): TreeNode => {
    const key = `kit:${id}`;
    const own = (kitCampaigns.get(id) ?? []).map((c) => campaignNode(c, campaigns.get(c)!, edges)).sort(biggestFirst);
    const loose = members.filter((r) => !r.campaignId);
    const children = [...own];
    if (loose.length) {
      const looseKey = `${key}/loose`;
      children.push({
        key: looseKey,
        kind: "loose",
        title: "Not tied to a campaign",
        name: "",
        note: "Same template, but nothing shows who runs it.",
        reasons: [],
        children: caseNodes(looseKey, loose),
        rows: [],
        count: loose.length,
      });
    }
    const all = [...own.flatMap((c) => campaigns.get(c.key.slice("campaign:".length))!), ...loose];
    return {
      key,
      kind: "kit",
      title: `Template used ${place(all)}`,
      name: all.find((r) => r.kit)?.kit ?? "",
      note: "Templates are sold and reused, so these may be different people.",
      href: kitUrl(id),
      reasons: reasons(all, edges, "kit"),
      children,
      rows: [],
      count: all.length,
    };
  });

  const placed = new Set(rows.filter((r) => r.campaignId || r.kitId).map((r) => r.id));
  const rest = rows.filter((r) => !placed.has(r.id));
  const linked = linkedGroups(rest, edges).filter((g) => g.length > 1);
  const inLinked = new Set(linked.flat().map((r) => r.id));
  const links = linked.map((members): TreeNode => {
    const key = `links:${members.map((r) => r.id).sort()[0]}`;
    const ids = new Set(members.map((r) => r.id));
    const between = edges.filter((e) => e.kind !== "possible" && ids.has(e.a) && ids.has(e.b));
    const onlyCopies = between.every((e) => e.kind === "duplicate");
    return {
      key,
      kind: "links",
      title: onlyCopies ? `Same page added ${members.length} times ${place(members)}` : `Linked items ${place(members)}`,
      name: "",
      note: onlyCopies ? "Copies of one page, not separate findings." : "",
      reasons: onlyCopies ? [] : [...new Set(between.map((e) => e.label))].sort(compare),
      children: caseNodes(key, members),
      rows: [],
      count: members.length,
    };
  });

  const tree = [
    // A template whose only items sit in a campaign shown under another template has nothing left.
    ...kits.filter((k) => k.count > 0).sort(biggestFirst),
    ...standalone.map((c) => campaignNode(c, campaigns.get(c)!, edges)).sort(biggestFirst),
    ...links.sort(biggestFirst),
  ];
  const unlinked = rest.filter((r) => !inLinked.has(r.id));
  if (unlinked.length) {
    tree.push({
      key: "unlinked",
      kind: "unlinked",
      title: "Not linked to anything yet",
      name: "",
      note: "No shared evidence so far. This doesn't prove they are unrelated.",
      reasons: [],
      children: caseNodes("unlinked", unlinked),
      rows: [],
      count: unlinked.length,
    });
  }
  return tree;
}

/** Lower case with Polish letters flattened, so "lodz" finds "Łódź". */
const plain = (text: string) =>
  text.toLocaleLowerCase("pl").replace(/ł/g, "l").normalize("NFD").replace(/\p{M}/gu, "");

const rowText = (r: Row) => plain([r.title, r.host, r.case, shortCase(r.case), r.type, r.campaign, r.kit].join(" "));

/** Only the branches holding a matching item; a matching group keeps all its items. */
export function filterTree(nodes: TreeNode[], query: string): TreeNode[] {
  const q = plain(query.trim());
  if (!q) return nodes;
  const prune = (node: TreeNode): TreeNode | null => {
    if (plain(`${node.title} ${node.name}`).includes(q)) return node;
    const rows = node.rows.filter((r) => rowText(r).includes(q));
    const children = node.children.map(prune).filter((c): c is TreeNode => c !== null);
    if (!rows.length && !children.length) return null;
    return { ...node, rows, children, count: rows.length + children.reduce((sum, c) => sum + c.count, 0) };
  };
  return nodes.map(prune).filter((n): n is TreeNode => n !== null);
}

export function groupKeys(nodes: TreeNode[]): string[] {
  return nodes.flatMap((n) => (n.kind === "case" ? [] : [n.key, ...groupKeys(n.children)]));
}
