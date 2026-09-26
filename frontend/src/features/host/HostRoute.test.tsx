import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "../../app/routes";
import type { HostControlView } from "../../protocol/models";
import {
  readHostSession,
  writeHostSession,
} from "../../session/storage";

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
let capturedBlob: Blob | null = null;
const createObjectURL = vi.fn((blob: Blob) => {
  capturedBlob = blob;
  return "blob:host-audit";
});
const revokeObjectURL = vi.fn();
let anchorClick: ReturnType<typeof vi.spyOn>;

function jsonResponse(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(payload),
  } as unknown as Response;
}

function publicView(revision: number) {
  return {
    schema_version: "public-view.v1" as const,
    room_id: "room-1",
    revision,
    phase: "DAY_DISCUSSION",
    day: 1,
    living_seats: [1, 2, 3, 4, 5, 6],
    public_timeline: [],
    vote_summary: null,
    deadline_at: null,
    paused: false,
    paused_at: null,
  };
}

function hostControl(paused = false, revision = 7): HostControlView {
  return {
    public_view: {
      ...publicView(revision),
      paused,
      paused_at: paused ? "2026-09-26T00:00:00.000Z" : null,
    },
    paused,
    revision,
  };
}

function sessionReady(control = hostControl()) {
  return {
    type: "session.ready",
    server_time: "2026-09-26T00:00:00.000Z",
    snapshot: {
      room_id: "room-1",
      room_code: "ABCDEF",
      revision: control.revision,
      outbox_seq: 0,
      public_view: control.public_view,
      seat_view: null,
      host_control: control,
    },
  };
}

function hostControlUpdate(control: HostControlView) {
  return {
    type: "host.control.updated",
    server_time: "2026-09-26T00:00:01.000Z",
    outbox_seq: control.revision,
    host_control: control,
  };
}

function commandAck(commandId: string, revision: number) {
  return {
    type: "command.ack",
    command_id: commandId,
    accepted: true,
    revision,
    error_code: null,
    outbox_seq: revision,
  };
}

function writeHostSessionForRoom(token = "host-token"): void {
  writeHostSession({
    schemaVersion: 1,
    roomCode: "ABCDEF",
    roomId: "room-1",
    token,
    expiresAt: "2099-01-01T00:00:00.000Z",
  });
}

function renderHost(path = "/host/ABCDEF") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

async function openHostSocket(
  control: HostControlView = hostControl(),
): Promise<FakeWebSocket> {
  expect(globalThis.WebSocket).toBe(FakeWebSocket);
  expect(screen.getByText("正在连接主持人控制台")).toBeVisible();
  await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
  const socket = FakeWebSocket.instances[0];
  await act(async () => {
    socket.open();
    socket.message(sessionReady(control));
  });
  return socket;
}

function parsedFrames(socket: FakeWebSocket): Array<Record<string, unknown>> {
  return socket.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>);
}

function lastCommand(
  socket: FakeWebSocket,
): Record<string, unknown> | undefined {
  return parsedFrames(socket).at(-1) as Record<string, unknown> | undefined;
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  fetchMock.mockReset();
  FakeWebSocket.reset();
  capturedBlob = null;
  createObjectURL.mockClear();
  revokeObjectURL.mockClear();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("WebSocket", FakeWebSocket);
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: createObjectURL,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: revokeObjectURL,
  });
  anchorClick = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  anchorClick.mockRestore();
  vi.unstubAllGlobals();
});

