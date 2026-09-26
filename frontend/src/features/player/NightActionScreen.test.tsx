import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { LegalAction, SeatView } from "../../protocol/models";

import { NightActionScreen, nightCommand } from "./NightActionScreen";
import { SeatShell } from "./SeatShell";

function nightAction(
  action: LegalAction["action"],
  targetSeatIds: number[] = [],
): LegalAction {
  return {
    action,
    target_seat_ids: targetSeatIds,
    deadline_at: "2099-01-01T00:01:00.000Z",
  };
}

function renderNight({
  role,
  actions,
  onAction = vi.fn(),
}: {
  role: string | null;
  actions: LegalAction[];
  onAction?: (
    action: LegalAction,
    targetSeatId?: number | null,
  ) => void;
}) {
  return {
    onAction,
    ...render(
      <NightActionScreen
        actions={actions}
        onAction={onAction}
        role={role}
      />,
    ),
  };
}

function nightSeatView(
  role: string,
  actions: LegalAction[],
): SeatView {
  return {
    schema_version: "seat-view.v1",
    room_id: "room-1",
    revision: 12,
    phase: "NIGHT_WOLF",
    day: 1,
    living_seats: [1, 2, 3, 4, 5, 6],
    public_timeline: [],
    vote_summary: null,
    deadline_at: "2099-01-01T00:01:00.000Z",
    paused: false,
    paused_at: null,
    seat_id: 1,
    role,
    private_facts: [],
    legal_actions: actions,
  };
}

describe("NightActionScreen", () => {
  it("renders only legal seer targets", () => {
    renderNight({
      role: "SEER",
      actions: [nightAction("SEER_INSPECT", [2, 4])],
    });

    expect(screen.getByRole("button", { name: "查验 2 号" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "查验 4 号" })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "查验 3 号" }),
    ).not.toBeInTheDocument();
  });

  it("does not make antidote available when legal actions omit it", () => {
    renderNight({
      role: "WITCH",
      actions: [
        nightAction("WITCH_USE_POISON", [2]),
        nightAction("WITCH_SKIP"),
      ],
    });

    expect(
      screen.queryByRole("button", { name: "使用解药" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "使用毒药" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "跳过行动" }),
    ).toBeInTheDocument();
  });

  it("starts without a selected target and blocks confirmation until one is chosen", () => {
    const onAction = vi.fn();
    renderNight({
      role: "WEREWOLF",
      actions: [nightAction("WOLF_NOMINATE_KILL", [2, 3])],
      onAction,
    });

    const target = screen.getByTestId("night-target-2");
    expect(target).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "确认行动" })).toBeDisabled();

    fireEvent.click(target);
    expect(target).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "确认行动" })).toBeEnabled();
  });

  it("emits the selected legal action and target once", () => {
    const action = nightAction("WOLF_NOMINATE_KILL", [2, 3]);
    const onAction = vi.fn();
    renderNight({
      role: "WEREWOLF",
      actions: [action],
      onAction,
    });

    fireEvent.click(screen.getByTestId("night-target-3"));
    fireEvent.click(screen.getByRole("button", { name: "确认行动" }));

    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith(action, 3);
  });

  it("requires an explicit action selection when multiple no-target actions are legal", () => {
    const antidote = nightAction("WITCH_USE_ANTIDOTE");
    const skip = nightAction("WITCH_SKIP");
    const onAction = vi.fn();
    renderNight({
      role: "WITCH",
      actions: [antidote, skip],
      onAction,
    });

    const confirm = screen.getByRole("button", { name: "确认行动" });
    expect(confirm).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "使用解药" }));
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    expect(onAction).toHaveBeenCalledWith(antidote, null);
  });

  it("exposes deterministic action and target hooks", () => {
    renderNight({
      role: "SEER",
      actions: [nightAction("SEER_INSPECT", [2])],
    });

    expect(screen.getByRole("button", { name: "查验" })).toHaveAttribute(
      "data-action",
      "SEER_INSPECT",
    );
    expect(screen.getByTestId("night-target-2")).toHaveTextContent("查验 2 号");
  });

  it("is integrated by SeatShell for night phases", () => {
    const onAction = vi.fn();
    render(
      <SeatShell
        actionConfirmed={false}
        actionPending={false}
        connectionState="ready"
        errorMessage={null}
        onAction={onAction}
        ready
        roomCode="ABCDEF"
        seatId={1}
        seatView={nightSeatView("SEER", [
          nightAction("SEER_INSPECT", [2, 4]),
        ])}
      />,
    );

    fireEvent.click(screen.getByTestId("night-target-4"));
    fireEvent.click(screen.getByRole("button", { name: "确认行动" }));

    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({ action: "SEER_INSPECT" }),
      4,
    );
  });
});

describe("nightCommand", () => {
  it.each([
    [
      "WOLF_NOMINATE_KILL",
      { command_type: "WOLF_NOMINATE_KILL", target_seat_id: 4 },
      4,
    ],
    [
      "SEER_INSPECT",
      { command_type: "SEER_INSPECT", target_seat_id: 2 },
      2,
    ],
    ["WITCH_USE_ANTIDOTE", { command_type: "WITCH_USE_ANTIDOTE" }, null],
    [
      "WITCH_USE_POISON",
      { command_type: "WITCH_USE_POISON", target_seat_id: 3 },
      3,
    ],
    ["WITCH_SKIP", { command_type: "WITCH_SKIP" }, null],
  ] as const)(
    "builds the exact %s payload",
    (actionName, payload, targetSeatId) => {
      const targets =
        targetSeatId === null ? [] : [targetSeatId];
      const action = nightAction(actionName, targets);

      const command = nightCommand(
        action,
        targetSeatId,
        "room-1",
        12,
      );

      expect(command).toMatchObject({
        schema_version: "command.v1",
        room_id: "room-1",
        expected_revision: 12,
        payload,
      });
      expect(command.command_id).not.toBe("");
      expect(Number.isNaN(Date.parse(command.issued_at))).toBe(false);
    },
  );

  it("fails instead of building a target action without a target", () => {
    expect(() =>
      nightCommand(
        nightAction("SEER_INSPECT", [2]),
        null,
        "room-1",
        12,
      ),
    ).toThrow("target seat is required");
  });

  it("fails instead of building a command for an illegal target", () => {
    expect(() =>
      nightCommand(
        nightAction("SEER_INSPECT", [2]),
        3,
        "room-1",
        12,
      ),
    ).toThrow("target seat is not legal");
  });

  it("rejects an unsupported action", () => {
    expect(() =>
      nightCommand(
        nightAction("VOTE", [2]),
        2,
        "room-1",
        12,
      ),
    ).toThrow("unsupported night action: VOTE");
  });
});
