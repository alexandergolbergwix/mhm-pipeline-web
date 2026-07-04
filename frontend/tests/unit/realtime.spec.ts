import {act, renderHook} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";

import {useProjectEvents, type ProjectEventMessage} from "@/api/realtime";

type Listener = (event: {data?: string}) => void;

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  static OPEN = 1;
  readyState = MockWebSocket.OPEN;
  private listeners = new Map<string, Set<Listener>>();

  constructor(public url: string) {
    MockWebSocket.instances.push(this);
  }

  addEventListener(type: string, listener: Listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }

  close() {
    this.emit("close", {});
  }

  emit(type: string, event: {data?: string}) {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

describe("useProjectEvents", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket as unknown as typeof WebSocket);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("connects to the project websocket URL", () => {
    renderHook(() => useProjectEvents("proj-1", vi.fn()));
    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url).toContain("/api/ws/projects/proj-1");
  });

  it("debounces onEvent and ignores ping messages", async () => {
    const onEvent = vi.fn();
    renderHook(() => useProjectEvents("proj-1", onEvent, {debounceMs: 200}));

    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.emit("open", {});
      socket.emit("message", {data: JSON.stringify({type: "ping"})});
      socket.emit("message", {data: JSON.stringify({
        project_id: "proj-1",
        event_id: "e1",
        type: "match.approved",
        actor_id: null,
        created_at: "2026-07-04T00:00:00Z",
      })});
      socket.emit("message", {data: JSON.stringify({
        project_id: "proj-1",
        event_id: "e2",
        type: "match.approved",
        actor_id: null,
        created_at: "2026-07-04T00:00:01Z",
      })});
    });

    expect(onEvent).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(200);
    });

    expect(onEvent).toHaveBeenCalledTimes(1);
    expect((onEvent.mock.calls[0][0] as ProjectEventMessage).event_id).toBe("e2");
  });

  it("calls onReconnect after a reconnect, not on the first open", () => {
    const onReconnect = vi.fn();
    renderHook(() => useProjectEvents("proj-1", vi.fn(), {onReconnect}));

    const socket = MockWebSocket.instances[0];
    act(() => {
      socket.emit("open", {});
    });
    expect(onReconnect).not.toHaveBeenCalled();

    act(() => {
      socket.emit("close", {});
      vi.advanceTimersByTime(1_000);
    });

    expect(MockWebSocket.instances).toHaveLength(2);
    act(() => {
      MockWebSocket.instances[1].emit("open", {});
    });
    expect(onReconnect).toHaveBeenCalledTimes(1);
  });
});
