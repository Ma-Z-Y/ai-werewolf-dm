import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createDisplayPairing,
  createRoom,
  exchangeDisplayPairing,
  getHostAudit,
  getPlayerReplay,
  joinRoom,
  revokeDisplay,
} from "./roomSession";

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(status: number, payload: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(payload),
  } as unknown as Response;
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("room REST client", () => {
  it("creates a room with the trimmed display name", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(201, {
        room_id: "5d1a89f5-82e4-4aac-b67a-bcbaef13c4d7",
        room_code: "ABCDEF",
        host_token: "host-token",
        expires_at: "2099-01-01T00:00:00.000Z",
      }),
    );

    await expect(createRoom("主持人")).resolves.toMatchObject({
      room_code: "ABCDEF",
      host_token: "host-token",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/rooms",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ display_name: "主持人" }),
      }),
    );
  });

  it("maps ROOM_FULL without exposing the server message", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(409, {
        code: "ROOM_FULL",
        message: "internal text",
        request_id: "6e6a5de8-cc6e-4d9f-8a69-2df6340d1165",
      }),
    );

    await expect(joinRoom("ABCDEF", "玩家")).rejects.toMatchObject({
      code: "ROOM_FULL",
      message: "房间已满",
    });
  });

  it("loads host audit only with an explicit host token", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(200, {
        room_id: "5d1a89f5-82e4-4aac-b67a-bcbaef13c4d7",
        revision: 4,
        state: {},
        raw_events: [],
        dm_trace: [],
        snapshots: [],
      }),
    );

    await getHostAudit("ABCDEF", "host-token");

    expect(fetchMock).toHaveBeenLastCalledWith(
      "/rooms/ABCDEF/audit",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer host-token",
        }),
      }),
    );
  });

  it("uses the correct bearer scope for replay and display operations", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse(200, {
          room_id: "5d1a89f5-82e4-4aac-b67a-bcbaef13c4d7",
          revision: 2,
          public_timeline: [],
          private_facts: [],
          events: [],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(201, {
          pairing_code: "482913",
          expires_at: "2099-01-01T00:00:00.000Z",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(201, {
          room_id: "5d1a89f5-82e4-4aac-b67a-bcbaef13c4d7",
          display_token: "display-token",
          expires_at: "2099-01-01T00:00:00.000Z",
        }),
      )
      .mockResolvedValueOnce(jsonResponse(204, null));

    await getPlayerReplay("ABCDEF", "seat-token");
    await createDisplayPairing("ABCDEF", "host-token");
    await exchangeDisplayPairing("ABCDEF", "482913");
    await revokeDisplay("ABCDEF", "host-token");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/rooms/ABCDEF/replay",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer seat-token",
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/rooms/ABCDEF/display-pairings",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: "Bearer host-token",
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/rooms/ABCDEF/display-sessions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ pairing_code: "482913" }),
        headers: expect.not.objectContaining({
          Authorization: expect.any(String),
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "/rooms/ABCDEF/display-sessions/current",
      expect.objectContaining({
        method: "DELETE",
        headers: expect.objectContaining({
          Authorization: "Bearer host-token",
        }),
      }),
    );
  });

  it("uses a generic message for unknown server errors", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(500, {
        code: "FUTURE_ERROR",
        message: "sensitive diagnostics",
        request_id: "3e28e10d-4b0a-4d98-9f6e-178e72bca1de",
      }),
    );

    await expect(joinRoom("ABCDEF", "玩家")).rejects.toMatchObject({
      code: "UNKNOWN_ERROR",
      message: "发生未知错误",
    });
  });

  it.each(["toString", "constructor", "__proto__"])(
    "does not trust prototype error code %s",
    async (code) => {
      fetchMock.mockResolvedValue(
        jsonResponse(500, {
          code,
          message: "sensitive diagnostics",
          request_id: "3e28e10d-4b0a-4d98-9f6e-178e72bca1de",
        }),
      );

      await expect(joinRoom("ABCDEF", "玩家")).rejects.toMatchObject({
        code: "UNKNOWN_ERROR",
        message: "发生未知错误",
      });
    },
  );

  it("maps network failures without exposing the fetch error", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(joinRoom("ABCDEF", "玩家")).rejects.toMatchObject({
      code: "NETWORK_ERROR",
      message: "网络连接失败",
    });
  });
});
