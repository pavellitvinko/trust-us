// Turns the Library's rows and links into one tree: template > campaign > case > item.
// Every row appears exactly once. Grouping follows the backend: a link is never treated as
// transitive, because the backend can refuse to merge A-B-C.

import type { LibraryData, MapEdge, Row } from "../api";
import { campaignUrl, kitUrl } from "../hooks";

export type TreeKind = "kit" | "campaign" | "loose" | "links" | "unlinked" | "case";
type MapData = LibraryData["map"];

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
  /** Items, with a page the backend marks as repeated counted once. */
  count: number;
  copies: number;
  /** On a case: what each item is linked to outside its own group. */
  linkNotes?: Record<string, { text: string; detail: string }>;
}

/** A firm link between items that sit in different groups. */
export interface CrossLink {
  a: string;
  b: string;
  labels: string[];
  detail: string;
}

const compare = new Intl.Collator("pl", { numeric: true, sensitivity: "base" }).compare;

/** A case named by a long run id reads better shortened. */
export const shortCase = (name: string) => (/^[0-9a-f]{32}$/.test(name) ? `Case ${name.slice(0, 8)}` : name);

const linkLabel = (e: MapEdge) => (e.kind === "duplicate" ? "same page" : e.label);

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
  const labels = edges.filter((e) => e.kind === kind && ids.has(e.a) && ids.has(e.b)).map(linkLabel);
  return [...new Set(labels)].sort(compare);
}

function caseNodes(parent: string, rows: Row[]): TreeNode[] {
  const cases = new Map<string, Row[]>();
  for (const r of rows) push(cases, r.caseId, r);
  return [...cases.entries()]
    .map(([id, members]): TreeNode => ({
      key: `${parent}/case:${id}`,
      kind: "case",
      title: shortCase(members[0].case),
      name: "",
      note: "",
      reasons: [],
      children: [],
      rows: members,
      count: members.length,
      copies: 0,
    }))
    .sort((a, b) => compare(a.title, b.title));
}

const biggestFirst = (a: TreeNode, b: TreeNode) => b.count - a.count || compare(a.title, b.title);

function push<T>(groups: Map<string, T[]>, key: string, value: T) {
  const list = groups.get(key);
  if (list) list.push(value);
  else groups.set(key, [value]);
}

const repeatIndex = (repeats: string[][]) =>
  new Map(repeats.flatMap((ids) => ids.map((id) => [id, ids[0]] as const)));

const distinctItems = (rows: Row[], repeatOf: Map<string, string>) =>
  new Set(rows.map((r) => repeatOf.get(r.id) ?? r.id)).size;

