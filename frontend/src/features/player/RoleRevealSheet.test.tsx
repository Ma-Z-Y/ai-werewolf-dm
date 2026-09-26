import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PrivateFact, SeatView } from "../../protocol/models";
import { writeSeatSession } from "../../session/storage";

import { PlayerRoute } from "./PlayerRoute";
import { RoleRevealSheet } from "./RoleRevealSheet";
import { SeatShell } from "./SeatShell";

const wolfTeamFact: PrivateFact = {
  fact_id: "fact-wolf-team",
  event_id: "event-role-assignment",
  recipient_seat_id: 1,
  revision: 12,
  fact_type: "WOLF_TEAM",
  payload: { seat_ids: [1, 2] },
};

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

function publicView(revision = 1) {
  return {
    schema_version: "public-view.v1",
    room_id: "room-1",
    revision,
    phase: "ROLE_REVEAL",
    day: 1,
    living_seats: [1, 2, 3, 4, 5, 6],
    public_timeline: [
      {
        event_id: "event-phase",
        revision,
        event_type: "PHASE_CHANGED",
        statement: "角色揭示阶段开始",
      },
    ],
    vote_summary: null,
    deadline_at: "2099-01-01T00:02:00.000Z",
    paused: false,
  };
}

function roleRevealSeatView(revision = 12): SeatView {
  return {
    ...publicView(revision),
    schema_version: "seat-view.v1",
    seat_id: 1,
    role: "WEREWOLF",
    private_facts: [wolfTeamFact],
    legal_actions: [
      {
        action: "CONFIRM_ROLE",
        target_seat_ids: [],
        deadline_at: "2099-01-01T00:02:00.000Z",
      },
    ],
  };
}

function sessionReady(view: SeatView) {
  return {
    type: "session.ready",
    server_time: "2026-09-25T00:00:00.000Z",
    snapshot: {
      room_id: view.room_id,
      room_code: "ABCDEF",
      revision: view.revision,
      outbox_seq: 0,
      public_view: {
        ...publicView(view.revision),
        schema_version: "public-view.v1",
      },
      seat_view: view,
      host_control: null,
    },
  };
}

function commandFrames(socket: FakeWebSocket): Array<Record<string, unknown>> {
  return socket.sent
    .map((raw) => JSON.parse(raw) as Record<string, unknown>)
    .filter((frame) => frame.type === "command");
}

function commandId(frame: Record<string, unknown>): string {
  return (frame.command as { command_id: string }).command_id;
}

