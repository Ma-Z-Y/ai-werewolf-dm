import { describe, expect, it } from "vitest";

import { reconnectDecision, reconnectDelay } from "./reconnect";

describe("reconnectDelay", () => {
  it("uses the frozen capped exponential schedule", () => {
    expect([0, 1, 2, 3, 4, 5, 20].map(reconnectDelay)).toEqual([
      500, 1000, 2000, 4000, 8000, 8000, 8000,
    ]);
  });
});

describe("reconnectDecision", () => {
  it("stops for expired tokens", () => {
    expect(reconnectDecision(4001, 0)).toEqual({
      kind: "stop",
      reason: "expired",
    });
  });

  it("stops when another connection replaces this device", () => {
    expect(reconnectDecision(4003, 0)).toEqual({
      kind: "stop",
      reason: "replaced",
    });
  });

  it("reconnects immediately after an idle close", () => {
    expect(reconnectDecision(1001, 3)).toEqual({
      kind: "retry",
      delayMs: 0,
    });
  });

  it("backs off for policy, capacity, and transport closes", () => {
    expect(reconnectDecision(1008, 1)).toEqual({
      kind: "retry",
      delayMs: 1000,
    });
    expect(reconnectDecision(1013, 2)).toEqual({
      kind: "retry",
      delayMs: 2000,
    });
    expect(reconnectDecision(1006, 4)).toEqual({
      kind: "retry",
      delayMs: 8000,
    });
  });
});
