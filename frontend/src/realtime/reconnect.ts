export const RECONNECT_BASE_DELAY_MS = 500;
export const RECONNECT_MAX_DELAY_MS = 8000;

export type ReconnectDecision =
  | { kind: "stop"; reason: "expired" | "replaced" }
  | { kind: "retry"; delayMs: number };

export function reconnectDelay(attempt: number): number {
  const normalizedAttempt = Math.max(0, Math.floor(attempt));
  return Math.min(
    RECONNECT_BASE_DELAY_MS * 2 ** normalizedAttempt,
    RECONNECT_MAX_DELAY_MS,
  );
}

export function reconnectDecision(
  closeCode: number,
  attempt: number,
): ReconnectDecision {
  if (closeCode === 4001) {
    return { kind: "stop", reason: "expired" };
  }
  if (closeCode === 4003) {
    return { kind: "stop", reason: "replaced" };
  }
  if (closeCode === 1001) {
    return { kind: "retry", delayMs: 0 };
  }
  return { kind: "retry", delayMs: reconnectDelay(attempt) };
}