export function buildTree(rows: Row[], map: MapData): { tree: TreeNode[]; crossLinks: CrossLink[] } {
  const edges = map.edges.filter((e) => e.kind !== "possible");
  const byId = new Map(rows.map((r) => [r.id, r]));
  const repeatOf = repeatIndex(map.repeats);
  const distinctCount = (members: Row[]) => distinctItems(members, repeatOf);

  const campaignNode = (id: string, members: Row[]): TreeNode => {
    const key = `campaign:${id}`;
    return {
      key,
      kind: "campaign",
      title: `Campaign ${place(members)}`,
      name: members[0].campaign,
      note: "Likely run by the same people.",
      href: campaignUrl(id),
      reasons: map.campaignWhy[id] ? [map.campaignWhy[id]] : reasons(members, edges, "campaign"),
      children: caseNodes(key, members),
      rows: [],
      count: distinctCount(members),
      copies: members.length - distinctCount(members),
    };
  };

  const campaigns = new Map<string, Row[]>();
  for (const r of rows) {
    if (r.campaignId) push(campaigns, r.campaignId, r);
  }

  // A campaign sits under the template most of its items use, so it is never shown twice.
  const kitCampaigns = new Map<string, string[]>();
  const standalone: string[] = [];
  for (const [id, members] of campaigns) {
    const votes = new Map<string, number>();
    for (const r of members) if (r.kitId) votes.set(r.kitId, (votes.get(r.kitId) ?? 0) + 1);
    const kit = [...votes.entries()].sort((a, b) => b[1] - a[1] || compare(a[0], b[0]))[0]?.[0];
    if (kit) push(kitCampaigns, kit, id);
    else standalone.push(id);
  }

  const kitRows = new Map<string, Row[]>();
  for (const r of rows) if (r.kitId) push(kitRows, r.kitId, r);
  for (const id of kitCampaigns.keys()) if (!kitRows.has(id)) kitRows.set(id, []);

  const kits = [...kitRows.entries()].map(([id, members]): TreeNode => {
    const key = `kit:${id}`;
    const ownIds = kitCampaigns.get(id) ?? [];
    const own = ownIds.map((c) => campaignNode(c, campaigns.get(c)!)).sort(biggestFirst);
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
        count: distinctCount(loose),
        copies: loose.length - distinctCount(loose),
      });
    }
    const all = [...ownIds.flatMap((c) => campaigns.get(c)!), ...loose];
    const distinct = distinctCount(all);
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
      count: distinct,
      copies: all.length - distinct,
    };
  });

  const rest = rows.filter((r) => !r.campaignId && !r.kitId);
  const restIds = new Set(rest.map((r) => r.id));
  const repeats = map.repeats
    .filter((ids) => ids.length > 1 && ids.every((id) => restIds.has(id)))
    .map((ids) => ids.map((id) => byId.get(id)!));
  const grouped = new Set(repeats.flat().map((r) => r.id));

  // Only the backend's repeats read as copies. Any other link between ungrouped items is one it
  // declined to merge, so those stay one to one below.
  const links = repeats.map((members): TreeNode => {
    const key = `links:${members.map((r) => r.id).sort()[0]}`;
    return {
      key,
      kind: "links",
      title: `Same page added ${members.length} times ${place(members)}`,
      name: "",
      note: "Copies of one page, not separate findings.",
      reasons: [],
      children: caseNodes(key, members),
      rows: [],
      count: 1,
      copies: members.length - 1,
    };
  });

  const tree = [
    // A template whose only items sit in a campaign shown under another template has nothing left.
    ...kits.filter((k) => k.count > 0).sort(biggestFirst),
    ...standalone.map((c) => campaignNode(c, campaigns.get(c)!)).sort(biggestFirst),
    ...links.sort(biggestFirst),
  ];

  const withLinks = new Set(edges.flatMap((e) => [e.a, e.b]));
  const hasLink = (r: Row) => withLinks.has(r.id) || !!map.journeys[r.id]?.length;
  const oneToOne = rest.filter((r) => !grouped.has(r.id) && hasLink(r));
  if (oneToOne.length) {
    tree.push({
      key: "one-to-one",
      kind: "links",
      title: "Linked one to one, not grouped",
      name: "",
      note: "Each item shares evidence with the item named, but that doesn't put them all in one group.",
      reasons: [],
      children: caseNodes("one-to-one", oneToOne),
      rows: [],
      count: oneToOne.length,
      copies: 0,
    });
  }
  const unlinked = rest.filter((r) => !grouped.has(r.id) && !hasLink(r));
  if (unlinked.length) {
    tree.push({
      key: "unlinked",
      kind: "unlinked",
      title: "No firm link yet",
      name: "",
      note: "Nothing proves a link so far. This doesn't mean they are unrelated.",
      reasons: [],
      children: caseNodes("unlinked", unlinked),
      rows: [],
      count: unlinked.length,
      copies: 0,
    });
  }

  return { tree, crossLinks: attachLinkNotes(tree, rows, edges, map.journeys, repeatOf) };
}

