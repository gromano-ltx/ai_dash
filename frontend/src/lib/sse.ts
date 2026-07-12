import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

export function useRunsStream() {
  const queryClient = useQueryClient();

  useEffect(() => {
    let es: EventSource | null = null;
    let retryTimeout: ReturnType<typeof setTimeout>;

    function connect() {
      es = new EventSource("/api/stream");

      es.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data);
          if (event.type === "run_updated") {
            queryClient.invalidateQueries({ queryKey: ["runs"] });
            queryClient.invalidateQueries({ queryKey: ["stats"] });
            queryClient.invalidateQueries({ queryKey: ["run", event.id] });
            // "daily" is deliberately not invalidated here — it buckets by
            // day, not by individual run, so its own 30s poll (see useDaily)
            // is fresh enough and doesn't need a per-event refresh.
          }
        } catch {
          // ignore malformed events
        }
      };

      es.onerror = () => {
        es?.close();
        retryTimeout = setTimeout(connect, 5000);
      };
    }

    connect();

    return () => {
      es?.close();
      clearTimeout(retryTimeout);
    };
  }, [queryClient]);
}
