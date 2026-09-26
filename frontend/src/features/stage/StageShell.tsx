import {
  Expand,
  Lightbulb,
  LightbulbOff,
  Minimize,
  Moon,
  RotateCw,
  Sun,
  Timer as TimerIcon,
  UsersRound,
  Vote,
  Wifi,
  WifiOff,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import type { PublicView } from "../../protocol/models";
import type { RoomSocketState } from "../../realtime/RoomSocket";

export interface StageShellProps {
  roomCode: string;
  connectionState: RoomSocketState;
  publicView: PublicView;
  serverTime: string;
  errorMessage: string | null;
  onRetryConnection: () => void;
}

const CONNECTION_LABELS: Record<RoomSocketState, string> = {
  idle: "等待连接",
  connecting: "连接中",
  awaiting_auth: "正在认证",
  ready: "已连接",
  retry_wait: "连接中断，正在重连",
  closed: "连接已关闭",
};

const PHASE_LABELS: Record<string, string> = {
  LOBBY: "等待开局",
  ROLE_REVEAL: "角色揭示",
  NIGHT_START: "夜晚开始",
  NIGHT_WOLF: "狼人行动",
  NIGHT_SEER: "预言家查验",
  NIGHT_WITCH: "女巫行动",
  NIGHT_RESOLVE: "夜间结算",
  DAY_ANNOUNCE: "天亮公布",
  DAY_DISCUSSION: "白天讨论",
  DAY_VOTE: "白天投票",
  DAY_PK_DISCUSSION: "PK 讨论",
  DAY_PK_VOTE: "PK 投票",
  DAY_EXILE: "放逐结算",
  WIN_CHECK: "胜负检查",
  GAME_END: "游戏结束",
};

const MUTED_TEXT = "text-text-muted day:text-surface";

interface WakeLockSentinelLike {
  release: () => Promise<void>;
  addEventListener: (type: "release", listener: () => void) => void;
}

interface WakeLockLike {
  request: (type: "screen") => Promise<WakeLockSentinelLike>;
}

function phaseTitle(publicView: PublicView): string {
  const label = PHASE_LABELS[publicView.phase] ?? publicView.phase;
  if (publicView.phase === "LOBBY" || publicView.phase === "GAME_END") {
    return label;
  }
  if (publicView.phase.startsWith("NIGHT")) {
    return `第 ${publicView.day} 夜 · ${label}`;
  }
  return `第 ${publicView.day} 天 · ${label}`;
}

function remainingMilliseconds(
  deadlineAt: string | null,
  serverTime: string,
): number {
  if (deadlineAt === null) return 0;
  const deadline = Date.parse(deadlineAt);
  const server = Date.parse(serverTime);
  if (!Number.isFinite(deadline) || !Number.isFinite(server)) return 0;
  return Math.max(0, deadline - server);
}

function formatRemaining(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.ceil(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function wakeLockApi(): WakeLockLike | null {
  const navigatorWithWakeLock = globalThis.navigator as
    | (Navigator & { wakeLock?: WakeLockLike })
    | undefined;
  return navigatorWithWakeLock?.wakeLock ?? null;
}

function fullscreenSupported(): boolean {
  return (
    typeof document !== "undefined" &&
    typeof document.documentElement.requestFullscreen === "function"
  );
}

function subscribeFullscreen(listener: () => void): () => void {
  document.addEventListener("fullscreenchange", listener);
  return () => document.removeEventListener("fullscreenchange", listener);
}

function getFullscreenSnapshot(): boolean {
  return document.fullscreenElement != null;
}

function getFullscreenServerSnapshot(): boolean {
  return false;
}

function ConnectionStatus({ state }: { state: RoomSocketState }) {
  const connected = state === "ready";
  const Icon = connected ? Wifi : WifiOff;
  return (
    <span
      className={`inline-flex items-center gap-2 text-sm ${
        connected ? "text-success day:text-surface" : MUTED_TEXT
      }`}
      role="status"
    >
      <Icon aria-hidden="true" size={17} />
      {CONNECTION_LABELS[state]}
    </span>
  );
}

interface StageCountdownProps {
  deadlineAt: string | null;
  serverTime: string;
  paused: boolean;
}

function StageCountdown({
  deadlineAt,
  serverTime,
  paused,
}: StageCountdownProps) {
  const [remainingMs, setRemainingMs] = useState(() =>
    remainingMilliseconds(deadlineAt, serverTime),
  );

  useEffect(() => {
    if (deadlineAt === null || paused) {
      return;
    }
    const timer = setInterval(() => {
      setRemainingMs((current) => Math.max(0, current - 250));
    }, 250);
    return () => clearInterval(timer);
  }, [deadlineAt, paused]);

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
          role="status"
        >
          游戏已暂停
        </p>
      ) : null}
    </section>
  );
}