/** Tell each item about firm links that its own group doesn't already show. */
function attachLinkNotes(
  tree: TreeNode[],
  rows: Row[],
  edges: MapEdge[],
  journeys: MapData["journeys"],
  repeatOf: Map<string, string>,
): CrossLink[] {
  const home = new Map<string, string>();
  const cases: TreeNode[] = [];
  const walk = (node: TreeNode, parent: string) => {
    if (node.kind !== "case") return node.children.forEach((c) => walk(c, node.key));
    cases.push(node);
    // These two branches don't claim their items belong together, so each item speaks for itself.
    const separate = parent === "one-to-one" || parent.endsWith("/loose");
    for (const r of node.rows) home.set(r.id, separate ? `one:${repeatOf.get(r.id) ?? r.id}` : parent);
  };
  tree.forEach((n) => walk(n, n.key));

  const byId = new Map(rows.map((r) => [r.id, r]));
  const others = new Map<string, Map<string, Set<string>>>();
  const details = new Map<string, Set<string>>();
  const pairs = new Map<string, { a: string; b: string; labels: Set<string>; details: Set<string> }>();
  const add = (from: string, to: string, label: string, detail: string) => {
    if (!byId.has(from) || !byId.has(to) || home.get(from) === home.get(to)) return;
    const mine = others.get(from) ?? new Map<string, Set<string>>();
    mine.set(to, (mine.get(to) ?? new Set<string>()).add(label));
    others.set(from, mine);
    details.set(from, (details.get(from) ?? new Set<string>()).add(detail));
    const [a, b] = from < to ? [from, to] : [to, from];
    const pair = pairs.get(`${a}\n${b}`) ?? { a, b, labels: new Set<string>(), details: new Set<string>() };
    pair.labels.add(label);
    pair.details.add(detail);
    pairs.set(`${a}\n${b}`, pair);
  };
  for (const e of edges) {
    // Items of one template are already shown together under it.
    if (e.kind === "kit" && byId.get(e.a)?.kitId && byId.get(e.a)?.kitId === byId.get(e.b)?.kitId) continue;
    add(e.a, e.b, linkLabel(e), e.reason);
    add(e.b, e.a, linkLabel(e), e.reason);
  }
  for (const [id, path] of Object.entries(journeys)) for (const to of path) add(id, to, "same visitor path", "On the same visitor path");

  const notes: Record<string, { text: string; detail: string }> = {};
  for (const [id, targets] of others) {
    const parts = [...targets.entries()].map(([to, labels]) => {
      const target = byId.get(to)!;
      return `${target.title} in ${shortCase(target.case)} (${[...labels].sort(compare).join(", ")})`;
    });
    notes[id] = { text: `linked to ${parts.sort(compare).join("; ")}`, detail: [...details.get(id)!].join("; ") };
  }
  for (const node of cases) node.linkNotes = notes;
  return [...pairs.values()].map((p) => ({
    a: p.a,
    b: p.b,
    labels: [...p.labels].sort(compare),
    detail: [...p.details].join("; "),
  }));
}

/** Lower case with Polish letters flattened, so "lodz" finds "Łódź". */
const plain = (text: string) =>
  text.toLocaleLowerCase("pl").replace(/ł/g, "l").normalize("NFD").replace(/\p{M}/gu, "");

const rowText = (r: Row) => plain([r.title, r.host, r.case, shortCase(r.case), r.type, r.campaign, r.kit].join(" "));

/** Only the branches holding an item whose title, site, case or group name matches. */
export function filterTree(nodes: TreeNode[], query: string, repeats: string[][]): TreeNode[] {
  const q = plain(query.trim());
  if (!q) return nodes;
  const repeatOf = repeatIndex(repeats);
  const allRows = (node: TreeNode): Row[] => [...node.rows, ...node.children.flatMap(allRows)];
  const prune = (node: TreeNode): TreeNode | null => {
    const rows = node.rows.filter((r) => rowText(r).includes(q));
    const children = node.children.map(prune).filter((c): c is TreeNode => c !== null);
    if (!rows.length && !children.length) return null;
    const kept = allRows({ ...node, rows, children });
    const count = distinctItems(kept, repeatOf);
    return { ...node, rows, children, count, copies: kept.length - count };
  };
  return nodes.map(prune).filter((n): n is TreeNode => n !== null);
}
