import { Check, CircleX, Moon, Sun, Wifi, WifiOff } from "lucide-react";

import type { LegalAction, SeatView } from "../../protocol/models";
import type { RoomSocketState } from "../../realtime/RoomSocket";

import { DayDiscussionScreen } from "./DayDiscussionScreen";
import { GameEndScreen } from "./GameEndScreen";
import { NightActionScreen } from "./NightActionScreen";
import { RoleRevealSheet } from "./RoleRevealSheet";
import { VoteScreen } from "./VoteScreen";

export interface SeatShellProps {
  roomCode: string;
  seatId: number;
  connectionState: RoomSocketState;
  seatView: SeatView;
  ready: boolean;
  actionPending: boolean;
  actionConfirmed: boolean;
  errorMessage: string | null;
  onAction: (action: LegalAction, targetSeatId?: number | null) => void;
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
  LOBBY: "玩家准备",
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

function ConnectionStatus({ state }: { state: RoomSocketState }) {
  const connected = state === "ready";
  const Icon = connected ? Wifi : WifiOff;
  return (
    <span
      className={`inline-flex items-center gap-2 text-sm ${
        connected ? "text-success" : "text-text-muted"
      }`}
      role="status"
    >
      <Icon aria-hidden="true" size={16} />
      {CONNECTION_LABELS[state]}
    </span>
  );
}

export function SeatShell({
  roomCode,
  seatId,
  connectionState,
  seatView,
  ready,
  actionPending,
  actionConfirmed,
  errorMessage,
  onAction,
}: SeatShellProps) {
  const isNight = seatView.phase.startsWith("NIGHT");
  const PhaseIcon = isNight ? Moon : Sun;
  const hasJoinAction = seatView.legal_actions.some(
    (action) => action.action === "JOIN_ROOM",
  );
  const isLiving =
    hasJoinAction || seatView.living_seats.includes(seatId);
  const confirmAction =
    seatView.legal_actions.find(
      (legalAction) => legalAction.action === "CONFIRM_ROLE",
    ) ?? null;
  const isDayDiscussion =
    seatView.phase === "DAY_DISCUSSION" ||
    seatView.phase === "DAY_PK_DISCUSSION";
  const isDayVote =
    seatView.phase === "DAY_VOTE" || seatView.phase === "DAY_PK_VOTE";
  const isCurrentSpeaker = seatView.legal_actions.some(
    (action) =>
      action.action === "SPEAK" || action.action === "PASS_SPEECH",
  );
  const isGameEnd = seatView.phase === "GAME_END";
  const recentTimeline = seatView.public_timeline.slice(-3);

  return (
    <main className="min-h-screen bg-surface px-5 py-8 text-text sm:px-8">
      <div className="mx-auto grid w-full max-w-md gap-6">
        <header className="grid gap-2">
          <p className="text-sm font-medium text-text-muted">{`房间 ${roomCode}`}</p>
          <h1 className="text-3xl font-semibold">玩家席</h1>
          <p className="inline-flex items-center gap-2 text-base text-text-muted">
            {`座位 ${seatId}`}
            <ConnectionStatus state={connectionState} />
          </p>
        </header>

        <section className="grid gap-4 rounded-lg bg-surface-raised p-5">
          <div className="flex items-center justify-between gap-4">
            <h2 className="inline-flex items-center gap-2 text-xl font-semibold">
              <PhaseIcon aria-hidden="true" size={20} />
              {PHASE_LABELS[seatView.phase] ?? seatView.phase}
            </h2>
            <span className="text-sm text-text-muted">{`第 ${seatView.day} 天`}</span>
          </div>
          <div className="flex flex-wrap gap-4 text-sm">
            <span className="inline-flex items-center gap-2">
              {isLiving ? (
                <Check aria-hidden="true" size={16} className="text-success" />
              ) : (
                <CircleX aria-hidden="true" size={16} className="text-danger" />
              )}
              {isLiving ? "存活" : "已出局"}
            </span>
            <span className="text-text-muted">
              {seatView.phase === "LOBBY"
                ? ready
                  ? "已准备"
                  : "未准备"
                : "已开局"}
            </span>
          </div>
        </section>

        <section
          aria-labelledby="seat-timeline-title"
          className="grid gap-3 rounded-lg bg-surface-raised p-5"
        >
          <h2 id="seat-timeline-title" className="text-lg font-semibold">
            公共时间线
          </h2>
          {recentTimeline.length === 0 ? (
            <p className="text-sm text-text-muted">暂无公开事件</p>
          ) : (
            <ol className="grid gap-2">
              {recentTimeline.map((item) => (
                <li className="text-sm text-text-muted" key={item.event_id}>
                  {item.statement}
                </li>
              ))}
            </ol>
          )}
        </section>

        <RoleRevealSheet
          privateFacts={seatView.private_facts}
          role={seatView.role}
        />

        {isGameEnd ? (
          <GameEndScreen roomCode={roomCode} seatView={seatView} />
        ) : confirmAction === null ? (
          isNight ? (
            <NightActionScreen
              actions={seatView.legal_actions}
              disabled={actionPending}
              key={`${seatView.revision}:${seatView.phase}`}
              onAction={onAction}
              role={seatView.role}
            />
          ) : isDayDiscussion ? (
            <DayDiscussionScreen
              actions={seatView.legal_actions}
              currentSpeakerSeatId={isCurrentSpeaker ? seatId : null}
              deadlineAt={seatView.deadline_at}
              disabled={actionPending}
              key={seatView.phase}
              onAction={onAction}
              phase={seatView.phase}
              seatId={seatId}
            />
          ) : isDayVote ? (
            <VoteScreen
              actions={seatView.legal_actions}
              disabled={actionPending}
              key={`${seatView.phase}:${seatView.vote_summary?.round_id ?? "none"}`}
              onAction={onAction}
              phase={seatView.phase}
              voteSummary={seatView.vote_summary}
            />
          ) : (
            <p className="text-sm text-text-muted">等待下一步行动</p>
          )
        ) : (
          <button
            className="min-h-11 rounded-lg bg-action px-4 font-semibold text-surface disabled:cursor-not-allowed disabled:opacity-60"
            disabled={actionPending || actionConfirmed}
            type="button"
            onClick={() => onAction(confirmAction)}
          >
            {actionPending
              ? "处理中"
              : actionConfirmed
                ? "已确认"
                : "确认身份"}
          </button>
        )}

        {errorMessage === null ? null : (
          <p className="text-sm text-danger" role="alert">
            {errorMessage}
          </p>
        )}
      </div>
    </main>
  );
}
