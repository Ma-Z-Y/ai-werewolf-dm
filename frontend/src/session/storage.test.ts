import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  readDisplaySession,
  readHostSession,
  readSeatSession,
  writeDisplaySession,
  writeHostSession,
  writeSeatSession,
} from "./storage";

const seatSession = {
  schemaVersion: 1 as const,
  roomCode: "ABCDEF",
  roomId: "5d1a89f5-82e4-4aac-b67a-bcbaef13c4d7",
  seatId: 3,
  token: "seat-token",
  expiresAt: "2099-01-01T00:00:00.000Z",
};

const hostSession = {
  schemaVersion: 1 as const,
  roomCode: "ABCDEF",
  roomId: "5d1a89f5-82e4-4aac-b67a-bcbaef13c4d7",
  token: "host-token",
  expiresAt: "2099-01-01T00:00:00.000Z",
};

const displaySession = {
  schemaVersion: 1 as const,
  roomCode: "ABCDEF",
  roomId: "5d1a89f5-82e4-4aac-b67a-bcbaef13c4d7",
  token: "display-token",
  expiresAt: "2099-01-01T00:00:00.000Z",
};

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("room session storage", () => {
  it("round-trips each role in its scoped storage", () => {
    writeSeatSession(seatSession);
    writeHostSession(hostSession);
    writeDisplaySession(displaySession);

    expect(readSeatSession("ABCDEF")).toEqual(seatSession);
    expect(readHostSession("ABCDEF")).toEqual(hostSession);
    expect(readDisplaySession("ABCDEF")).toEqual(displaySession);

    expect(localStorage.getItem("werewolf:v1:room:ABCDEF:seat")).not.toBeNull();
    expect(localStorage.getItem("werewolf:v1:room:ABCDEF:host")).not.toBeNull();
    expect(localStorage.getItem("werewolf:v1:last-host-room")).toBe("ABCDEF");
    expect(
      sessionStorage.getItem("werewolf:v1:room:ABCDEF:display"),
    ).not.toBeNull();
    expect(localStorage.getItem("werewolf:v1:room:ABCDEF:display")).toBeNull();
  });

  it("drops a seat session from another room", () => {
    localStorage.setItem(
      "werewolf:v1:room:ZZZZZZ:seat",
      JSON.stringify({ ...seatSession, roomCode: "ZZZZZZ" }),
    );

    expect(readSeatSession("ABCDEF")).toBeNull();
    expect(localStorage.length).toBe(0);
  });

  it("drops an expired host session", () => {
    writeHostSession({
      ...hostSession,
      expiresAt: "2000-01-01T00:00:00.000Z",
    });

    expect(readHostSession("ABCDEF")).toBeNull();
    expect(localStorage.length).toBe(0);
  });

  it("drops malformed and wrong-version display data", () => {
    sessionStorage.setItem(
      "werewolf:v1:room:ABCDEF:display",
      JSON.stringify({ ...displaySession, schemaVersion: 2 }),
    );
    expect(readDisplaySession("ABCDEF")).toBeNull();

    sessionStorage.setItem(
      "werewolf:v1:room:ABCDEF:display",
      "{not-json",
    );
    expect(readDisplaySession("ABCDEF")).toBeNull();
    expect(sessionStorage.length).toBe(0);
  });

  it.each([
    "2099-02-31T00:00:00.000Z",
    "2099-01-01T00:00:00",
    "2099-13-01T00:00:00.000Z",
  ])("drops invalid calendar date %s", (expiresAt) => {
    localStorage.setItem(
      "werewolf:v1:room:ABCDEF:seat",
      JSON.stringify({ ...seatSession, expiresAt }),
    );

    expect(readSeatSession("ABCDEF")).toBeNull();
    expect(localStorage.getItem("werewolf:v1:room:ABCDEF:seat")).toBeNull();
  });

  it("does not leak storage quota failures into game logic", () => {
    vi.stubGlobal("localStorage", {
      length: 0,
      clear: vi.fn(),
      getItem: vi.fn(() => null),
      key: vi.fn(() => null),
      removeItem: vi.fn(),
      setItem: vi.fn(() => {
        throw new DOMException("quota", "QuotaExceededError");
      }),
    });

    expect(() => writeSeatSession(seatSession)).not.toThrow();
    expect(readSeatSession("ABCDEF")).toBeNull();
  });
});
