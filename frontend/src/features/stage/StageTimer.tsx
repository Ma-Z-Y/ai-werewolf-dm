/* eslint-disable react-refresh/only-export-components -- Timer helpers are pure unit seams testable without the component. */
import { Timer as TimerIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export const TIMER_DRIFT_TOLERANCE_MS = 250;

export interface TimerAnchorSource {
  deadlineAt: string | null;
  serverTime: string;
  phase?: string;
  revision?: number;
  paused?: boolean;
}

export interface TimerAnchor {
  deadlineAt: string | null;
  deadlineMs: number | null;
  remainingMs: number;
  anchoredAt: number;
  phase?: string;
  revision?: number;
  paused: boolean;
  serverTime: string;
  remainingAt: (elapsedMs: number) => number;
}

function parseTimestamp(value: string | null): number | null {
  if (value === null) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function remainingFromServer(
  deadlineAt: string | null,
  serverTime: string,
): number {
  const deadlineMs = parseTimestamp(deadlineAt);
  const serverMs = parseTimestamp(serverTime);
  if (deadlineMs === null || serverMs === null) return 0;
  return Math.max(0, deadlineMs - serverMs);
}

function anchorFromRemaining(
  source: TimerAnchorSource,
  remainingMs: number,
  anchoredAt: number,
): TimerAnchor {
  return {
    deadlineAt: source.deadlineAt,
    deadlineMs: parseTimestamp(source.deadlineAt),
    remainingMs,
    anchoredAt,
    phase: source.phase,
    revision: source.revision,
    paused: source.paused ?? false,
    serverTime: source.serverTime,
    remainingAt(elapsedMs: number): number {
      return Math.max(0, remainingMs - Math.max(0, elapsedMs));
    },
  };
}

export function createTimerAnchor(
  source: TimerAnchorSource,
  now = performance.now(),
): TimerAnchor {
  return anchorFromRemaining(
    source,
    remainingFromServer(source.deadlineAt, source.serverTime),
    now,
  );
}

export function displayRemaining(
  anchor: TimerAnchor,
  now: number,
  paused: boolean,
): number {
  return paused ? anchor.remainingMs : anchor.remainingAt(now - anchor.anchoredAt);
}

export function shouldReanchor(
  anchor: TimerAnchor,
  source: TimerAnchorSource,
  now = performance.now(),
): boolean {
  const nextDeadlineMs = parseTimestamp(source.deadlineAt);
  if (nextDeadlineMs !== anchor.deadlineMs) return true;
  if (source.phase !== undefined && source.phase !== anchor.phase) return true;
  if (source.revision !== undefined && source.revision !== anchor.revision) {
    return true;
  }
  if (source.paused !== undefined && source.paused !== anchor.paused) return true;

  if (source.serverTime === anchor.serverTime) return false;
  const serverMs = parseTimestamp(source.serverTime);
  if (serverMs === null) return false;
  const expectedRemaining =
    nextDeadlineMs === null ? 0 : Math.max(0, nextDeadlineMs - serverMs);
  return (
    Math.abs(expectedRemaining - anchor.remainingAt(now - anchor.anchoredAt)) >=
    TIMER_DRIFT_TOLERANCE_MS
  );
}

function formatRemaining(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.ceil(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export interface StageTimerProps extends TimerAnchorSource {
  paused: boolean;
}

export function StageTimer({
  deadlineAt,
  paused,
  phase,
  revision,
  serverTime,
}: StageTimerProps) {
  const anchorRef = useRef<TimerAnchor | null>(null);
  const [remainingMs, setRemainingMs] = useState(() =>
    remainingFromServer(deadlineAt, serverTime),
  );

  useEffect(() => {
    const source = { deadlineAt, paused, phase, revision, serverTime };
    const now = performance.now();
    const currentAnchor = anchorRef.current;
    const deadlineChanged =
      currentAnchor === null ||
      parseTimestamp(currentAnchor.deadlineAt) !== parseTimestamp(deadlineAt);

    if (currentAnchor === null || shouldReanchor(currentAnchor, source, now)) {
      const shouldFreeze =
        currentAnchor !== null &&
        !deadlineChanged &&
        (paused || currentAnchor.paused);
      anchorRef.current = shouldFreeze
        ? anchorFromRemaining(
            source,
            currentAnchor.paused
              ? currentAnchor.remainingMs
              : currentAnchor.remainingAt(now - currentAnchor.anchoredAt),
            now,
          )
        : createTimerAnchor(source, now);
    }

    if (deadlineAt === null || paused) return;
    const timer = window.setInterval(() => {
      const tickNow = performance.now();
      const anchor = anchorRef.current;
      if (anchor === null) return;
      if (shouldReanchor(anchor, source, tickNow)) {
        anchorRef.current = createTimerAnchor(source, tickNow);
      }
      const activeAnchor = anchorRef.current;
      if (activeAnchor === null) return;
      setRemainingMs(displayRemaining(activeAnchor, tickNow, false));
    }, 250);
    return () => window.clearInterval(timer);
  }, [deadlineAt, paused, phase, revision, serverTime]);

  return (
    <section aria-labelledby="stage-timer-title" className="grid gap-3">
      <h2
        className="inline-flex items-center gap-2 text-lg font-semibold"
        id="stage-timer-title"
      >
        <TimerIcon aria-hidden="true" size={20} />
        阶段计时
      </h2>
      <p className="font-mono text-3xl font-semibold">
        {deadlineAt === null
          ? "不限时"
          : `计时 ${formatRemaining(remainingMs)}`}
      </p>
      {paused ? (
        <p
          className="text-sm font-medium text-action day:text-surface"
          data-testid="stage-pause-banner"
          role="status"
        >
          游戏已暂停
        </p>
      ) : null}
    </section>
  );
}