export function StageShell({
  roomCode,
  connectionState,
  publicView,
  serverTime,
  errorMessage,
  onRetryConnection,
}: StageShellProps) {
  const isNight = publicView.phase.startsWith("NIGHT");
  const PhaseIcon = publicView.phase === "GAME_END" ? Sun : isNight ? Moon : Sun;
  const [wakeLockRequested, setWakeLockRequested] = useState(false);
  const [wakeLockActive, setWakeLockActive] = useState(false);
  const wakeLockSentinelRef = useRef<WakeLockSentinelLike | null>(null);
  const wakeLockGenerationRef = useRef(0);
  const mountedRef = useRef(false);
  const isFullscreen = useSyncExternalStore(
    subscribeFullscreen,
    getFullscreenSnapshot,
    getFullscreenServerSnapshot,
  );
  const wakeLockSupported = wakeLockApi() !== null;
  const canUseFullscreen = fullscreenSupported();
  const unsupportedPresentation = [
    canUseFullscreen ? null : "全屏",
    wakeLockSupported ? null : "屏幕常亮",
  ].filter((feature): feature is string => feature !== null);
  const showRetry =
    connectionState === "retry_wait" || connectionState === "closed";
  const gameEndedResult =
    publicView.phase === "GAME_END"
      ? [...publicView.public_timeline]
          .reverse()
          .find((item) => item.event_type === "GAME_ENDED")?.statement ?? null
      : null;

  const requestWakeLock = useCallback(async () => {
    const api = wakeLockApi();
    if (api === null) return;
    const generation = wakeLockGenerationRef.current + 1;
    wakeLockGenerationRef.current = generation;
    try {
      const sentinel = await api.request("screen");
      if (
        !mountedRef.current ||
        generation !== wakeLockGenerationRef.current
      ) {
        await sentinel.release();
        return;
      }
      const previousSentinel = wakeLockSentinelRef.current;
      wakeLockSentinelRef.current = sentinel;
      sentinel.addEventListener("release", () => {
        if (wakeLockSentinelRef.current !== sentinel) return;
        wakeLockSentinelRef.current = null;
        if (mountedRef.current) {
          setWakeLockActive(false);
        }
      });
      setWakeLockActive(true);
      if (previousSentinel !== null && previousSentinel !== sentinel) {
        try {
          await previousSentinel.release();
        } catch {
          // The new sentinel remains authoritative if cleanup of the old one fails.
        }
      }
    } catch {
      if (mountedRef.current) {
        wakeLockSentinelRef.current = null;
        setWakeLockActive(false);
        setWakeLockRequested(false);
      }
    }
  }, []);

  const releaseWakeLock = useCallback(async () => {
    wakeLockGenerationRef.current += 1;
    const sentinel = wakeLockSentinelRef.current;
    wakeLockSentinelRef.current = null;
    setWakeLockActive(false);
    if (sentinel !== null) {
      try {
        await sentinel.release();
      } catch {
        // Presentation-only lock; browser policy failures are non-fatal.
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      wakeLockGenerationRef.current += 1;
      const sentinel = wakeLockSentinelRef.current;
      wakeLockSentinelRef.current = null;
      if (sentinel !== null) {
        void sentinel.release();
      }
    };
  }, []);

  useEffect(() => {
    function handleVisibilityChange() {
      if (
        document.visibilityState === "visible" &&
        wakeLockRequested &&
        wakeLockSentinelRef.current === null
      ) {
        void requestWakeLock();
      }
    }

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () =>
      document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [requestWakeLock, wakeLockRequested]);

  async function toggleFullscreen() {
    if (!canUseFullscreen) return;
    try {
      if (document.fullscreenElement == null) {
        await document.documentElement.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
    } catch {
      // Presentation-only control; keep the stage fully usable on failure.
    }
  }

  async function toggleWakeLock() {
    if (!wakeLockSupported) return;
    if (wakeLockRequested) {
      setWakeLockRequested(false);
      await releaseWakeLock();
      return;
    }
    setWakeLockRequested(true);
    await requestWakeLock();
  }

  return (
    <main
      className="night:bg-phase-night day:bg-phase-day min-h-screen bg-surface text-text day:text-surface"
      data-phase={isNight ? "night" : "day"}
      data-testid="stage-root"
    >
      <div className="mx-auto grid min-h-screen w-full max-w-[1800px] grid-rows-[auto_1fr_auto] gap-6 px-5 py-5 sm:px-8 lg:px-12">
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-text-muted/30 pb-4">
          <div className="grid gap-1">
            <p className={`text-sm font-medium ${MUTED_TEXT}`}>
              {`房间 ABCDEF`.replace("ABCDEF", roomCode)}
            </p>
            <h1 className="inline-flex items-center gap-3 text-3xl font-semibold sm:text-4xl">
              <PhaseIcon aria-hidden="true" size={30} />
              {phaseTitle(publicView)}
            </h1>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <ConnectionStatus state={connectionState} />
            {unsupportedPresentation.length === 0 ? null : (
              <span
                className={`text-xs ${MUTED_TEXT}`}
                id="stage-presentation-support"
                role="status"
              >
                {`当前浏览器不支持${unsupportedPresentation.join("和")}`}
              </span>
            )}
            {showRetry ? (
              <button
                className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-action px-4 font-semibold text-surface"
                onClick={onRetryConnection}
                type="button"
              >
                <RotateCw aria-hidden="true" size={18} />
                重新连接共享屏
              </button>
            ) : null}
            <button
              aria-describedby={
                canUseFullscreen ? undefined : "stage-presentation-support"
              }
              aria-label={isFullscreen ? "退出全屏" : "进入全屏"}
              aria-pressed={isFullscreen}
              className="inline-flex size-11 items-center justify-center rounded-lg border border-text-muted/40 bg-surface-raised text-text day:border-text day:text-text disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!canUseFullscreen}
              onClick={() => void toggleFullscreen()}
              title={isFullscreen ? "退出全屏" : "进入全屏"}
              type="button"
            >
              {isFullscreen ? (
                <Minimize aria-hidden="true" size={20} />
              ) : (
                <Expand aria-hidden="true" size={20} />
              )}
            </button>
            <button
              aria-describedby={
                wakeLockSupported ? undefined : "stage-presentation-support"
              }
              aria-label="保持屏幕常亮"
              aria-pressed={wakeLockActive}
              className="inline-flex size-11 items-center justify-center rounded-lg border border-text-muted/40 bg-surface-raised text-text day:border-text day:text-text disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!wakeLockSupported}
              onClick={() => void toggleWakeLock()}
              title={
                wakeLockSupported
                  ? wakeLockActive
                    ? "关闭屏幕常亮"
                    : "保持屏幕常亮"
                  : "当前浏览器不支持屏幕常亮"
              }
              type="button"
            >
              {wakeLockActive ? (
                <Lightbulb aria-hidden="true" size={20} />
              ) : (
                <LightbulbOff aria-hidden="true" size={20} />
              )}
            </button>
          </div>
        </header>

        <div className="grid min-h-0 gap-8 lg:grid-cols-[minmax(0,1fr)_24rem]">
          <section
            aria-labelledby="stage-timeline-title"
            className="grid min-h-0 content-start gap-5"
          >
            <div className="flex items-end justify-between gap-4">
              <h2 id="stage-timeline-title" className="text-2xl font-semibold">
                公共时间线
              </h2>
              <span className={`text-sm ${MUTED_TEXT}`}>
                {`第 ${publicView.day} 天`}
              </span>
            </div>
            {publicView.public_timeline.length === 0 ? (
              <p className={`text-base ${MUTED_TEXT}`}>暂无公开事件</p>
            ) : (
              <ol className="grid gap-3">
                {publicView.public_timeline.map((item) => (
                  <li
                    className="border-l-2 border-action pl-4 text-lg leading-relaxed text-text day:border-text day:text-surface"
                    key={item.event_id}
                  >
                    {item.statement}
                  </li>
                ))}
              </ol>
            )}
          </section>

          <aside className="grid content-start gap-8 border-text-muted/30 lg:border-l lg:pl-8">
            {gameEndedResult === null ? null : (
              <section aria-labelledby="stage-result-title" className="grid gap-2">
                <h2 id="stage-result-title" className="text-lg font-semibold">
                  游戏结束
                </h2>
                <p
                  className="text-2xl font-semibold text-success day:text-surface"
                  data-testid="stage-result"
                >
                  {gameEndedResult}
                </p>
              </section>
            )}

            <section aria-labelledby="stage-seats-title" className="grid gap-3">
              <h2
                className="inline-flex items-center gap-2 text-lg font-semibold"
                id="stage-seats-title"
              >
                <UsersRound aria-hidden="true" size={20} />
                存活座位
              </h2>
              {publicView.living_seats.length === 0 ? (
                <p className={`text-sm ${MUTED_TEXT}`}>暂无存活座位</p>
              ) : (
                <ol className="flex flex-wrap gap-2">
                  {publicView.living_seats.map((seatId) => (
                    <li
                      className="grid size-11 place-items-center rounded-full bg-success/15 font-semibold text-success day:bg-surface/10 day:text-surface"
                      key={seatId}
                    >
                      {seatId}
                    </li>
                  ))}
                </ol>
              )}
            </section>

            <section aria-labelledby="stage-vote-title" className="grid gap-3">
              <h2
                className="inline-flex items-center gap-2 text-lg font-semibold"
                id="stage-vote-title"
              >
                <Vote aria-hidden="true" size={20} />
                投票进度
              </h2>
              {publicView.vote_summary === null ? (
                <p className={`text-sm ${MUTED_TEXT}`}>
                  当前没有进行中的投票
                </p>
              ) : (
                <div className="grid gap-2">
                  <p className="text-base font-medium">
                    {`${publicView.vote_summary.submitted_count} / ${publicView.vote_summary.eligible_count} 已提交`}
                  </p>
                  <progress
                    aria-labelledby="stage-vote-title"
                    className="h-2 w-full accent-text"
                    max={Math.max(1, publicView.vote_summary.eligible_count)}
                    value={Math.min(
                      publicView.vote_summary.submitted_count,
                      publicView.vote_summary.eligible_count,
                    )}
                  />
                </div>
              )}
            </section>

            <StageCountdown
              deadlineAt={publicView.deadline_at}
              key={publicView.deadline_at ?? "none"}
              paused={publicView.paused}
              serverTime={serverTime}
            />
          </aside>
        </div>

        <footer className="min-h-6">
          {errorMessage === null ? null : (
            <p className="text-sm text-danger day:text-surface" role="alert">
              {errorMessage}
            </p>
          )}
        </footer>
      </div>
    </main>
  );
}
