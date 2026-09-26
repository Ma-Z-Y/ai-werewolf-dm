import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { writeDisplaySession } from "../../session/storage";

import { StagePairingScreen } from "./StagePairingScreen";
import { StageRoute } from "./StageRoute";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  static reset(): void {
    FakeWebSocket.instances = [];
  }

  readonly url: string;
  readyState = 0;
  readonly sent: string[] = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }

  message(payload: unknown): void {
    this.onmessage?.(
      new MessageEvent("message", { data: JSON.stringify(payload) }),
    );
  }

  close(code = 1000, reason = ""): void {
    this.readyState = 3;
    this.onclose?.({ code, reason } as CloseEvent);
  }

  send(data: string): void {
    this.sent.push(data);
  }
}

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(payload),
  } as unknown as Response;
}

function publicView(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "public-view.v1",
    room_id: "room-1",
    revision: 4,
    phase: "DAY_DISCUSSION",
    day: 1,
    living_seats: [1, 2, 3, 5],
    public_timeline: [],
    vote_summary: null,
    deadline_at: null,
    paused: false,
    paused_at: null,
    ...overrides,
  };
}

function sessionReady(view: ReturnType<typeof publicView>) {
  return {
    type: "session.ready",
    server_time: "2026-09-26T00:00:00.000Z",
    snapshot: {
      room_id: "room-1",
      room_code: "ABCDEF",
      revision: view.revision,
      outbox_seq: 0,
      public_view: view,
      seat_view: null,
      host_control: null,
    },
  };
}

function publicViewUpdate(
  view: ReturnType<typeof publicView>,
  serverTime = "2026-09-26T00:00:01.000Z",
) {
  return {
    type: "public.view.updated",
    server_time: serverTime,
    outbox_seq: view.revision,
    public_view: view,
  };
}

function renderStageRoute(path = "/stage/ABCDEF") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/stage/:roomCode" element={<StageRoute />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function openStageSocket(
  view: ReturnType<typeof publicView> = publicView(),
): Promise<FakeWebSocket> {
  await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
  const socket = FakeWebSocket.instances[0];
  await act(async () => {
    socket.open();
    socket.message(sessionReady(view));
  });
  return socket;
}

function parsedFrames(socket: FakeWebSocket): Array<Record<string, unknown>> {
  return socket.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>);
}

function writeDisplaySessionForRoom(): void {
  writeDisplaySession({
    schemaVersion: 1,
    roomCode: "ABCDEF",
    roomId: "room-1",
    token: "display-token",
    expiresAt: "2099-01-01T00:00:00.000Z",
  });
}

