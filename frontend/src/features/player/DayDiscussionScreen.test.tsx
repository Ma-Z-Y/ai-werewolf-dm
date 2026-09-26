import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { LegalAction } from "../../protocol/models";

import { DayDiscussionScreen } from "./DayDiscussionScreen";

const DEADLINE = "2099-01-01T00:01:00.000Z";

function discussionActions(): LegalAction[] {
  return [
    { action: "SPEAK", target_seat_ids: [], deadline_at: DEADLINE },
    { action: "PASS_SPEECH", target_seat_ids: [], deadline_at: DEADLINE },
  ];
}

function renderDiscussion({
  currentSeatId = 3,
  seatId = 3,
  phase = "DAY_DISCUSSION",
  actions = discussionActions(),
  disabled = false,
}: {
  currentSeatId?: number | null;
  seatId?: number;
  phase?: string;
  actions?: LegalAction[];
  disabled?: boolean;
} = {}) {
  const onAction = vi.fn();
  render(
    <DayDiscussionScreen
      actions={actions}
      currentSpeakerSeatId={currentSeatId}
      deadlineAt={DEADLINE}
      disabled={disabled}
      onAction={onAction}
      phase={phase}
      seatId={seatId}
    />,
  );
  return { onAction };
}

describe("DayDiscussionScreen", () => {
  it("shows speak controls only for the current speaker", () => {
    renderDiscussion();

    expect(screen.getByRole("textbox", { name: "发言内容" })).toBeVisible();
    expect(screen.getByRole("button", { name: "发送发言" })).toBeVisible();
    expect(screen.getByRole("button", { name: "跳过发言" })).toBeVisible();
    expect(screen.getByTestId("speech-action")).toBeVisible();
    expect(screen.getByText(/截止时间/)).toBeVisible();
  });

  it("does not render input controls when another seat is speaking", () => {
    renderDiscussion({ currentSeatId: 5, seatId: 3 });

    expect(
      screen.queryByRole("textbox", { name: "发言内容" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "跳过发言" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("等待当前玩家完成发言")).toBeVisible();
  });

  it("caps speech input at 1000 characters", () => {
    renderDiscussion();
    const input = screen.getByRole("textbox", { name: "发言内容" });

    expect(input).toHaveAttribute("maxlength", "1000");
    fireEvent.change(input, { target: { value: "字".repeat(1001) } });
    expect((input as HTMLTextAreaElement).value).toHaveLength(1000);
  });

  it("trims speech before sending", async () => {
    const user = userEvent.setup();
    const { onAction } = renderDiscussion();
    const input = screen.getByRole("textbox", { name: "发言内容" });

    fireEvent.change(input, {
      target: { value: "  有话要说  " },
    });
    await user.click(screen.getByRole("button", { name: "发送发言" }));

    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({ action: "SPEAK" }),
      null,
      "有话要说",
    );
  });

  it("sends PASS_SPEECH without speech text", async () => {
    const user = userEvent.setup();
    const { onAction } = renderDiscussion();

    await user.click(screen.getByRole("button", { name: "跳过发言" }));

    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({ action: "PASS_SPEECH" }),
      null,
      null,
    );
  });

  it("marks PK discussion with visible PK text", () => {
    renderDiscussion({ phase: "DAY_PK_DISCUSSION" });

    expect(screen.getByTestId("pk-phase")).toHaveTextContent("PK");
  });

  it("keeps the PK marker visible when another seat is speaking", () => {
    renderDiscussion({
      currentSeatId: 5,
      seatId: 3,
      phase: "DAY_PK_DISCUSSION",
      actions: [],
    });

    expect(screen.getByTestId("pk-phase")).toHaveTextContent("PK");
  });
});
