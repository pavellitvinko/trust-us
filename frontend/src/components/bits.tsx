import { createContext, useContext } from "react";
import { pictureUrl, type Meta } from "../api";

export const MetaContext = createContext<Meta | null>(null);
export const useMeta = () => useContext(MetaContext);

type Group = keyof Meta["groups"];

/** A "?" that explains a group in one or two sentences. */
export function Help({ topic }: { topic: Group }) {
  const meta = useMeta();
  if (!meta) return null;
  return (
    <span className="help" tabIndex={0} aria-label={meta.groups[topic]} onClick={(e) => e.stopPropagation()}>
      ?<span className="tip">{meta.groups[topic]}</span>
    </span>
  );
}

export function Chip({ kind, children }: { kind: string; children: React.ReactNode }) {
  if (!children) return null;
  return <span className={`chip ${kind}`}>{children}</span>;
}

/** The item's picture, or a quiet placeholder saying why there is none. */
export function Picture({
  id,
  has,
  type,
  width,
  height,
}: {
  id: string;
  has: boolean;
  type: string;
  width: number;
  height: number;
}) {
  if (!has) {
    return (
      <div className="pic none" style={{ width, height }}>
        {type === "Video" ? "Video" : type === "Page" ? "Web archive only" : "No picture"}
      </div>
    );
  }
  return <img className="pic" src={pictureUrl(id)} alt="" loading="lazy" style={{ width, height }} />;
}

const LEGEND: { kind: Group; label: string }[] = [
  { kind: "case", label: "Case" },
  { kind: "campaign", label: "Campaign" },
  { kind: "kit", label: "Kit" },
  { kind: "duplicate", label: "Duplicate" },
];

/** What the four groups mean, always in view above the Library. */
export function Legend() {
  const meta = useMeta();
  if (!meta) return null;
  return (
    <div className="legend">
      {LEGEND.map(({ kind, label }) => (
        <div key={kind}>
          <Chip kind={kind}>{label}</Chip>
          <div>{meta.groups[kind]}</div>
        </div>
      ))}
    </div>
  );
}

export function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
