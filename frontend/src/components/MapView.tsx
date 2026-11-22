import { useNavigate } from "react-router-dom";
import { pictureUrl, type LibraryData, type LinkKind } from "../api";
import { plural } from "./bits";

const W = 170;
const H = 104;
const STROKE: Record<Exclude<LinkKind, "possible">, { color: string; dash?: string; width: number }> = {
  campaign: { color: "var(--camp-line)", width: 3 },
  kit: { color: "var(--kit-line)", width: 2.5, dash: "8 6" },
  duplicate: { color: "var(--dup-line)", width: 3 },
};

const short = (text: string, n: number) => (text.length > n ? text.slice(0, n - 1) + "…" : text);

/** Linked items as pictures; a line is a link, labelled with what the two items share. */
export default function MapView({ map, unlinked }: { map: LibraryData["map"]; unlinked: number }) {
  const navigate = useNavigate();
  if (!map.nodes.length) {
    return (
      <div className="empty card">
        Nothing is linked yet. Items appear here once they share something with another item.
      </div>
    );
  }
  const at = Object.fromEntries(map.nodes.map((n) => [n.id, n]));
  const width = Math.max(...map.nodes.map((n) => n.x)) + W;
  const height = Math.max(...map.nodes.map((n) => n.y)) + H + 40;
  const open = (id: string) => navigate(`/item/${encodeURIComponent(id)}`);
  return (
    <>
      <div className="mapbox">
        <svg viewBox={`-20 -10 ${width + 40} ${height}`} role="img" aria-label="Map of linked items">
          <defs>
            <clipPath id="card">
              <rect width={W} height={H} rx={6} />
            </clipPath>
          </defs>
          {map.edges.map((e) => {
            const a = at[e.a];
            const b = at[e.b];
            if (!a || !b || e.kind === "possible") return null;
            const s = STROKE[e.kind];
            const label = e.kind === "duplicate" ? "same page" : e.label;
            const mx = (a.x + b.x) / 2;
            const my = (a.y + b.y) / 2;
            return (
              <g key={`${e.a}|${e.b}`}>
                <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={s.color} strokeWidth={s.width} strokeDasharray={s.dash}>
                  <title>{e.reason}</title>
                </line>
                <rect x={mx - label.length * 3.4 - 6} y={my - 10} width={label.length * 6.8 + 12} height={20} rx={4} fill="#fff" />
                <text x={mx} y={my + 4} textAnchor="middle" fontSize={12} fill="#4a4945">
                  {label}
                </text>
              </g>
            );
          })}
          {map.nodes.map((n) => (
            <g
              key={n.id}
              className="node"
              transform={`translate(${n.x - W / 2},${n.y - H / 2})`}
              onClick={() => open(n.id)}
              onKeyDown={(e) => e.key === "Enter" && open(n.id)}
              tabIndex={0}
              role="link"
              aria-label={n.title}
            >
              <rect className="frame" x={-2} y={-2} width={W + 4} height={H + 4} rx={8} fill="#fff" stroke="#d3d1c7" />
              {n.picture ? (
                <image href={pictureUrl(n.id)} width={W} height={H} preserveAspectRatio="xMidYMin slice" clipPath="url(#card)" />
              ) : (
                <>
                  <rect width={W} height={H} rx={6} fill="#f3f2ed" />
                  <text x={W / 2} y={H / 2 + 4} textAnchor="middle" fontSize={11.5} fill="#8a877f">
                    no preview
                  </text>
                </>
              )}
              <text y={H + 18} fontSize={12} fill="#1f1f1d">
                {short(n.title, 25)}
              </text>
              <text y={H + 33} fontSize={11.5} fill="#6d6b67">
                {n.case}
                {n.signs ? ` · ${plural(n.signs, "warning sign")}` : ""}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <div className="mapkey">
        <span>
          <i style={{ borderColor: "var(--camp-line)" }} />
          Campaign link: likely the same people
        </span>
        <span>
          <i style={{ borderColor: "var(--kit-line)", borderTopStyle: "dashed" }} />
          Kit link: same template, maybe different people
        </span>
        <span>
          <i style={{ borderColor: "var(--dup-line)" }} />
          Same page added twice
        </span>
        <span>Click a picture to open it.</span>
        {unlinked > 0 && (
          <span>
            {unlinked === 1 ? "1 item isn't" : `${unlinked} items aren't`} linked to anything yet and appear only in
            the table.
          </span>
        )}
      </div>
    </>
  );
}
