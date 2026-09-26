import { Check, UsersRound, Wifi, WifiOff } from "lucide-react";

import type { SeatView } from "../../protocol/models";
import type { RoomSocketState } from "../../realtime/RoomSocket";

export interface PlayerLobbyScreenProps {
  roomCode: string;
  seatId: number;
  connectionState: RoomSocketState;
  seatView: SeatView | null;
  joinPending: boolean;
  ready: boolean;
  readyPending: boolean;
  errorMessage: string | null;
  onToggleReady: () => void;
}

const CONNECTION_LABELS: Record<RoomSocketState, string> = {
  idle: "等待连接",
  connecting: "连接中",
  awaiting_auth: "正在认证",
  ready: "已连接",
  retry_wait: "连接中断，正在重连",
  closed: "连接已关闭",
};

function ConnectionStatus({ state }: { state: RoomSocketState }) {
  const connected = state === "ready";
  const Icon = connected ? Wifi : WifiOff;
  return (
    <p
      className={`inline-flex items-center gap-2 text-sm ${
        connected ? "text-success" : "text-text-muted"
      }`}
      role="status"
    >
      <Icon aria-hidden="true" size={16} />
      {CONNECTION_LABELS[state]}
    </p>
  );
}

export function PlayerLobbyScreen({
  roomCode,
  seatId,
  connectionState,
  seatView,
  joinPending,
  ready,
  readyPending,
  errorMessage,
  onToggleReady,
}: PlayerLobbyScreenProps) {
  const hasJoinAction =
    seatView?.legal_actions.some((action) => action.action === "JOIN_ROOM") ===
    true;
  const isLiving =
    seatView === null ||
    hasJoinAction ||
    seatView.living_seats.includes(seatId);
  const canSetReady =
    isLiving &&
    seatView?.legal_actions.some((action) => action.action === "SET_READY") ===
      true;
  const participationLabel = joinPending
    ? "正在加入房间"
    : isLiving
      ? "等待其他玩家"
      : "你已出局";

  return (
    <main className="min-h-screen bg-surface px-5 py-8 text-text sm:px-8">
      <div className="mx-auto grid w-full max-w-md gap-6">
        <header className="grid gap-2">
          <p className="text-sm font-medium text-text-muted">{`房间 ${roomCode}`}</p>
          <h1 className="text-3xl font-semibold">玩家大厅</h1>
          <p className="text-base text-text-muted">{`座位 ${seatId}`}</p>
        </header>

        <section
          aria-labelledby="lobby-status-title"
          className="grid gap-5 rounded-lg bg-surface-raised p-5"
        >
          <div className="flex items-center justify-between gap-4">
            <h2 id="lobby-status-title" className="text-xl font-semibold">
              等待开局
            </h2>
            <ConnectionStatus state={connectionState} />
          </div>

          <div className="grid gap-3">
            <p className="inline-flex items-center gap-2 text-base">
              <UsersRound aria-hidden="true" size={18} />
              {participationLabel}
            </p>
            {ready ? (
              <p className="inline-flex items-center gap-2 text-sm text-success">
                <Check aria-hidden="true" size={16} />
                已准备
              </p>
            ) : null}
          </div>

          {errorMessage === null ? null : (
            <p className="text-sm text-danger" role="alert">
              {errorMessage}
            </p>
          )}

          {canSetReady ? (
            <button
              className="inline-flex min-h-11 items-center justify-center rounded-lg bg-action px-4 font-semibold text-surface"
              disabled={readyPending}
              type="button"
              onClick={onToggleReady}
            >
              {ready ? "取消准备" : "准备"}
            </button>
          ) : null}
        </section>
      </div>
    </main>
  );
}
