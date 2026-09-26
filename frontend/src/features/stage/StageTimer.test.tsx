import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PublicTimelineItem, PublicView } from "../../protocol/models";

import { StageShell } from "./StageShell";
import {
  StageTimer,
  createTimerAnchor,
  displayRemaining,
  shouldReanchor,
} from "./StageTimer";
import { StageTimeline } from "./StageTimeline";

function publicView(overrides: Partial<PublicView> = {}): PublicView {
  return {
    schema_version: "public-view.v1",
    room_id: "room-1",
    revision: 4,
    phase: "DAY_DISCUSSION",
    day: 1,
    living_seats: [1, 2, 3, 5],
    public_timeline: [],
    vote_summary: null,
    deadline_at: "2026-09-25T00:00:30.000Z",
    paused: false,
    paused_at: null,
    ...overrides,
  };
}

function timelineItem(
  overrides: Partial<PublicTimelineItem> = {},
): PublicTimelineItem {
  return {
    event_id: "event-1",
    revision: 4,
    event_type: "PHASE_CHANGED",
    statement: "第 1 天 · 白天讨论",
    ...overrides,
  };
}

function renderShell(overrides: {
  publicView?: PublicView;
  phase?: string;
} = {}) {
  return render(
    <StageShell
      connectionState="ready"
      errorMessage={null}
      onRetryConnection={vi.fn()}
      phase={overrides.phase}
      publicView={overrides.publicView ?? publicView()}
      roomCode="ABCDEF"
      serverTime="2026-09-25T00:00:00.000Z"
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("stage timer", () => {
  it("anchors on server_time instead of wall-clock now", () => {
    vi.spyOn(performance, "now").mockReturnValue(1_000);

    const anchor = createTimerAnchor({
      deadlineAt: "2026-09-25T00:00:30Z",
      serverTime: "2026-09-25T00:00:00Z",
    });

    expect(anchor.remainingAt(5_000)).toBe(25_000);
  });

  it("does not advance while paused", () => {
    vi.spyOn(performance, "now").mockReturnValue(1_000);

    const anchor = createTimerAnchor({
      deadlineAt: "2026-09-25T00:00:30Z",
      serverTime: "2026-09-25T00:00:00Z",
    });

    expect(displayRemaining(anchor, 5_000, true)).toBe(30_000);
  });

  it("does not reanchor when only the monotonic clock advances", () => {
    vi.spyOn(performance, "now").mockReturnValue(1_000);
    const source = {
      deadlineAt: "2026-09-25T00:00:30Z",
      phase: "DAY_DISCUSSION",
      revision: 4,
      serverTime: "2026-09-25T00:00:00Z",
    };
    const anchor = createTimerAnchor(source);

    expect(shouldReanchor(anchor, source, 6_000)).toBe(false);
    expect(displayRemaining(anchor, 6_000, false)).toBe(25_000);
  });

  it("reanchors on deadline, phase, revision, and drift at 250ms", () => {
    vi.spyOn(performance, "now").mockReturnValue(1_000);
    const anchor = createTimerAnchor({
      deadlineAt: "2026-09-25T00:00:30Z",
      phase: "DAY_DISCUSSION",
      revision: 4,
      serverTime: "2026-09-25T00:00:00Z",
    });

    expect(
      shouldReanchor(anchor, {
        deadlineAt: "2026-09-25T00:00:31Z",
        phase: "DAY_DISCUSSION",
        revision: 4,
        serverTime: "2026-09-25T00:00:01Z",
      }, 2_000),
    ).toBe(true);
    expect(
      shouldReanchor(anchor, {
        deadlineAt: "2026-09-25T00:00:30Z",
        phase: "DAY_VOTE",
        revision: 4,
        serverTime: "2026-09-25T00:00:01Z",
      }, 2_000),
    ).toBe(true);
    expect(
      shouldReanchor(anchor, {
        deadlineAt: "2026-09-25T00:00:30Z",
        phase: "DAY_DISCUSSION",
        revision: 5,
        serverTime: "2026-09-25T00:00:01Z",
      }, 2_000),
    ).toBe(true);
    expect(
      shouldReanchor(anchor, {
        deadlineAt: "2026-09-25T00:00:30Z",
        phase: "DAY_DISCUSSION",
        revision: 4,
        serverTime: "2026-09-25T00:00:01.25Z",
      }, 2_000),
    ).toBe(true);
  });

  it("renders a monotonic countdown and keeps paused time frozen", () => {
    vi.useFakeTimers();
    let currentNow = 1_000;
    vi.spyOn(performance, "now").mockImplementation(() => currentNow);

    const { rerender } = render(
      <StageTimer
        deadlineAt="2026-09-25T00:00:30Z"
        paused={false}
        phase="DAY_DISCUSSION"
        revision={4}
        serverTime="2026-09-25T00:00:00Z"
      />,
    );

    expect(screen.getByText("计时 00:30")).toBeVisible();

    currentNow = 6_000;
    rerender(
      <StageTimer
        deadlineAt="2026-09-25T00:00:30Z"
        paused={false}
        phase="DAY_DISCUSSION"
        revision={4}
        serverTime="2026-09-25T00:00:05Z"
      />,
    );
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(screen.getByText("计时 00:25")).toBeVisible();

    rerender(
      <StageTimer
        deadlineAt="2026-09-25T00:00:30Z"
        paused
        phase="DAY_DISCUSSION"
        revision={5}
        serverTime="2026-09-25T00:00:10Z"
      />,
    );

    expect(screen.getByText("计时 00:25")).toBeVisible();
    expect(screen.getByTestId("stage-pause-banner")).toHaveTextContent(
      "游戏已暂停",
    );

    currentNow = 21_000;
    rerender(
      <StageTimer
        deadlineAt="2026-09-25T00:00:30Z"
        paused
        phase="DAY_DISCUSSION"
        revision={5}
        serverTime="2026-09-25T00:00:20Z"
      />,
    );
    expect(screen.getByText("计时 00:25")).toBeVisible();
  });

  it("does not subtract paused time after resuming", () => {
    vi.useFakeTimers();
    let currentNow = 1_000;
    vi.spyOn(performance, "now").mockImplementation(() => currentNow);

    const { rerender } = render(
      <StageTimer
        deadlineAt="2026-09-25T00:00:30Z"
        paused={false}
        phase="DAY_DISCUSSION"
        revision={4}
        serverTime="2026-09-25T00:00:00Z"
      />,
    );

    currentNow = 6_000;
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(screen.getByText("计时 00:25")).toBeVisible();

    rerender(
      <StageTimer
        deadlineAt="2026-09-25T00:00:30Z"
        paused
        phase="DAY_DISCUSSION"
        revision={5}
        serverTime="2026-09-25T00:00:06Z"
      />,
    );
    currentNow = 26_000;
    rerender(
      <StageTimer
        deadlineAt="2026-09-25T00:00:30Z"
        paused
        phase="DAY_DISCUSSION"
        revision={6}
        serverTime="2026-09-25T00:00:26Z"
      />,
    );
    rerender(
      <StageTimer
        deadlineAt="2026-09-25T00:00:30Z"
        paused={false}
        phase="DAY_DISCUSSION"
        revision={7}
        serverTime="2026-09-25T00:00:26Z"
      />,
    );
    act(() => {
      vi.advanceTimersByTime(250);
    });

    expect(screen.getByText("计时 00:25")).toBeVisible();
  });

  it("restores the frozen remaining time after a paused reload", () => {
    const pausedProps = {
      pausedAt: "2026-09-25T00:00:00.000Z",
    } as { pausedAt: string };

    render(
      <StageTimer
        {...pausedProps}
        deadlineAt="2026-09-25T00:00:30.000Z"
        paused
        phase="DAY_DISCUSSION"
        revision={5}
        serverTime="2026-09-25T00:00:20.000Z"
      />,
    );

    expect(screen.getByText("计时 00:30")).toBeVisible();
  });
});

describe("stage timeline", () => {
  it("renders only public statements and never expands hidden facts", () => {
    render(
      <StageTimeline
        items={[
          timelineItem(),
          {
            ...timelineItem({
              event_id: "event-2",
              statement: "公开事件",
            }),
            hidden_facts: ["夜间狼人目标"],
          } as PublicTimelineItem & { hidden_facts: string[] },
        ]}
      />,
    );

    expect(screen.getByText("公开事件")).toBeVisible();
    expect(screen.queryByText("夜间狼人目标")).not.toBeInTheDocument();
  });
});

describe("stage presentation", () => {
  it("exposes a phase hook for day and night styling", () => {
    renderShell({
      phase: "NIGHT",
      publicView: publicView({ phase: "NIGHT_WOLF" }),
    });

    expect(screen.getByTestId("stage-root")).toHaveAttribute(
      "data-phase",
      "night",
    );
    expect(screen.getByTestId("stage-root")).toHaveClass("night:bg-phase-night");
  });

  it("shows the pause banner without rendering a host control", () => {
    renderShell({
      publicView: publicView({ paused: true }),
    });

    expect(screen.getByTestId("stage-pause-banner")).toHaveTextContent(
      "游戏已暂停",
    );
    expect(screen.queryByText("主持人控制")).not.toBeInTheDocument();
  });
});
