import { useEffect, useState } from "react";

/** Data from the API, fetched again every 1.5 s while an analysis runs. */
export function usePolled<T extends { busy: boolean }>(fetcher: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const load = () =>
      fetcher()
        .then((d) => {
          if (!alive) return;
          setData(d);
          setError(null);
          if (d.busy) timer = window.setTimeout(load, 1500);
        })
        .catch((e) => alive && setError(String(e.message ?? e)));
    load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [fetcher]);
  return { data, error };
}

export const campaignUrl = (id: string) => `/campaigns/${encodeURIComponent(id)}`;
export const kitUrl = (id: string) => `/campaigns/kit/${encodeURIComponent(id)}`;