describe("host control console", () => {
  it("sends HOST_PAUSE and HOST_RESUME with the host session", async () => {
    writeHostSessionForRoom();
    renderHost();
    const socket = await openHostSocket();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "暂停游戏" }));

    const pauseFrame = lastCommand(socket);
    expect(pauseFrame).toMatchObject({
      type: "command",
      command: {
        room_id: "room-1",
        expected_revision: 7,
        payload: {
          command_type: "HOST_PAUSE",
          reason: "主持人暂停",
        },
      },
    });
    const pauseCommandId = (
      pauseFrame?.command as { command_id: string }
    ).command_id;

    await act(async () => {
      socket.message(commandAck(pauseCommandId, 8));
      socket.message(hostControlUpdate(hostControl(true, 8)));
    });
    await user.click(screen.getByRole("button", { name: "恢复游戏" }));

    expect(lastCommand(socket)).toMatchObject({
      type: "command",
      command: {
        room_id: "room-1",
        expected_revision: 8,
        payload: {
          command_type: "HOST_RESUME",
        },
      },
    });
  });

  it("explains that correction is unavailable without fake controls", async () => {
    writeHostSessionForRoom();
    renderHost();
    await openHostSocket();

    expect(
      screen.getByText("主持人纠错尚未实现，请结束并重开一局。"),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /修正|回退/ }),
    ).not.toBeInTheDocument();
  });

  it("creates and revokes a display pairing", async () => {
    writeHostSessionForRoom();
    fetchMock.mockImplementation(async (input, init) => {
      const path = String(input);
      if (
        path === "/rooms/ABCDEF/display-pairings" &&
        init?.method === "POST"
      ) {
        return jsonResponse(201, {
          pairing_code: "482913",
          expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
          expires_in_seconds: 3600,
        });
      }
      if (
        path === "/rooms/ABCDEF/display-sessions/current" &&
        init?.method === "DELETE"
      ) {
        return jsonResponse(204, null);
      }
      throw new Error(`Unexpected request: ${init?.method} ${path}`);
    });
    renderHost();
    await openHostSocket();
    const user = userEvent.setup();

    await user.click(
      screen.getByRole("button", { name: "生成共享屏配对码" }),
    );

    expect(await screen.findByText("482913")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      "/rooms/ABCDEF/display-pairings",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: "Bearer host-token",
        }),
      }),
    );

    await user.click(screen.getByRole("button", { name: "撤销共享屏" }));

    expect(fetchMock).toHaveBeenCalledWith(
      "/rooms/ABCDEF/display-sessions/current",
      expect.objectContaining({
        method: "DELETE",
        headers: expect.objectContaining({
          Authorization: "Bearer host-token",
        }),
      }),
    );
  });

  it("downloads host audit without putting the token in the URL", async () => {
    writeHostSessionForRoom();
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        room_id: "room-1",
        revision: 7,
        state: { phase: "DAY_DISCUSSION" },
        raw_events: [],
        dm_trace: [],
        snapshots: [],
      }),
    );
    renderHost();
    const socket = await openHostSocket();
    const user = userEvent.setup();

    await user.click(
      screen.getByRole("button", { name: "下载主持人审计" }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "/rooms/ABCDEF/audit",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer host-token",
        }),
      }),
    );
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(capturedBlob).not.toBeNull();
    expect(await capturedBlob?.text()).not.toContain("host-token");
    const downloadUrl = createObjectURL.mock.results[0]?.value;
    expect(downloadUrl).not.toContain("host-token");
    expect(revokeObjectURL).toHaveBeenCalledWith(downloadUrl);
    expect(anchorClick).toHaveBeenCalledTimes(1);
    expect(socket.url).not.toContain("host-token");
    expect(window.location.href).not.toContain("host-token");
  });

  it("does not finish an in-flight audit after another device takes over", async () => {
    writeHostSessionForRoom();
    let resolveAudit: ((response: Response) => void) | null = null;
    fetchMock.mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveAudit = resolve;
        }),
    );
    renderHost();
    const socket = await openHostSocket();
    const user = userEvent.setup();

    await user.click(
      screen.getByRole("button", { name: "下载主持人审计" }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await act(async () => {
      socket.close(4003, "replaced");
      resolveAudit?.(
        jsonResponse(200, {
          room_id: "room-1",
          revision: 7,
          state: { phase: "DAY_DISCUSSION" },
          raw_events: [],
          dm_trace: [],
          snapshots: [],
        }),
      );
    });

    expect(createObjectURL).not.toHaveBeenCalled();
    expect(anchorClick).not.toHaveBeenCalled();
  });

  it("shows connection, room, revision, and audit diagnostics", async () => {
    writeHostSessionForRoom();
    renderHost();
    await openHostSocket(hostControl(false, 12));

    expect(screen.getByText("房间 ABCDEF")).toBeVisible();
    expect(screen.getByText("已连接")).toBeVisible();
    expect(screen.getByTestId("host-revision")).toHaveTextContent("12");
    expect(
      screen.getByRole("button", { name: "下载主持人审计" }),
    ).toBeVisible();
  });

  it("clears an expired host session", async () => {
    writeHostSessionForRoom();
    renderHost();
    const socket = await openHostSocket();

    await act(async () => {
      socket.close(4001, "expired");
    });

    expect(readHostSession("ABCDEF")).toBeNull();
    expect(
      screen.getByText("主持人会话已失效，请重新创建房间。"),
    ).toBeVisible();
  });

  it("shows safe server errors for rejected commands and socket errors", async () => {
    writeHostSessionForRoom();
    renderHost();
    const socket = await openHostSocket();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "暂停游戏" }));
    const commandId = (
      lastCommand(socket)?.command as { command_id: string }
    ).command_id;

    await act(async () => {
      socket.message({
        type: "command.ack",
        command_id: commandId,
        accepted: false,
        revision: 7,
        error_code: "RATE_LIMITED",
        outbox_seq: 7,
      });
    });
    expect(screen.getByText("请求过于频繁")).toBeVisible();

    await act(async () => {
      socket.message({
        type: "error",
        code: "INTERNAL_ERROR",
        message: "internal detail must not render",
        request_id: "request-1",
      });
    });
    expect(screen.getByText("服务器内部错误")).toBeVisible();
    expect(
      screen.queryByText("internal detail must not render"),
    ).not.toBeInTheDocument();
  });

  it("stops the old host console when another device takes over", async () => {
    writeHostSessionForRoom();
    renderHost();
    const socket = await openHostSocket();

    await act(async () => {
      socket.close(4003, "replaced");
    });

    expect(
      screen.getByText("主持人控制台已在其他设备接管，请继续使用新设备。"),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "暂停游戏" }),
    ).not.toBeInTheDocument();
    expect(
      globalThis.localStorage.getItem(
        "werewolf:v1:room:ABCDEF:host",
      ),
    ).not.toBeNull();
    expect(readHostSession("ABCDEF")).toBeNull();
    globalThis.sessionStorage.clear();
    expect(readHostSession("ABCDEF")?.token).toBe("host-token");
  });

  it("expires a display pairing after its server TTL", async () => {
    writeHostSessionForRoom();
    const expiresAt = new Date(Date.now() + 300).toISOString();
    fetchMock.mockResolvedValue(
      jsonResponse(201, {
        pairing_code: "482913",
        expires_at: expiresAt,
        expires_in_seconds: 1,
      }),
    );
    renderHost();
    await openHostSocket();
    const user = userEvent.setup();

    await user.click(
      screen.getByRole("button", { name: "生成共享屏配对码" }),
    );
    expect(await screen.findByText("482913")).toBeVisible();

    await waitFor(
      () => {
        expect(screen.queryByText("482913")).not.toBeInTheDocument();
      },
      { timeout: 1000 },
    );
    expect(
      screen.getByText("配对码已失效，请重新生成。"),
    ).toBeVisible();
  });

  it("uses the server pairing TTL despite browser clock skew", async () => {
    writeHostSessionForRoom();
    const clientNow = Date.parse("2026-09-26T01:00:00.000Z");
    const nowSpy = vi.spyOn(Date, "now").mockReturnValue(clientNow);
    fetchMock.mockResolvedValue(
      jsonResponse(201, {
        pairing_code: "482913",
        expires_at: "2026-09-26T00:05:00.000Z",
        expires_in_seconds: 300,
      }),
    );
    renderHost();
    await openHostSocket();
    const user = userEvent.setup();

    await user.click(
      screen.getByRole("button", { name: "生成共享屏配对码" }),
    );

    expect(await screen.findByText("482913")).toBeVisible();
    expect(
      screen.queryByText("配对码已失效，请重新生成。"),
    ).not.toBeInTheDocument();
    nowSpy.mockRestore();
  });
});
