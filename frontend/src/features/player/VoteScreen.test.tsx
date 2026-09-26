import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { LegalAction, VoteSummary } from "../../protocol/models";

import { VoteScreen } from "./VoteScreen";

const DEADLINE = "2099-01-01T00:01:00.000Z";

function voteActions(targets = [2, 4]): LegalAction[] {
  return [
    { action: "VOTE", target_seat_ids: targets, deadline_at: DEADLINE },
    { action: "ABSTAIN", target_seat_ids: [], deadline_at: DEADLINE },
  ];
}

function renderVote({
  phase = "DAY_VOTE",
  actions = voteActions(),
  voteSummary = {
    round_id: "round-1",
    tallies: [],
    abstention_count: 0,
    closed: false,
    submitted_count: 3,
    eligible_count: 6,
  },
  disabled = false,
}: {
  phase?: string;
  actions?: LegalAction[];
  voteSummary?: VoteSummary | null;
  disabled?: boolean;
} = {}) {
  const onAction = vi.fn();
  const result = render(
    <VoteScreen
      actions={actions}
      disabled={disabled}
      onAction={onAction}
      phase={phase}
      voteSummary={voteSummary}
    />,
  );
  return { onAction, ...result };
}

describe("VoteScreen", () => {
  it("shows only aggregate vote progress and an abstain action", () => {
    const { container } = renderVote({
      voteSummary: {
        round_id: "round-1",
        tallies: [{ seat_id: 41, votes: 37 }],
        abstention_count: 29,
        closed: false,
        submitted_count: 3,
        eligible_count: 6,
      },
    });

    expect(screen.getByText("已提交 3 / 6")).toBeVisible();
    expect(screen.queryByTestId("vote-tally")).not.toBeInTheDocument();
    expect(screen.queryByText("41")).not.toBeInTheDocument();
    expect(screen.queryByText("37")).not.toBeInTheDocument();
    expect(screen.queryByText("29")).not.toBeInTheDocument();
    expect(container.textContent).not.toMatch(/41|37|29/);
    expect(screen.getByRole("button", { name: "弃票" })).toBeVisible();
  });

  it("renders target buttons only from legal actions", () => {
    renderVote();

    expect(screen.getByTestId("vote-target-2")).toBeVisible();
    expect(screen.getByTestId("vote-target-4")).toBeVisible();
    expect(screen.queryByTestId("vote-target-3")).not.toBeInTheDocument();
  });

  it("requires a target and sends the exact selected seat", async () => {
    const user = userEvent.setup();
    const { onAction } = renderVote();
    const confirm = screen.getByRole("button", { name: "确认投票" });

    expect(confirm).toBeDisabled();
    await user.click(screen.getByTestId("vote-target-4"));
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({ action: "VOTE" }),
      4,
    );
  });

  it("sends ABSTAIN only when the legal action is present", async () => {
    const user = userEvent.setup();
    const { onAction } = renderVote();

    await user.click(screen.getByRole("button", { name: "弃票" }));

    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({ action: "ABSTAIN" }),
      null,
    );
  });

  it("disables all vote controls while a submission is pending", () => {
    renderVote({ disabled: true });

    expect(screen.getByRole("button", { name: "确认投票" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "弃票" })).toBeDisabled();
    expect(screen.getByTestId("vote-target-2")).toBeDisabled();
  });

  it("clears a selected target that is no longer legal", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn();
    const { rerender } = render(
      <VoteScreen
        actions={voteActions([2, 4])}
        onAction={onAction}
        phase="DAY_VOTE"
        voteSummary={null}
      />,
    );

    await user.click(screen.getByTestId("vote-target-4"));
    expect(screen.getByRole("button", { name: "确认投票" })).toBeEnabled();

    rerender(
      <VoteScreen
        actions={voteActions([2])}
        onAction={onAction}
        phase="DAY_VOTE"
        voteSummary={null}
      />,
    );

    expect(screen.queryByTestId("vote-target-4")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认投票" })).toBeDisabled();
  });

  it("marks PK voting with visible PK text", () => {
    renderVote({ phase: "DAY_PK_VOTE" });

    expect(screen.getByTestId("pk-phase")).toHaveTextContent("PK");
  });

  it("keeps the PK marker visible without a legal voting action", () => {
    renderVote({
      phase: "DAY_PK_VOTE",
      actions: [],
      voteSummary: null,
    });

    expect(screen.getByTestId("pk-phase")).toHaveTextContent("PK");
  });
});
