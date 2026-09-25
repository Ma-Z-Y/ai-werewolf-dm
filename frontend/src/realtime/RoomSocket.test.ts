import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { parseServerMessage } from "../protocol/parseServerMessage";
import { RoomSocket } from "./RoomSocket";
import { useRoomSocket } from "./useRoomSocket";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static latest: FakeWebSocket;

  static reset(): void {
    FakeWebSocket.instances = [];
  }

  readonly url: string;
  readyState = 0;
  readonly sent: string[] = [];
  readonly closeCalls: Array<{ code: number; reason: string }> = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
    FakeWebSocket.latest = this;
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }

  message(payload: unknown): void {
    const data =
      typeof payload === "string" ? payload : JSON.stringify(payload);
    this.onmessage?.(new MessageEvent("message", { data }));
  }

  serverClose(code: number, reason = ""): void {
    this.readyState = 3;
    this.onclose?.({ code, reason } as CloseEvent);
  }

  close(code = 1000, reason = ""): void {
    this.closeCalls.push({ code, reason });
    this.serverClose(code, reason);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  received(): unknown[] {
    return this.sent.map((data) => JSON.parse(data) as unknown);
  }
}

const WebSocketCtor = FakeWebSocket as unknown as typeof WebSocket;

function publicView() {
  return {
    schema_version: "public-view.v1",
    room_id: "room-1",
    revision: 1,
    phase: "LOBBY",
    day: 0,
    living_seats: [1, 2, 3, 4, 5, 6],
    public_timeline: [],
    vote_summary: null,
    deadline_at: null,
    paused: false,
  };
}

function seatView(action: string) {
  return {
    ...publicView(),
    schema_version: "seat-view.v1",
    seat_id: 1,
    role: null,
    private_facts: [],
    legal_actions: [
      {
        action,
        target_seat_ids: [],
        deadline_at: null,
      },
    ],
  };
}

function createSocket(listener = vi.fn()) {
  const socket = new RoomSocket({
    url: "ws://test/ws",
    WebSocketCtor,
  });
  socket.subscribe(listener);
  return { socket, listener };
}

function messageEvents(listener: ReturnType<typeof vi.fn>) {
  return listener.mock.calls
    .map(([event]) => event)
    .filter((event) => event.kind === "message");
}

describe("parseServerMessage", () => {
  it("ignores unknown message types", () => {
    expect(parseServerMessage({ type: "future.message" })).toEqual({
      kind: "ignored",
    });
  });

  it("ignores malformed envelopes and accepts allowed message types", () => {
    expect(parseServerMessage(null)).toEqual({ kind: "ignored" });
    expect(parseServerMessage({ type: "public.view.updated" })).toEqual({
      kind: "ignored",
    });
    expect(parseServerMessage({ type: "auth.required" })).toEqual({
      kind: "message",
      message: { type: "auth.required" },
    });
    expect(parseServerMessage({ type: "pong" })).toEqual({
      kind: "message",
      message: { type: "pong" },
    });
  });

  it("parses room updates, acknowledgements, and errors", () => {
    const publicUpdate = {
      type: "public.view.updated",
      server_time: "2026-09-25T00:00:00.000Z",
      outbox_seq: 3,
      public_view: publicView(),
    };
    expect(parseServerMessage(publicUpdate)).toEqual({
      kind: "message",
      message: publicUpdate,
    });
    expect(
      parseServerMessage({
        type: "command.ack",
        command_id: "command-1",
        accepted: true,
        revision: 2,
        error_code: null,
        outbox_seq: 3,
      }),
    ).toEqual({
      kind: "message",
      message: {
        type: "command.ack",
        command_id: "command-1",
        accepted: true,
        revision: 2,
        error_code: null,
        outbox_seq: 3,
      },
    });
    expect(
      parseServerMessage({
        type: "error",
        code: "TOKEN_INVALID",
        message: "令牌无效",
        request_id: "request-1",
      }),
    ).toEqual({
      kind: "message",
      message: {
        type: "error",
        code: "TOKEN_INVALID",
        message: "令牌无效",
        request_id: "request-1",
      },
    });
  });

  it("ignores a seat update with an unknown legal action", () => {
    expect(
      parseServerMessage({
        type: "seat.view.updated",
        server_time: "2026-09-25T00:00:00.000Z",
        outbox_seq: 3,
        seat_id: 1,
        seat_view: seatView("NOT_A_COMMAND"),
      }),
    ).toEqual({ kind: "ignored" });
  });
});

