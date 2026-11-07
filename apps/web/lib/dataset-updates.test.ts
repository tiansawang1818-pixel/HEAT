import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { subscribeDatasetUpdates } from "./dataset-updates";

class Stream extends EventTarget {
  static CLOSED = 2;
  static instances: Stream[] = [];
  readyState = 1;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  constructor(public url: string) { super(); Stream.instances.push(this); }
  close() { this.readyState = Stream.CLOSED; }
  publish(data: unknown, id = "") {
    this.dispatchEvent(new MessageEvent("data-update", { data: typeof data === "string" ? data : JSON.stringify(data), lastEventId: id }));
  }
}

beforeEach(() => { vi.useFakeTimers(); Stream.instances = []; vi.stubGlobal("EventSource", Stream); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function listen() {
  const reload = vi.fn(async () => {}), refreshPlans = vi.fn(async () => {}), connection = vi.fn();
  const stop = subscribeDatasetUpdates({ url: "/api/events/stream", activeLayer: "temperature", reload, refreshPlans,
    connection, needsMapRetry: () => false, candidateApproved: vi.fn() });
  return { reload, refreshPlans, connection, stop, stream: Stream.instances[0] };
}

it("updates only affected data and does not refetch the map for an unrelated publication", () => {
  const test = listen();
  expect(test.reload).toHaveBeenCalledWith(false, false, true);
  test.stream.publish({ layer: "vegetation", affected_layers: ["vegetation", "priority"] }, "event-1");
  expect(test.reload).toHaveBeenLastCalledWith(true, true, false);
  test.stream.publish({ layer: "temperature", affected_layers: ["temperature", "priority"] }, "event-2");
  expect(test.reload).toHaveBeenLastCalledWith(true, true, true);
  expect(test.refreshPlans).toHaveBeenCalledTimes(2);
  test.stream.publish({ layer: "temperature" }, "event-2");
  expect(test.reload).toHaveBeenCalledTimes(3);
  test.stop();
});

it("refreshes freshness metadata while live, falls back to features after disconnection and releases all timers", () => {
  const test = listen();
  test.stream.onopen?.();
  expect(test.connection).toHaveBeenLastCalledWith("live");
  vi.advanceTimersByTime(60_000);
  expect(test.reload).toHaveBeenLastCalledWith(false, true, false);
  test.stream.onerror?.();
  expect(test.connection).toHaveBeenLastCalledWith("polling");
  vi.advanceTimersByTime(60_000);
  expect(test.reload).toHaveBeenLastCalledWith(false, true, true);
  test.stop();
  const count = test.reload.mock.calls.length;
  test.stream.publish({ layer: "temperature" });
  vi.advanceTimersByTime(120_000);
  expect(test.reload).toHaveBeenCalledTimes(count);
});

it("handles unavailable streams and invalid event shapes without a browser page reload", () => {
  const test = listen();
  test.stream.publish("invalid-json");
  expect(test.reload).toHaveBeenLastCalledWith(true, true, true);
  test.stop();
  vi.stubGlobal("EventSource", class { constructor() { throw new Error("stream unavailable"); } });
  const fallback = listen();
  expect(fallback.connection).toHaveBeenCalledWith("polling");
  vi.advanceTimersByTime(60_000);
  expect(fallback.reload).toHaveBeenLastCalledWith(false, true, true);
  fallback.stop();
});
