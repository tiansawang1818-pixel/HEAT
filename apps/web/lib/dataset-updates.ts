export type DataConnection = "connecting" | "live" | "polling";

interface DatasetUpdates {
  url: string;
  activeLayer: string;
  reload: (notify?: boolean, metadataOnly?: boolean, refreshActive?: boolean) => Promise<unknown>;
  refreshPlans: () => Promise<unknown>;
  connection: (status: DataConnection) => void;
  needsMapRetry: () => boolean;
  candidateApproved: () => void;
}

/** Publication changes data only. This subscriber never owns map/selection state. */
export function subscribeDatasetUpdates(options: DatasetUpdates) {
  let source: EventSource | null = null;
  let disposed = false;
  const recentEvents = new Set<string>();
  const reload = (notify: boolean, metadataOnly: boolean, refreshActive: boolean) => {
    if (disposed) return;
    void options.reload(notify, metadataOnly, refreshActive).catch(() => {
      if (!disposed) options.connection("polling");
    });
  };
  const plans = () => { if (!disposed) void options.refreshPlans().catch(() => {}); };
  const publication = (event: MessageEvent) => {
    if (disposed || (event.lastEventId && recentEvents.has(event.lastEventId))) return;
    if (event.lastEventId) {
      recentEvents.add(event.lastEventId);
      if (recentEvents.size > 64) recentEvents.delete(recentEvents.values().next().value!);
    }
    let refreshActive = true;
    try {
      const payload = JSON.parse(event.data) as { affected_layers?: unknown; layer?: unknown };
      const affected = Array.isArray(payload.affected_layers)
        ? payload.affected_layers.filter((layer): layer is string => typeof layer === "string")
        : typeof payload.layer === "string" ? [payload.layer] : [];
      refreshActive = !affected.length || affected.includes(options.activeLayer);
    } catch { /* Unknown publication payloads conservatively refresh the visible data. */ }
    reload(true, true, refreshActive);
    plans();
  };
  reload(false, false, true);
  try {
    source = new EventSource(options.url);
    source.onopen = () => { if (!disposed) options.connection("live"); };
    source.addEventListener("data-update", event => publication(event as MessageEvent));
    source.addEventListener("candidate.approved", () => {
      if (disposed) return;
      reload(true, true, options.activeLayer === "candidates");
      plans(); options.candidateApproved();
    });
    source.onmessage = event => {
      try {
        const type = JSON.parse(event.data).type;
        if (["data-update", "dataset_published"].includes(type)) publication(event);
      } catch { /* Heartbeat frames need no data refresh. */ }
    };
    source.onerror = () => {
      source?.close();
      if (!disposed) options.connection("polling");
    };
  } catch { options.connection("polling"); }
  const timer = setInterval(() => {
    const fallback = !source || source.readyState === EventSource.CLOSED || options.needsMapRetry();
    reload(false, true, fallback);
    plans();
  }, 60_000);
  return () => { disposed = true; source?.close(); clearInterval(timer); };
}