describe("RoomSocket", () => {
  beforeEach(() => {
    vi.useRealTimers();
    FakeWebSocket.reset();
  });

  it("authenticates after open", () => {
    const { socket } = createSocket();
    socket.connect("seat-token");

    FakeWebSocket.latest.open();

    expect(FakeWebSocket.latest.received()).toEqual([
      { type: "auth", token: "seat-token", last_seq: 0 },
    ]);
  });

  it("always authenticates with last_seq zero", () => {
    const socket = new RoomSocket({
      url: "ws://test/ws",
      WebSocketCtor,
      lastSeq: 42,
    } as unknown as ConstructorParameters<typeof RoomSocket>[0]);
    socket.connect("seat-token");

    FakeWebSocket.latest.open();

    expect(FakeWebSocket.latest.received()).toEqual([
      { type: "auth", token: "seat-token", last_seq: 0 },
    ]);
  });

  it("forwards allowed messages and ignores malformed frames", () => {
    const { socket, listener } = createSocket();
    socket.connect("seat-token");
    const ws = FakeWebSocket.latest;
    ws.open();

    ws.message("not-json");
    ws.message({ type: "public.view.updated" });
    ws.message({ type: "pong" });

    expect(messageEvents(listener)).toEqual([
      { kind: "message", message: { type: "pong" } },
    ]);
  });

  it("does not reconnect after 4001", () => {
    vi.useFakeTimers();
    const { socket } = createSocket();
    socket.connect("bad-token");

    FakeWebSocket.latest.serverClose(4001);
    vi.runAllTimers();

    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("does not reconnect after 4003", () => {
    vi.useFakeTimers();
    const { socket } = createSocket();
    socket.connect("seat-token");

    FakeWebSocket.latest.serverClose(4003);
    vi.runAllTimers();

    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("reconnects immediately after 1001", () => {
    vi.useFakeTimers();
    const { socket } = createSocket();
    socket.connect("seat-token");

    FakeWebSocket.latest.serverClose(1001);
    vi.runAllTimers();

    expect(FakeWebSocket.instances).toHaveLength(2);
    FakeWebSocket.latest.open();
    expect(FakeWebSocket.latest.received()).toEqual([
      { type: "auth", token: "seat-token", last_seq: 0 },
    ]);
  });

  it("backs off exponentially after 1008", () => {
    vi.useFakeTimers();
    const { socket } = createSocket();
    socket.connect("seat-token");

    FakeWebSocket.latest.serverClose(1008);
    vi.advanceTimersByTime(499);
    expect(FakeWebSocket.instances).toHaveLength(1);

    vi.advanceTimersByTime(1);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("closes and retries when authentication does not complete", () => {
    vi.useFakeTimers();
    const { socket } = createSocket();
    socket.connect("seat-token");
    const ws = FakeWebSocket.latest;
    ws.open();

    vi.advanceTimersByTime(5000);
    expect(ws.closeCalls).toEqual([{ code: 4002, reason: "auth timeout" }]);

    vi.advanceTimersByTime(500);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("disconnect cancels pending reconnects", () => {
    vi.useFakeTimers();
    const { socket } = createSocket();
    socket.connect("seat-token");

    FakeWebSocket.latest.serverClose(1013);
    socket.disconnect();
    vi.runAllTimers();

    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("disconnect closes a live socket", () => {
    const { socket } = createSocket();
    socket.connect("seat-token");
    const ws = FakeWebSocket.latest;
    ws.open();

    socket.disconnect();

    expect(ws.closeCalls).toEqual([{ code: 1000, reason: "client disconnect" }]);
  });
});

describe("useRoomSocket", () => {
  beforeEach(() => {
    FakeWebSocket.reset();
  });

  it("keeps one socket per room and role and forwards messages", () => {
    const listener = vi.fn();
    const config = {
      url: "ws://test/ws",
      roomCode: "ABCDEF",
      role: "seat" as const,
      token: "seat-token",
      WebSocketCtor,
    };
    const { result, rerender, unmount } = renderHook(
      ({ value }) => useRoomSocket(value, listener),
      { initialProps: { value: config } },
    );

    const socket = result.current;
    const ws = FakeWebSocket.latest;
    ws.open();
    rerender({
      value: {
        ...config,
      },
    });

    expect(result.current).toBe(socket);
    expect(FakeWebSocket.instances).toHaveLength(1);

    ws.message({ type: "pong" });
    expect(listener).toHaveBeenCalledWith({
      kind: "message",
      message: { type: "pong" },
    });

    unmount();
    expect(ws.closeCalls).toEqual([{ code: 1000, reason: "client disconnect" }]);
  });
});
