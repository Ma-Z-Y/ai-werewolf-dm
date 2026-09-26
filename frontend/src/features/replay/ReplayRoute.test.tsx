import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "../../app/routes";
import type { PrivateFact, SeatView } from "../../protocol/models";
import {
  readSeatSession,
  writeSeatSession,
} from "../../session/storage";
import { SeatShell } from "../player/SeatShell";

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(payload),
  } as unknown as Response;
}

function writeSeatSessionForRoom(token = "seat-token-SENTINEL"): void {
  writeSeatSession({
    schemaVersion: 1,
    roomCode: "ABCDEF",
    roomId: "room-1",
    seatId: 1,
    token,
    expiresAt: "2099-01-01T00:00:00.000Z",
  });
}

function gameEndSeatView(): SeatView {
  return {
    schema_version: "seat-view.v1",
    room_id: "room-1",
    revision: 15,
    phase: "GAME_END",
    day: 2,
    living_seats: [1, 3, 5],
    public_timeline: [
      {
        event_id: "event-end",
        revision: 15,
        event_type: "GAME_ENDED",
        statement: "游戏结束：好人阵营获胜",
      },
    ],
    vote_summary: null,
    deadline_at: null,
    paused: false,
    paused_at: null,
    seat_id: 1,
    role: "SEER",
    private_facts: [
      {
        fact_id: "fact-seer",
        event_id: "event-seer",
        recipient_seat_id: 1,
        revision: 8,
        fact_type: "SEER_CHECK",
        payload: {
          day: 1,
          target_seat_id: 2,
          faction: "GOOD",
        },
      },
    ],
    legal_actions: [],
  };
}

function renderReplay(path = "/replay/ABCDEF") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  globalThis.localStorage.clear();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ReplayRoute", () => {
  it("renders only returned public and own private facts", async () => {
    writeSeatSessionForRoom();
    const privateFacts: PrivateFact[] = [
      {
        fact_id: "fact-seer",
        event_id: "event-seer",
        recipient_seat_id: 1,
        revision: 8,
        fact_type: "SEER_CHECK",
        payload: {
          day: 1,
          target_seat_id: 2,
          faction: "GOOD",
        },
      },
      {
        fact_id: "fact-other-seat",
        event_id: "event-other-seat",
        recipient_seat_id: 2,
        revision: 9,
        fact_type: "SEER_CHECK",
        payload: {
          day: 1,
          target_seat_id: 3,
          faction: "WEREWOLF",
        },
      },
    ];
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        room_id: "room-1",
        revision: 12,
        public_timeline: [
          {
            event_id: "event-public",
            revision: 11,
            event_type: "PLAYERS_DIED",
            statement: "2 号玩家出局",
          },
        ],
        private_facts: privateFacts,
        events: [
          {
            event_type: "DM_TRACE",
            payload: { raw_snapshot: "RAW_SNAPSHOT_SENTINEL" },
          },
        ],
      }),
    );

    renderReplay();

    expect(
      await screen.findByText("预言家查验 2 号为好人"),
    ).toBeVisible();
    expect(screen.getByText("2 号玩家出局")).toBeVisible();
    expect(
      screen.queryByText("预言家查验 3 号为狼人"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("DM_TRACE")).not.toBeInTheDocument();
    expect(
      screen.queryByText("RAW_SNAPSHOT_SENTINEL"),
    ).not.toBeInTheDocument();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [requestPath, requestInit] = fetchMock.mock.calls[0];
    expect(requestPath).toBe("/rooms/ABCDEF/replay");
    expect(requestPath).not.toContain("seat-token-SENTINEL");
    expect(
      (requestInit?.headers as Record<string, string>).Authorization,
    ).toBe("Bearer seat-token-SENTINEL");
  });

  it("clears an expired replay token", async () => {
    writeSeatSessionForRoom();
    fetchMock.mockResolvedValue(
      jsonResponse(401, { code: "TOKEN_EXPIRED" }),
    );

    renderReplay();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("登录已过期");
    expect(readSeatSession("ABCDEF")).toBeNull();
  });

  it("mounts the game end screen with role, seats, and replay link", () => {
    render(
      <MemoryRouter>
        <SeatShell
          actionConfirmed={false}
          actionPending={false}
          connectionState="ready"
          errorMessage={null}
          onAction={vi.fn()}
          ready={false}
          roomCode="ABCDEF"
          seatId={1}
          seatView={gameEndSeatView()}
        />
      </MemoryRouter>,
    );

    const gameEnd = screen
      .getByRole("heading", { name: "终局结果" })
      .closest("section");
    expect(gameEnd).not.toBeNull();
    expect(
      within(gameEnd as HTMLElement).getByText(
        "游戏结束：好人阵营获胜",
      ),
    ).toBeVisible();
    expect(
      within(gameEnd as HTMLElement).getByText("你的身份：预言家"),
    ).toBeVisible();
    expect(
      within(gameEnd as HTMLElement).getByText(
        "存活座位：1、3、5 号",
      ),
    ).toBeVisible();
    expect(
      within(gameEnd as HTMLElement).getByText("出局座位：2、4、6 号"),
    ).toBeVisible();
    const replayLink = within(gameEnd as HTMLElement).getByRole("link", {
      name: "查看玩家回放",
    });
    expect(replayLink).toHaveAttribute(
      "href",
      "/replay/ABCDEF?revision=15",
    );
    expect(replayLink.getAttribute("href")).not.toContain(
      "seat-token-SENTINEL",
    );
  });

  it("does not fetch replay without a valid seat session", () => {
    renderReplay();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "未找到有效玩家会话",
    );
  });

  it("fetches once for the same room and revision", async () => {
    writeSeatSessionForRoom();
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        room_id: "room-1",
        revision: 12,
        public_timeline: [],
        private_facts: [],
        events: [],
      }),
    );

    const view = renderReplay("/replay/ABCDEF?revision=12");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    view.rerender(
      <MemoryRouter initialEntries={["/replay/ABCDEF?revision=12"]}>
        <AppRoutes />
      </MemoryRouter>,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });

  it("deduplicates replay fetches under StrictMode", async () => {
    writeSeatSessionForRoom();
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        room_id: "room-1",
        revision: 12,
        public_timeline: [],
        private_facts: [],
        events: [],
      }),
    );

    render(
      <StrictMode>
        <MemoryRouter initialEntries={["/replay/ABCDEF?revision=12"]}>
          <AppRoutes />
        </MemoryRouter>
      </StrictMode>,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
