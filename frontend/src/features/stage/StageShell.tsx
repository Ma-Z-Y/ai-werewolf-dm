import {
  Expand,
  Lightbulb,
  LightbulbOff,
  Minimize,
  Moon,
  RotateCw,
  Sun,
  UsersRound,
  Vote,
  Wifi,
  WifiOff,
} from "lucide-react";

import type { PublicView } from "../../protocol/models";
import type { RoomSocketState } from "../../realtime/RoomSocket";

import { StageTimeline } from "./StageTimeline";
import { StageTimer } from "./StageTimer";
import { useStageDisplay } from "./useStageDisplay";

export interface StageShellProps {
  roomCode: string;
  connectionState: RoomSocketState;
  publicView: PublicView;
  serverTime: string;
  errorMessage: string | null;
  onRetryConnection: () => void;
  phase?: string;
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

function phaseTitle(publicView: PublicView, phase: string): string {
  const label = PHASE_LABELS[phase] ?? phase;
  if (phase === "LOBBY" || phase === "GAME_END") {
    return label;
  }
  if (phase.startsWith("NIGHT")) {
    return `第 ${publicView.day} 夜 · ${label}`;
  }
  return `第 ${publicView.day} 天 · ${label}`;
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

export function StageShell({
  roomCode,
  connectionState,
  publicView,
  serverTime,
  errorMessage,
  onRetryConnection,
  phase = publicView.phase,
}: StageShellProps) {
  const isNight = phase.startsWith("NIGHT");
  const PhaseIcon = phase === "GAME_END" ? Sun : isNight ? Moon : Sun;
  const display = useStageDisplay();
  const showRetry =
    connectionState === "retry_wait" || connectionState === "closed";
  const gameEndedResult =
    phase === "GAME_END"
      ? [...publicView.public_timeline]
          .reverse()
          .find((item) => item.event_type === "GAME_ENDED")?.statement ?? null
      : null;

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
              {phaseTitle(publicView, phase)}
            </h1>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <ConnectionStatus state={connectionState} />
            {display.presentationStatus === null ? null : (
              <span
                className={`text-xs ${MUTED_TEXT}`}
                id="stage-presentation-support"
                role="status"
              >
                {display.presentationStatus}
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
                display.fullscreenSupported
                  ? undefined
                  : "stage-presentation-support"
              }
              aria-label={display.isFullscreen ? "退出全屏" : "进入全屏"}
              aria-pressed={display.isFullscreen}
              className="inline-flex size-11 items-center justify-center rounded-lg border border-text-muted/40 bg-surface-raised text-text day:border-text day:text-text disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!display.fullscreenSupported}
              onClick={() => void display.toggleFullscreen()}
              title={display.isFullscreen ? "退出全屏" : "进入全屏"}
              type="button"
            >
              {display.isFullscreen ? (
                <Minimize aria-hidden="true" size={20} />
              ) : (
                <Expand aria-hidden="true" size={20} />
              )}
            </button>
            <button
              aria-describedby={
                display.wakeLockSupported
                  ? undefined
                  : "stage-presentation-support"
              }
              aria-label="保持屏幕常亮"
              aria-pressed={display.wakeLockActive}
              className="inline-flex size-11 items-center justify-center rounded-lg border border-text-muted/40 bg-surface-raised text-text day:border-text day:text-text disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!display.wakeLockSupported}
              onClick={() => void display.toggleWakeLock()}
              title={
                display.wakeLockSupported
                  ? display.wakeLockActive
                    ? "关闭屏幕常亮"
                    : "保持屏幕常亮"
                  : "当前浏览器不支持屏幕常亮"
              }
              type="button"
            >
              {display.wakeLockActive ? (
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
            <StageTimeline items={publicView.public_timeline} />
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

            <StageTimer
              deadlineAt={publicView.deadline_at}
              paused={publicView.paused}
              pausedAt={publicView.paused_at}
              phase={phase}
              revision={publicView.revision}
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