function renderPlayerRoute() {
  return render(
    <MemoryRouter initialEntries={["/play/ABCDEF"]}>
      <Routes>
        <Route path="/play/:roomCode" element={<PlayerRoute />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function openLatestSocket(message: unknown): Promise<FakeWebSocket> {
  await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
  const socket = FakeWebSocket.instances[0];
  await act(async () => {
    socket.open();
    socket.message(message);
  });
  return socket;
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("RoleRevealSheet", () => {
  it("does not mount private role or facts before reveal", () => {
    render(
      <RoleRevealSheet role="WEREWOLF" privateFacts={[wolfTeamFact]} />,
    );

    expect(screen.queryByText("狼人")).not.toBeInTheDocument();
    expect(screen.queryByText("狼人队友：1、2 号")).not.toBeInTheDocument();
  });

  it("mounts private content on pointer down and unmounts on pointer up", () => {
    render(<RoleRevealSheet role="WEREWOLF" />);
    const reveal = screen.getByRole("button", { name: "按住查看身份" });

    fireEvent.pointerDown(reveal);

    expect(screen.getByText("狼人")).toBeVisible();
    fireEvent.pointerUp(window);
    expect(screen.queryByText("狼人")).not.toBeInTheDocument();

    fireEvent.click(reveal, { detail: 1 });
    expect(screen.queryByText("狼人")).not.toBeInTheDocument();
  });

  it("unmounts private content on pointer cancel", () => {
    render(<RoleRevealSheet role="SEER" />);
    fireEvent.pointerDown(
      screen.getByRole("button", { name: "按住查看身份" }),
    );
    expect(screen.getByText("预言家")).toBeVisible();

    fireEvent.pointerCancel(window);

    expect(screen.queryByText("预言家")).not.toBeInTheDocument();
  });

  it("unmounts private content when the window loses focus", () => {
    render(<RoleRevealSheet role="WEREWOLF" revealed />);
    expect(screen.getByText("狼人")).toBeVisible();

    fireEvent.blur(window);

    expect(screen.queryByText("狼人")).not.toBeInTheDocument();
  });

  it("unmounts private content when the page becomes hidden", () => {
    const visibilityState = vi
      .spyOn(document, "visibilityState", "get")
      .mockReturnValue("hidden");
    render(<RoleRevealSheet role="WEREWOLF" revealed />);
    expect(screen.getByText("狼人")).toBeVisible();

    fireEvent(document, new Event("visibilitychange"));

    expect(screen.queryByText("狼人")).not.toBeInTheDocument();
    visibilityState.mockRestore();
  });

  it("supports keyboard reveal while the key is held", () => {
    render(<RoleRevealSheet role="SEER" />);
    const reveal = screen.getByRole("button", { name: "按住查看身份" });
    reveal.focus();

    fireEvent.keyDown(reveal, { key: "Enter" });

    expect(screen.getByText("预言家")).toBeVisible();

    fireEvent.keyUp(reveal, { key: "Enter" });

    expect(screen.queryByText("预言家")).not.toBeInTheDocument();
  });

  it("supports assistive-technology click activation", () => {
    render(<RoleRevealSheet role="WITCH" />);
    const reveal = screen.getByRole("button", { name: "按住查看身份" });

    fireEvent.click(reveal);
    expect(screen.getByText("女巫")).toBeVisible();

    fireEvent.click(reveal);
    expect(screen.queryByText("女巫")).not.toBeInTheDocument();
  });

  it("unmounts private content when the reveal control loses focus", () => {
    render(<RoleRevealSheet role="WEREWOLF" />);
    const reveal = screen.getByRole("button", { name: "按住查看身份" });
    fireEvent.keyDown(reveal, { key: " " });
    expect(screen.getByText("狼人")).toBeVisible();

    fireEvent.blur(reveal);

    expect(screen.queryByText("狼人")).not.toBeInTheDocument();
  });

  it("removes private content when unmounted", () => {
    const { unmount } = render(<RoleRevealSheet role="WEREWOLF" revealed />);
    expect(screen.getByText("狼人")).toBeVisible();

    unmount();

    expect(screen.queryByText("狼人")).not.toBeInTheDocument();
  });
});

describe("SeatShell", () => {
  it("renders public state and reveals private facts only while requested", () => {
    const onAction = vi.fn();
    const seatView = roleRevealSeatView();

    render(
      <SeatShell
        actionConfirmed={false}
        connectionState="ready"
        errorMessage={null}
        onAction={onAction}
        ready={false}
        roomCode="ABCDEF"
        seatId={1}
        seatView={seatView}
        actionPending={false}
      />,
    );

    expect(screen.getByText("角色揭示")).toBeVisible();
    expect(screen.getByText("角色揭示阶段开始")).toBeVisible();
    expect(screen.getByText("座位 1")).toBeVisible();
    expect(screen.getByText("存活")).toBeVisible();
    expect(screen.getByText("已开局")).toBeVisible();
    expect(screen.getByRole("status").tagName).toBe("SPAN");
    expect(screen.queryByText("狼人")).not.toBeInTheDocument();

    fireEvent.pointerDown(
      screen.getByRole("button", { name: "按住查看身份" }),
    );
    expect(screen.getByText("狼人")).toBeVisible();
    expect(screen.getByText("狼人队友：1、2 号")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "确认身份" }));
    expect(onAction).toHaveBeenCalledWith(seatView.legal_actions[0]);
  });

  it("does not render an unimplemented legal action as an active button", () => {
    const onAction = vi.fn();
    const seatView: SeatView = {
      ...roleRevealSeatView(),
      phase: "NIGHT_WOLF",
      legal_actions: [
        {
          action: "WOLF_NOMINATE_KILL",
          target_seat_ids: [2],
          deadline_at: "2099-01-01T00:01:00.000Z",
        },
      ],
    };

    render(
      <SeatShell
        actionConfirmed={false}
        actionPending={false}
        connectionState="ready"
        errorMessage={null}
        onAction={onAction}
        ready={true}
        roomCode="ABCDEF"
        seatId={1}
        seatView={seatView}
      />,
    );

    expect(
      screen.queryByRole("button", { name: "执行行动" }),
    ).not.toBeInTheDocument();
    expect(onAction).not.toHaveBeenCalled();
  });

  it("sends CONFIRM_ROLE through PlayerRoute", async () => {
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "ABCDEF",
      roomId: "room-1",
      seatId: 1,
      token: "seat-token",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });

    renderPlayerRoute();
    const socket = await openLatestSocket(sessionReady(roleRevealSeatView()));

    fireEvent.click(await screen.findByRole("button", { name: "确认身份" }));

    const frame = commandFrames(socket)[0];
    expect(frame).toMatchObject({
      type: "command",
      command: {
        expected_revision: 12,
        payload: { command_type: "CONFIRM_ROLE" },
      },
    });
    await act(async () => {
      socket.message({
        type: "command.ack",
        command_id: commandId(frame),
        accepted: true,
        revision: 13,
        error_code: null,
        outbox_seq: 1,
      });
    });
    await waitFor(() => {
      expect(screen.queryByText("处理中")).not.toBeInTheDocument();
    });
  });

  it("does not resend CONFIRM_ROLE while pending and recovers after rejection", async () => {
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "ABCDEF",
      roomId: "room-1",
      seatId: 1,
      token: "seat-token",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });

    renderPlayerRoute();
    const socket = await openLatestSocket(sessionReady(roleRevealSeatView()));
    const confirm = await screen.findByRole("button", { name: "确认身份" });

    fireEvent.click(confirm);
    fireEvent.click(confirm);

    expect(commandFrames(socket)).toHaveLength(1);
    const first = commandFrames(socket)[0];
    await act(async () => {
      socket.message({
        type: "command.ack",
        command_id: commandId(first),
        accepted: false,
        revision: 12,
        error_code: "ILLEGAL_PHASE",
        outbox_seq: 0,
      });
    });

    expect(
      await screen.findByText("当前阶段不允许该操作"),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认身份" }));
    expect(commandFrames(socket)).toHaveLength(2);
  });
});
