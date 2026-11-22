import { Chip, useMeta } from "../components/bits";

const EVIDENCE = [
  {
    name: "Accounts and IDs",
    text: "Tracking pixels, campaign records, phone numbers: things only the operator controls. Shared ones make a campaign link.",
  },
  {
    name: "Content",
    text: "The same or nearly the same images, text or ad pictures. Shows reuse; a page copied whole is a duplicate.",
  },
  {
    name: "Templates and code",
    text: "The same page template, forms or tracking software. Makes a kit link: kits are sold, so this alone doesn't mean the same people.",
  },
  {
    name: "Persuasion style",
    text: "Similar story, roles or pressure tactics. Only ever a possible link, never enough on its own.",
  },
];

export default function HowItWorks() {
  const meta = useMeta();
  return (
    <div className="prose" style={{ maxWidth: 900 }}>
      <h1>How it works</h1>
      <p>
        Add what you found: a link, a saved page, screenshots or a whole folder. Each item is read for warning signs,
        then compared with everything already in the Library. Items that share something are linked, and the Library
        shows why.
      </p>

      <h2>The groups</h2>
      {meta && (
        <div className="explain">
          {(["case", "campaign", "kit", "duplicate"] as const).map((k) => (
            <div className="box" key={k}>
              <Chip kind={k}>{k[0].toUpperCase() + k.slice(1)}</Chip>
              <p style={{ margin: "6px 0 0" }}>{meta.groups[k]}</p>
            </div>
          ))}
        </div>
      )}

      <h2>What a link is based on</h2>
      <div className="explain">
        {EVIDENCE.map((e) => (
          <div className="box" key={e.name}>
            <b>{e.name}</b>
            <p style={{ margin: "4px 0 0" }}>{e.text}</p>
          </div>
        ))}
      </div>
      <p>
        Each link says how confident the comparison is: <b>strong</b>, <b>moderate</b> or <b>weak</b>. A value
        that many unrelated sites share, such as a common analytics script, never links anything.
      </p>

      <h2>Warning signs</h2>
      <p>
        Warning signs are things scam pages tend to do: pressure tactics such as countdown timers and "only N left"
        counters, tricks that hide the page from reviewers, web addresses with endings often used by scam sites, or a
        listing on a public warning list. They are counted, not scored: the Library never says "scam" by itself.
      </p>
      <p>
        Some checks rely on a model (for example spotting a page that pretends to be a known brand) and may not have
        run. When a check did not run, the item says <b>not checked</b>, which is different from <b>none found</b>.
      </p>

      <h2>Report</h2>
      <p>Everything stored, as one Markdown report or as a zip with the report and JSON/CSV exports.</p>
      <div style={{ display: "flex", gap: 8 }}>
        <a className="btn" href="/api/report/corpus-report.md">
          Download report (Markdown)
        </a>
        <a className="btn" href="/api/report/follow-the-fake-export.zip">
          Download everything (zip)
        </a>
      </div>
      <p className="small">Private project material: do not publish or redistribute.</p>

      <h2>Specialist tools</h2>
      <p>
        Method weights and thresholds, pattern-library edits and the graph of every stored relation are still in the
        classic interface: <code>streamlit run app/streamlit_app.py</code>.
      </p>
    </div>
  );
}
