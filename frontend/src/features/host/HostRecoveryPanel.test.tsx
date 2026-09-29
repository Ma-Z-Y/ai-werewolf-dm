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
  writeHostSession,
  writeSeatSession,
} from "../../session/storage";

import { HostRecoveryPanel } from "./HostRecoveryPanel";

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
    paused: true,
    paused_at: "2026-09-29T00:00:00.000Z",
  };
}

function hostControl(revision = 7): HostControlView {
  return {
    public_view: publicView(revision),
    paused: true,
    revision,
  };
}

function sessionReady(control = hostControl()) {
  return {
    type: "session.ready",
    server_time: "2026-09-29T00:00:00.000Z",
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
    server_time: "2026-09-29T00:00:01.000Z",
    outbox_seq: control.revision,
    host_control: control,
  };
}

function writeHostSessionForRoom(): void {
  writeHostSession({
    schemaVersion: 1,
    roomCode: "ABCDEF",
    roomId: "room-1",
    token: "host-token",
    expiresAt: "2099-01-01T00:00:00.000Z",
  });
}

function renderHost() {
  return render(
    <MemoryRouter initialEntries={["/host/ABCDEF"]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

async function openHostSocket(
  control: HostControlView = hostControl(),
): Promise<FakeWebSocket> {
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

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("host recovery panel", () => {
  it("requires pause before sending a patch", async () => {
    const onSubmit = vi.fn();
    render(
      <HostRecoveryPanel
        onPatchSubmit={onSubmit}
        paused={false}
        pending={false}
      />,
    );
    const user = userEvent.setup();

    const submit = screen.getByRole("button", { name: "提交主持人纠错" });
    expect(submit).toBeDisabled();
    await user.click(submit);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("keeps the reason and pending action while waiting for ACK", async () => {
    const onSubmit = vi.fn();
    const view = render(
      <HostRecoveryPanel
        onPatchSubmit={onSubmit}
        paused
        pending={false}
      />,
    );
    const user = userEvent.setup();

    await user.selectOptions(screen.getByLabelText("修正类型"), "SET_ALIVE");
    await user.selectOptions(screen.getByLabelText("座位"), "2");
    await user.click(screen.getByRole("checkbox", { name: "存活" }));
    await user.type(screen.getByLabelText("确认原因"), "状态误标");
    await user.click(screen.getByRole("button", { name: "提交主持人纠错" }));

    expect(onSubmit).toHaveBeenCalledWith({
      command_type: "HOST_PATCH",
      patch: {
        patch_type: "SET_ALIVE",
        seat_id: 2,
        alive: false,
      },
    });

    view.rerender(
      <HostRecoveryPanel
        onPatchSubmit={onSubmit}
        paused
        pending
      />,
    );

    expect(screen.getByLabelText("确认原因")).toHaveValue("状态误标");
    expect(screen.getByLabelText("修正类型")).toHaveValue("SET_ALIVE");
    expect(screen.getByLabelText("座位")).toHaveValue("2");
    expect(screen.getByRole("checkbox", { name: "存活" })).not.toBeChecked();
    expect(screen.getByRole("button", { name: "正在提交" })).toBeDisabled();
  });

  it("uses the visible seer target after switching from vote", async () => {
    const onSubmit = vi.fn();
    render(
      <HostRecoveryPanel
        onPatchSubmit={onSubmit}
        paused
        pending={false}
      />,
    );
    const user = userEvent.setup();

    await user.selectOptions(screen.getByLabelText("修正类型"), "SET_VOTE");
    await user.selectOptions(screen.getByLabelText("投票目标"), "3");
    await user.selectOptions(
      screen.getByLabelText("修正类型"),
      "SET_SEER_CHECKS",
    );
    await user.selectOptions(screen.getByLabelText("查验目标"), "4");
    await user.type(screen.getByLabelText("确认原因"), "查验目标修正");
    await user.click(screen.getByRole("button", { name: "提交主持人纠错" }));

    expect(screen.getByLabelText("查验目标")).toHaveValue("4");
    expect(onSubmit).toHaveBeenCalledWith({
      command_type: "HOST_PATCH",
      patch: {
        patch_type: "SET_SEER_CHECKS",
        seer_seat_id: 1,
        checks: [
          {
            day: 0,
            target_seat_id: 4,
            faction: "GOOD",
          },
        ],
      },
    });
  });

  it("disables recovery submission while resume is pending", async () => {
    writeHostSessionForRoom();
    renderHost();
    const socket = await openHostSocket(hostControl(7));
    const user = userEvent.setup();

    await user.type(screen.getByLabelText("确认原因"), "状态误标");
    await user.click(screen.getByRole("button", { name: "恢复游戏" }));

    expect(parsedFrames(socket).at(-1)).toMatchObject({
      command: {
        payload: { command_type: "HOST_RESUME" },
      },
    });
    expect(
      screen.getByRole("button", { name: "提交主持人纠错" }),
    ).toBeDisabled();
  });

  it("retries a revision conflict with the latest host revision", async () => {
    writeHostSessionForRoom();
    renderHost();
    const socket = await openHostSocket();
    const user = userEvent.setup();

    await user.selectOptions(screen.getByLabelText("修正类型"), "SET_ALIVE");
    await user.selectOptions(screen.getByLabelText("座位"), "2");
    await user.click(screen.getByRole("checkbox", { name: "存活" }));
    await user.type(screen.getByLabelText("确认原因"), "状态误标");
    await user.click(screen.getByRole("button", { name: "提交主持人纠错" }));

    const firstFrame = parsedFrames(socket).at(-1);
    const firstCommand = firstFrame?.command as {
      command_id: string;
      expected_revision: number;
    };
    expect(firstCommand.expected_revision).toBe(7);

    await act(async () => {
      socket.message({
        type: "command.ack",
        command_id: firstCommand.command_id,
        accepted: false,
        revision: 8,
        error_code: "REVISION_CONFLICT",
        outbox_seq: 8,
      });
    });

    const retryFrame = parsedFrames(socket).at(-1);
    expect(retryFrame?.command).toMatchObject({
      expected_revision: 8,
      payload: {
        command_type: "HOST_PATCH",
        patch: {
          patch_type: "SET_ALIVE",
          seat_id: 2,
          alive: false,
        },
      },
    });
    await act(async () => {
      socket.message(hostControlUpdate(hostControl(8)));
    });
  });

  it("maps voided recovery commands to fixed safe copy", async () => {
    writeHostSessionForRoom();
    renderHost();
    const socket = await openHostSocket();
    const user = userEvent.setup();

    await user.selectOptions(screen.getByLabelText("修正类型"), "SET_POTION");
    await user.type(screen.getByLabelText("确认原因"), "药水状态误标");
    await user.click(screen.getByRole("button", { name: "提交主持人纠错" }));

    const frame = parsedFrames(socket).at(-1);
    const commandId = (frame?.command as { command_id: string }).command_id;
    await act(async () => {
      socket.message({
        type: "command.ack",
        command_id: commandId,
        accepted: false,
        revision: 7,
        error_code: "COMMAND_VOIDED_BY_REWIND",
        outbox_seq: 7,
      });
    });

    expect(screen.getByText("该操作已因回退失效")).toBeVisible();
    expect(
      screen.queryByText("COMMAND_VOIDED_BY_REWIND"),
    ).not.toBeInTheDocument();
  });

  it("does not render snapshot or diff data for a seat", () => {
    writeSeatSession({
      schemaVersion: 1,
      roomCode: "ABCDEF",
      roomId: "room-1",
      seatId: 1,
      token: "seat-token",
      expiresAt: "2099-01-01T00:00:00.000Z",
    });
    renderHost();

    expect(screen.queryByText("主持人纠错")).not.toBeInTheDocument();
    expect(screen.queryByText(/snapshot|diff|raw_events/i)).not.toBeInTheDocument();
    expect(screen.queryByText("seat-token")).not.toBeInTheDocument();
  });
});
