import { Link } from "react-router-dom";

/** Where the page sits: "Campaigns › Shared kits › Kit 25c6c6fd…". The last part is this page. */
export function Crumbs({ parts }: { parts: { label: string; to?: string }[] }) {
  return (
    <nav className="crumbs" aria-label="You are here">
      {parts.map((p, n) => (
        <span key={n}>
          {n > 0 && <span className="sep">›</span>}
          {p.to ? <Link to={p.to}>{p.label}</Link> : <span>{p.label}</span>}
        </span>
      ))}
    </nav>
  );
}

const BANDS = {
  item: { icon: "▢", text: "" },
  campaign: { icon: "◉", text: "Campaign · very likely the same people" },
  kit: { icon: "⧉", text: "Kit · one template, possibly different people" },
};

/** The label above a page's title that says what kind of page it is. */
export function Band({ kind, text }: { kind: keyof typeof BANDS; text?: string }) {
  return (
    <div className={`band band-${kind}`}>
      <span aria-hidden="true">{BANDS[kind].icon}</span> {text ?? BANDS[kind].text}
    </div>
  );
}
