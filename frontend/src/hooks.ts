import { useEffect, useState } from "react";

/** Data from the API, fetched again every 1.5 s while an analysis runs. */
export function usePolled<T extends { busy: boolean }>(fetcher: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const load = () =>
      fetcher()
        .then((d) => {
          if (!alive) return;
          setData(d);
          setError(null);
          timer = window.setTimeout(load, d.busy ? 1500 : 10000);
        })
        .catch((e) => alive && setError(String(e.message ?? e)));
    load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [fetcher, revision]);
  return { data, error, refresh: () => setRevision((r) => r + 1) };
}

export const campaignUrl = (id: string) => `/campaigns/${encodeURIComponent(id)}`;
export const kitUrl = (id: string) => `/campaigns/kit/${encodeURIComponent(id)}`;
