import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { FollowerStart, useVictim, VictimRunView } from "../components/VictimAgent";

/** The Follower: goes through a site as a made-up person and records it. Started from Add material. */
export default function Follower() {
  const [params, setParams] = useSearchParams();
  const agent = useVictim(params.get("run"));
  const { data, selected, setSelected } = agent;
  // Open the newest run when none is named; keep the address in step with the run on show.
  useEffect(() => {
    if (!selected && data?.runs.length) setSelected(data.runs[0].id);
  }, [data, selected, setSelected]);
  useEffect(() => {
    if (selected && params.get("run") !== selected) setParams({ run: selected }, { replace: true });
  }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="follower">
      <h1>Follower</h1>
      <p className="lead">
        Goes through a site the way a targeted person would: reads, answers the chat or quiz, and fills in the lead form
        with made-up details. It never pays, uploads or installs anything. A browser window opens on this computer, and
        each run is recorded as a video, with every page it reached added to the Library.
      </p>
      {data && !data.runs.length && !agent.run && (
        <div className="card">
          No runs yet. Paste a link in <Link to="/add">Add material</Link> and press <b>Start Follower</b>.
        </div>
      )}
      <VictimRunView agent={agent} />
      <FollowerStart agent={agent} initialUrl={agent.run?.startUrl ?? ""} key={agent.run?.id ?? "none"} />
    </div>
  );
}
