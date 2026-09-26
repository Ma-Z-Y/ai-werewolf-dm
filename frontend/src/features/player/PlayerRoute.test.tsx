import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  MemoryRouter,
  Route,
  Routes,
  useNavigate,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "../../app/routes";
import type {
  CommandType,
  SeatView,
} from "../../protocol/models";
import {
  readHostSession,
  readSeatSession,
  writeSeatSession,
} from "../../session/storage";

import { PlayerRoute } from "./PlayerRoute";

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

  sentLast(): unknown {
    const raw = this.sent.at(-1);
    return raw === undefined ? undefined : (JSON.parse(raw) as unknown);
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

function publicView(revision = 1, livingSeats = [1, 2, 3, 4, 5, 6]) {
  return {
    schema_version: "public-view.v1",
    room_id: "room-1",
    revision,
    phase: "LOBBY",
    day: 0,
    living_seats: livingSeats,
    public_timeline: [],
    vote_summary: null,
    deadline_at: null,
    paused: false,
  };
}

interface SeatViewOptions {
  legalActions?: CommandType[];
  livingSeats?: number[];
}

function seatView(revision = 1, options: SeatViewOptions = {}): SeatView {
  return {
    ...publicView(revision, options.livingSeats),
    schema_version: "seat-view.v1",
    seat_id: 1,
    role: null,
    private_facts: [],
    legal_actions: (options.legalActions ?? ["SET_READY"]).map((action) => ({
      action,
      target_seat_ids: [],
      deadline_at: null,
    })),
  };
}

function preJoinSeatView(revision = 0) {
  return seatView(revision, {
    legalActions: ["JOIN_ROOM"],
    livingSeats: [],
  });
}

function nightSeatView({
  action,
  targetSeatIds = [],
  role,
  revision = 12,
}: {
  action: CommandType;
  targetSeatIds?: number[];
  role: string;
  revision?: number;
}): SeatView {
  const phase =
    action === "WOLF_NOMINATE_KILL"
      ? "NIGHT_WOLF"
      : action === "SEER_INSPECT"
        ? "NIGHT_SEER"
        : "NIGHT_WITCH";
  return {
    ...seatView(revision, { legalActions: [] }),
    phase,
    day: 1,
    role,
    legal_actions: [
      {
        action,
        target_seat_ids: targetSeatIds,
        deadline_at: "2099-01-01T00:01:00.000Z",
      },
    ],
  };
}

function sessionReady(
  view: ReturnType<typeof seatView> | null,
  revision = view?.revision ?? 0,
) {
  return {
    type: "session.ready",
    server_time: "2026-09-25T00:00:00.000Z",
    snapshot: {
      room_id: "room-1",
      room_code: "ABCDEF",
      revision,
      outbox_seq: 0,
      public_view: publicView(revision),
      seat_view: view,
      host_control: null,
    },
  };
}

function NavigationProbe() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate("/play/BBBBBB")}>
      换房间
    </button>
  );
}

function renderHome() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

function renderPlayerRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/join/:roomCode?" element={<PlayerRoute />} />
        <Route path="/play/:roomCode" element={<PlayerRoute />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function openLatestSocket(
  message: unknown = sessionReady(null),
): Promise<FakeWebSocket> {
  await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
  const socket = FakeWebSocket.instances[0];
  await act(async () => {
    socket.open();
    socket.message(message);
  });
  return socket;
}

function commandFrames(socket: FakeWebSocket): Array<Record<string, unknown>> {
  return socket.sent
    .map((raw) => JSON.parse(raw) as Record<string, unknown>)
    .filter((frame) => frame.type === "command");
}

function commandId(frame: Record<string, unknown>): string {
  return (frame.command as { command_id: string }).command_id;
}

function commandAck(
  id: string,
  accepted = true,
  errorCode: string | null = null,
) {
  return {
    type: "command.ack",
    command_id: id,
    accepted,
    revision: accepted ? 2 : 1,
    error_code: errorCode,
    outbox_seq: 1,
  };
}

function seatViewUpdate(view: ReturnType<typeof seatView>, outboxSeq = 1) {
  return {
    type: "seat.view.updated",
    server_time: "2026-09-25T00:00:00.000Z",
    outbox_seq: outboxSeq,
    seat_id: view.seat_id,
    seat_view: view,
  };
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

describe("player entry flow", () => {
  it("creates a room and persists only the host session", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(
      jsonResponse(201, {
        room_id: "room-1",
        room_code: "ABCDEF",
        host_token: "host-token",
        expires_at: "2099-01-01T00:00:00.000Z",
      }),
    );

    renderHome();
    await user.type(screen.getByLabelText("主持人名字"), "主持人");
    await user.click(screen.getByRole("button", { name: "创建房间" }));

    expect(await screen.findByText("房间码 ABCDEF")).toBeVisible();
    expect(readHostSession("ABCDEF")?.token).toBe("host-token");
    expect(readSeatSession("ABCDEF")).toBeNull();
  });

  it("joins a room and sends JOIN_ROOM", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(
      jsonResponse(201, {
        room_id: "room-1",
        seat_id: 1,
        seat_token: "seat-token",
        expires_at: "2099-01-01T00:00:00.000Z",
      }),
    );

    renderPlayerRoute("/join/abcdef");
    expect(screen.getByLabelText("房间码")).toHaveValue("ABCDEF");
    await user.type(screen.getByLabelText("名字"), "3号");
    await user.click(screen.getByRole("button", { name: "加入房间" }));

    const socket = await openLatestSocket(
      sessionReady(preJoinSeatView(), 0),
    );

    expect(await screen.findByText("正在加入房间")).toBeVisible();
    expect(screen.queryByText("你已出局")).not.toBeInTheDocument();
    expect(socket.sentLast()).toMatchObject({
      type: "command",
      command: {
        payload: {
          command_type: "JOIN_ROOM",
          seat_id: 1,
          display_name: "3号",
        },
      },
    });
    expect(readSeatSession("ABCDEF")?.token).toBe("seat-token");

    const joinFrame = commandFrames(socket)[0];
    await act(async () => {
      socket.message(commandAck(commandId(joinFrame)));
    });

    expect(await screen.findByText("等待其他玩家")).toBeVisible();
  });

  it("retries JOIN_ROOM until the server acknowledges it", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(
      jsonResponse(201, {
        room_id: "room-1",
        seat_id: 1,
        seat_token: "seat-token",
        expires_at: "2099-01-01T00:00:00.000Z",
      }),
    );

    renderPlayerRoute("/join/ABCDEF");
    await user.type(screen.getByLabelText("名字"), "3号");
    await user.click(screen.getByRole("button", { name: "加入房间" }));
    const socket = await openLatestSocket(
      sessionReady(preJoinSeatView(), 0),
    );

    const first = commandFrames(socket)[0];
    expect(commandFrames(socket)).toHaveLength(1);
    await act(async () => {
      socket.message(seatViewUpdate(preJoinSeatView(1)));
    });

    expect(commandFrames(socket)).toHaveLength(1);
    await act(async () => {
      socket.message(
        commandAck(commandId(first), false, "REVISION_CONFLICT"),
      );
    });

    expect(commandFrames(socket)).toHaveLength(2);
    const retry = commandFrames(socket)[1];
    await act(async () => {
      socket.message(commandAck(commandId(retry)));
      socket.message(seatViewUpdate(seatView(2), 2));
    });

    expect(commandFrames(socket)).toHaveLength(2);
    expect(await screen.findByText("等待其他玩家")).toBeVisible();
  });

  it("resumes JOIN_ROOM after a refresh before acknowledgement", async () => {
    localStorage.setItem("werewolf:v1:room:ABCDEF:seat-name", "3号");
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "ABCDEF",
      roomId: "room-1",
      seatId: 1,
      token: "seat-token",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });

    renderPlayerRoute("/play/ABCDEF");
    const socket = await openLatestSocket(
      sessionReady(preJoinSeatView(), 0),
    );

    expect(socket.sentLast()).toMatchObject({
      type: "command",
      command: {
        payload: {
          command_type: "JOIN_ROOM",
          seat_id: 1,
          display_name: "3号",
        },
      },
    });
  });

  it("toggles SET_READY and renders the acknowledged ready state", async () => {
    const user = userEvent.setup();
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "ABCDEF",
      roomId: "room-1",
      seatId: 1,
      token: "seat-token",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });

    renderPlayerRoute("/play/ABCDEF");
    const socket = await openLatestSocket(sessionReady(seatView(3), 3));

    const readyButton = await screen.findByRole("button", { name: "准备" });
    expect(screen.getByText("房间 ABCDEF")).toBeVisible();
    expect(screen.getByText("座位 1")).toBeVisible();
    expect(screen.getByText("已连接")).toBeVisible();

    await user.click(readyButton);

    const readyFrame = commandFrames(socket)[0];

    expect(socket.sentLast()).toMatchObject({
      type: "command",
      command: {
        expected_revision: 3,
        payload: {
          command_type: "SET_READY",
          ready: true,
        },
      },
    });

    await act(async () => {
      socket.message(commandAck(commandId(readyFrame)));
    });

    expect(await screen.findByText("已准备")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "取消准备" }));

    expect(socket.sentLast()).toMatchObject({
      type: "command",
      command: {
        payload: {
          command_type: "SET_READY",
          ready: false,
        },
      },
    });
  });

  it("generates command ids without secure-context randomUUID", async () => {
    const user = userEvent.setup();
    let randomValue = 1;
    vi.stubGlobal("crypto", {
      getRandomValues(array: Uint8Array) {
        return array.fill(randomValue++);
      },
    });
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "ABCDEF",
      roomId: "room-1",
      seatId: 1,
      token: "seat-token",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });

    renderPlayerRoute("/play/ABCDEF");
    const socket = await openLatestSocket(sessionReady(seatView(1), 1));
    await user.click(await screen.findByRole("button", { name: "准备" }));

    const frame = commandFrames(socket)[0];
    expect(commandId(frame)).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });

  it.each([
    [
      "WOLF_NOMINATE_KILL",
      "WEREWOLF",
      [2, 3],
      3,
      { command_type: "WOLF_NOMINATE_KILL", target_seat_id: 3 },
    ],
    [
      "SEER_INSPECT",
      "SEER",
      [2, 4],
      4,
      { command_type: "SEER_INSPECT", target_seat_id: 4 },
    ],
    [
      "WITCH_USE_ANTIDOTE",
      "WITCH",
      [],
      null,
      { command_type: "WITCH_USE_ANTIDOTE" },
    ],
    [
      "WITCH_USE_POISON",
      "WITCH",
      [3],
      3,
      { command_type: "WITCH_USE_POISON", target_seat_id: 3 },
    ],
    [
      "WITCH_SKIP",
      "WITCH",
      [],
      null,
      { command_type: "WITCH_SKIP" },
    ],
  ])(
    "sends the exact %s command from the player route",
    async (action, role, targetSeatIds, selectedTarget, payload) => {
      const user = userEvent.setup();
      writeSeatSession({
        schemaVersion: 1,
        roomCode: "ABCDEF",
        roomId: "room-1",
        seatId: 1,
        token: "seat-token",
        expiresAt: "2099-01-01T00:00:00.000Z",
      });

      renderPlayerRoute("/play/ABCDEF");
      const socket = await openLatestSocket(
        sessionReady(
          nightSeatView({
            action: action as CommandType,
            role: role as string,
            targetSeatIds: targetSeatIds as number[],
          }),
          12,
        ),
      );

      if (selectedTarget !== null) {
        await user.click(
          await screen.findByTestId(
            `night-target-${String(selectedTarget)}`,
          ),
        );
      } else {
        await user.click(screen.getByText(
          action === "WITCH_USE_ANTIDOTE" ? "使用解药" : "跳过行动",
        ));
      }
      await user.click(screen.getByRole("button", { name: "确认行动" }));

      expect(socket.sentLast()).toMatchObject({
        type: "command",
        command: {
          room_id: "room-1",
          expected_revision: 12,
          payload,
        },
      });
    },
  );

  it("disables duplicate night submissions until the server acknowledges", async () => {
    const user = userEvent.setup();
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "ABCDEF",
      roomId: "room-1",
      seatId: 1,
      token: "seat-token",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });

    renderPlayerRoute("/play/ABCDEF");
    const socket = await openLatestSocket(
      sessionReady(
        nightSeatView({
          action: "SEER_INSPECT",
          role: "SEER",
          targetSeatIds: [2],
        }),
        12,
      ),
    );

    await user.click(await screen.findByTestId("night-target-2"));
    const confirm = screen.getByRole("button", { name: "确认行动" });
    await user.click(confirm);
    await user.click(confirm);

    expect(commandFrames(socket)).toHaveLength(1);
    expect(confirm).toBeDisabled();

    await act(async () => {
      socket.message(commandAck(commandId(commandFrames(socket)[0]), true));
    });

    expect(confirm).toBeEnabled();
  });

  it("retries a night action with the latest revision after a conflict", async () => {
    const user = userEvent.setup();
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "ABCDEF",
      roomId: "room-1",
      seatId: 1,
      token: "seat-token",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });

    renderPlayerRoute("/play/ABCDEF");
    const socket = await openLatestSocket(
      sessionReady(
        nightSeatView({
          action: "SEER_INSPECT",
          role: "SEER",
          targetSeatIds: [2],
        }),
        12,
      ),
    );

    await user.click(await screen.findByTestId("night-target-2"));
    await user.click(screen.getByRole("button", { name: "确认行动" }));
    const first = commandFrames(socket)[0];

    await act(async () => {
      socket.message(
        seatViewUpdate(
          nightSeatView({
            action: "SEER_INSPECT",
            role: "SEER",
            targetSeatIds: [2],
            revision: 13,
          }),
          2,
        ),
      );
      socket.message(
        commandAck(commandId(first), false, "REVISION_CONFLICT"),
      );
    });

    expect(commandFrames(socket)).toHaveLength(2);
    expect(socket.sentLast()).toMatchObject({
      type: "command",
      command: {
        expected_revision: 13,
        payload: { command_type: "SEER_INSPECT", target_seat_id: 2 },
      },
    });
  });

  it("does not retry a stale night action that the latest view removed", async () => {
    const user = userEvent.setup();
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "ABCDEF",
      roomId: "room-1",
      seatId: 1,
      token: "seat-token",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });

    renderPlayerRoute("/play/ABCDEF");
    const socket = await openLatestSocket(
      sessionReady(
        nightSeatView({
          action: "SEER_INSPECT",
          role: "SEER",
          targetSeatIds: [2],
        }),
        12,
      ),
    );

    await user.click(await screen.findByTestId("night-target-2"));
    await user.click(screen.getByRole("button", { name: "确认行动" }));
    const first = commandFrames(socket)[0];
    const latestView = {
      ...nightSeatView({
        action: "SEER_INSPECT",
        role: "SEER",
        targetSeatIds: [],
        revision: 13,
      }),
      phase: "NIGHT_WITCH",
      legal_actions: [],
    };

    await act(async () => {
      socket.message(seatViewUpdate(latestView, 2));
      socket.message(
        commandAck(commandId(first), false, "REVISION_CONFLICT"),
      );
    });

    expect(commandFrames(socket)).toHaveLength(1);
    expect(
      await screen.findByText("行动已失效，请重新选择"),
    ).toBeVisible();
  });

  it("does not send a target action before a target is selected", async () => {
    const user = userEvent.setup();
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "ABCDEF",
      roomId: "room-1",
      seatId: 1,
      token: "seat-token",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });

    renderPlayerRoute("/play/ABCDEF");
    const socket = await openLatestSocket(
      sessionReady(
        nightSeatView({
          action: "SEER_INSPECT",
          role: "SEER",
          targetSeatIds: [2],
        }),
        12,
      ),
    );

    const confirm = await screen.findByRole("button", { name: "确认行动" });
    expect(confirm).toBeDisabled();
    await user.click(confirm);

    expect(commandFrames(socket)).toHaveLength(0);
  });

  it.each([
    ["ROOM_FULL", 409, "房间已满"],
    ["ROOM_NOT_FOUND", 404, "房间不存在"],
  ])("shows %s without opening a socket", async (code, status, message) => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(
      jsonResponse(status, {
        code,
        message: "server text must not render",
        request_id: "request-1",
      }),
    );

    renderPlayerRoute("/join/ABCDEF");
    await user.type(screen.getByLabelText("名字"), "玩家");
    await user.click(screen.getByRole("button", { name: "加入房间" }));

    expect(await screen.findByText(message)).toBeVisible();
    expect(screen.queryByText("server text must not render")).not.toBeInTheDocument();
    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(readSeatSession("ABCDEF")).toBeNull();
  });

  it("offers rejoin when a player route has no valid seat session", () => {
    renderPlayerRoute("/play/ABCDEF");

    expect(screen.getByText("会话已过期，请重新加入")).toBeVisible();
    expect(screen.getByRole("button", { name: "加入房间" })).toBeVisible();
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("clears an expired seat session after close code 4001", async () => {
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "ABCDEF",
      roomId: "room-1",
      seatId: 1,
      token: "seat-token",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });

    renderPlayerRoute("/play/ABCDEF");
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    await act(async () => {
      FakeWebSocket.instances[0].close(4001, "session expired");
    });

    expect(await screen.findByText("会话已过期，请重新加入")).toBeVisible();
    expect(localStorage.getItem("werewolf:v1:room:ABCDEF:seat")).toBeNull();
  });

  it("reloads the seat session when only the route room code changes", async () => {
    const user = userEvent.setup();
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "AAAAAA",
      roomId: "room-a",
      seatId: 1,
      token: "seat-token-a",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "BBBBBB",
      roomId: "room-b",
      seatId: 2,
      token: "seat-token-b",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });

    render(
      <MemoryRouter initialEntries={["/play/AAAAAA"]}>
        <NavigationProbe />
        <AppRoutes />
      </MemoryRouter>,
    );

    expect(await screen.findByText("房间 AAAAAA")).toBeVisible();
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "BBBBBB",
      roomId: "room-b",
      seatId: 2,
      token: "seat-token-b",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });
    await user.click(screen.getByRole("button", { name: "换房间" }));

    expect(await screen.findByText("房间 BBBBBB")).toBeVisible();
    expect(screen.getByText("座位 2")).toBeVisible();
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("requires a display name of at most 24 characters", async () => {
    const user = userEvent.setup();
    renderPlayerRoute("/join/ABCDEF");

    const submit = screen.getByRole("button", { name: "加入房间" });
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText("名字"), "a".repeat(25));
    expect(submit).toBeDisabled();

    await user.clear(screen.getByLabelText("名字"));
    await user.type(screen.getByLabelText("名字"), "玩家");
    expect(submit).toBeEnabled();
  });
});