function stubWakeLock() {
  const sentinel = {
    addEventListener: vi.fn(),
    release: vi.fn().mockResolvedValue(undefined),
  };
  const request = vi.fn().mockResolvedValue(sentinel);
  vi.stubGlobal("navigator", {
    ...globalThis.navigator,
    wakeLock: { request },
  });
  return { request, sentinel };
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  fetchMock.mockReset();
  FakeWebSocket.reset();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("shared stage", () => {
  it("exchanges a pairing code and renders only public data", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(
      jsonResponse(201, {
        room_id: "room-1",
        display_token: "display-token",
        expires_at: "2099-01-01T00:00:00.000Z",
      }),
    );

    renderStageRoute();
    await user.type(screen.getByLabelText("配对码"), "482913");
    await user.click(screen.getByRole("button", { name: "连接共享屏" }));

    expect(
      await screen.findByText("正在连接共享屏"),
    ).toBeVisible();
    expect(screen.queryByLabelText("配对码")).not.toBeInTheDocument();
    expect(
      sessionStorage.getItem("werewolf:v1:room:ABCDEF:display"),
    ).toContain("display-token");
    expect(localStorage.getItem("werewolf:v1:room:ABCDEF:display")).toBeNull();

    const socket = await openStageSocket(
      publicView({
        public_timeline: [
          {
            event_id: "event-1",
            revision: 4,
            event_type: "PHASE_CHANGED",
            statement: "第 1 天 · 白天讨论",
          },
        ],
      }),
    );

    expect(
      await screen.findByRole("heading", { name: "第 1 天 · 白天讨论" }),
    ).toBeVisible();
    expect(screen.queryByText("主持人控制")).not.toBeInTheDocument();
    expect(parsedFrames(socket)).toEqual([
      {
        type: "auth",
        token: "display-token",
        last_seq: 0,
      },
      {
        type: "subscribe",
        channel: "public",
      },
    ]);
  });

  it("rejects a pairing code after five failures", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(
      jsonResponse(400, {
        code: "TOKEN_INVALID",
        message: "server text must not render",
        request_id: "request-1",
      }),
    );

    renderStageRoute();
    for (let attempt = 0; attempt < 5; attempt += 1) {
      await user.type(screen.getByLabelText("配对码"), "000000");
      await user.click(screen.getByRole("button", { name: "连接共享屏" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "配对码无效或已过期，请重新生成",
      );
    }

    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(
      screen.queryByText("server text must not render"),
    ).not.toBeInTheDocument();
  });

  it("shows the independent rate-limit message", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(
      jsonResponse(429, {
        code: "RATE_LIMITED",
        message: "server text must not render",
        request_id: "request-1",
      }),
    );

    renderStageRoute();
    await user.type(screen.getByLabelText("配对码"), "000000");
    await user.click(screen.getByRole("button", { name: "连接共享屏" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "尝试次数过多，请重新生成配对码",
    );
  });

  it("renders room, timeline, living seats, vote progress, and timer", async () => {
    writeDisplaySessionForRoom();
    renderStageRoute();
    await openStageSocket(
      publicView({
        deadline_at: "2026-09-26T00:00:30.000Z",
        public_timeline: [
          {
            event_id: "event-1",
            revision: 3,
            event_type: "PHASE_CHANGED",
            statement: "第 1 天 · 白天讨论",
          },
          {
            event_id: "event-2",
            revision: 4,
            event_type: "PHASE_CHANGED",
            statement: "3 号玩家开始发言",
          },
        ],
        vote_summary: {
          round_id: "round-1",
          tallies: [],
          abstention_count: 0,
          closed: false,
          submitted_count: 2,
          eligible_count: 4,
        },
      }),
    );

    expect(await screen.findByText("房间 ABCDEF")).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "第 1 天 · 白天讨论" }),
    ).toBeVisible();
    expect(screen.getByText("3 号玩家开始发言")).toBeVisible();
    expect(screen.getByText("存活座位")).toBeVisible();
    expect(screen.getByText("2 / 4 已提交")).toBeVisible();
    expect(
      screen.getByRole("progressbar", { name: "投票进度" }),
    ).toBeVisible();
    expect(screen.getByText("计时 00:30")).toBeVisible();
    expect(screen.getByTestId("stage-root")).toHaveAttribute(
      "data-phase",
      "day",
    );
    expect(screen.getByTestId("stage-root")).toHaveClass(
      "day:bg-phase-day",
      "day:text-surface",
    );
  });

  it("uses the last public GAME_ENDED timeline item after GAME_END", async () => {
    writeDisplaySessionForRoom();
    renderStageRoute();
    await openStageSocket(
      publicView({
        phase: "GAME_END",
        public_timeline: [
          {
            event_id: "event-1",
            revision: 4,
            event_type: "GAME_ENDED",
            statement: "旧结果",
          },
          {
            event_id: "event-2",
            revision: 5,
            event_type: "GAME_ENDED",
            statement: "狼人阵营获胜",
          },
        ],
      }),
    );

    expect(await screen.findByTestId("stage-result")).toHaveTextContent(
      "狼人阵营获胜",
    );
  });

  it("ignores non-public room updates and exposes local presentation controls", async () => {
    writeDisplaySessionForRoom();
    renderStageRoute();
    const socket = await openStageSocket();

    await act(async () => {
      socket.message({
        type: "seat.view.updated",
        server_time: "2026-09-26T00:00:01.000Z",
        outbox_seq: 5,
        seat_id: 1,
        seat_view: {
          ...publicView({ phase: "NIGHT_WOLF", day: 1 }),
          schema_version: "seat-view.v1",
          seat_id: 1,
          role: "WEREWOLF",
          private_facts: [],
          legal_actions: [],
        },
      });
      socket.message({
        type: "host.control.updated",
        server_time: "2026-09-26T00:00:01.000Z",
        outbox_seq: 5,
        host_control: {
          public_view: publicView({ phase: "NIGHT_WOLF", day: 1 }),
          paused: false,
          revision: 5,
        },
      });
      socket.message({
        type: "command.ack",
        command_id: "command-1",
        accepted: true,
        revision: 5,
        error_code: null,
        outbox_seq: 5,
      });
    });

    expect(screen.queryByText("狼人行动")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "进入全屏" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "保持屏幕常亮" }),
    ).toBeVisible();
    expect(
      screen.getByText("当前浏览器不支持全屏和屏幕常亮"),
    ).toBeVisible();
    expect(parsedFrames(socket).some((frame) => frame.type === "command")).toBe(
      false,
    );
  });

  it("keeps a paused countdown frozen when server time changes", async () => {
    writeDisplaySessionForRoom();
    renderStageRoute();
    const socket = await openStageSocket(
      publicView({
        deadline_at: "2026-09-26T00:00:30.000Z",
        paused: true,
        paused_at: "2026-09-26T00:00:00.000Z",
      }),
    );

    expect(await screen.findByText("计时 00:30")).toBeVisible();

    await act(async () => {
      socket.message(
        publicViewUpdate(
          publicView({
            deadline_at: "2026-09-26T00:00:30.000Z",
            paused: true,
            paused_at: "2026-09-26T00:00:00.000Z",
            revision: 5,
          }),
          "2026-09-26T00:05:00.000Z",
        ),
      );
    });

    expect(screen.getByText("计时 00:30")).toBeVisible();
  });

  it("offers a manual reconnect after a retryable close", async () => {
    const user = userEvent.setup();
    writeDisplaySessionForRoom();
    renderStageRoute();
    const socket = await openStageSocket();

    await act(async () => {
      socket.close(1008, "policy close");
    });

    const retryButton = await screen.findByRole("button", {
      name: "重新连接共享屏",
    });
    expect(retryButton).toBeVisible();
    await user.click(retryButton);

    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
  });

  it("keeps local presentation state across public updates", async () => {
    const user = userEvent.setup();
    const { request } = stubWakeLock();
    writeDisplaySessionForRoom();
    renderStageRoute();
    const socket = await openStageSocket();

    const wakeLockButton = await screen.findByRole("button", {
      name: "保持屏幕常亮",
    });
    await user.click(wakeLockButton);
    await waitFor(() => expect(wakeLockButton).toHaveAttribute(
      "aria-pressed",
      "true",
    ));

    await act(async () => {
      socket.message(publicViewUpdate(publicView({ revision: 5 })));
    });

    expect(
      screen.getByRole("button", { name: "保持屏幕常亮" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(request).toHaveBeenCalledTimes(1);
    expect(parsedFrames(socket).some((frame) => frame.type === "command")).toBe(
      false,
    );
  });

  it("clears the display session when another device takes over", async () => {
    writeDisplaySessionForRoom();
    renderStageRoute();
    const socket = await openStageSocket();

    await act(async () => {
      socket.close(4003, "session replaced");
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "共享屏已在其他设备接管，请重新配对",
    );
    expect(
      sessionStorage.getItem("werewolf:v1:room:ABCDEF:display"),
    ).toBeNull();
  });

  it("returns to pairing and clears the display session after close 4001", async () => {
    writeDisplaySessionForRoom();
    renderStageRoute();
    const socket = await openStageSocket();

    await act(async () => {
      socket.close(4001, "display revoked");
    });

    expect(await screen.findByLabelText("配对码")).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "共享屏配对已失效，请重新配对",
    );
    expect(
      sessionStorage.getItem("werewolf:v1:room:ABCDEF:display"),
    ).toBeNull();
  });

  it("submits the normalized pairing code and clears it without unmounting", async () => {
    const user = userEvent.setup();
    const onPaired = vi.fn();
    fetchMock.mockResolvedValue(
      jsonResponse(201, {
        room_id: "room-1",
        display_token: "display-token",
        expires_at: "2099-01-01T00:00:00.000Z",
      }),
    );

    render(
      <StagePairingScreen onPaired={onPaired} roomCode="ABCDEF" />,
    );
    const input = screen.getByLabelText("配对码");
    await user.type(input, "48a2 913");
    expect(input).toHaveValue("482913");

    await user.click(screen.getByRole("button", { name: "连接共享屏" }));

    await waitFor(() => expect(onPaired).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "/rooms/ABCDEF/display-sessions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ pairing_code: "482913" }),
      }),
    );
    expect(input).toHaveValue("");
  });
});
