import { Link } from "react-router-dom";

import type { SeatView } from "../../protocol/models";

export interface GameEndScreenProps {
  roomCode: string;
  seatView: SeatView;
}

const ROLE_LABELS: Record<string, string> = {
  WEREWOLF: "狼人",
  SEER: "预言家",
  WITCH: "女巫",
  VILLAGER: "村民",
};

const ALL_SEATS = [1, 2, 3, 4, 5, 6];

export function GameEndScreen({
  roomCode,
  seatView,
}: GameEndScreenProps) {
  const winnerStatement =
    [...seatView.public_timeline]
      .reverse()
      .find((item) => item.event_type === "GAME_ENDED")?.statement ??
    "游戏已结束";
  const livingSeats = ALL_SEATS.filter((seatId) =>
    seatView.living_seats.includes(seatId),
  );
  const deadSeats = ALL_SEATS.filter(
    (seatId) => !seatView.living_seats.includes(seatId),
  );
  const role =
    seatView.role === null
      ? "未知"
      : (ROLE_LABELS[seatView.role] ?? "未知");
  const replayPath = `/replay/${encodeURIComponent(
    roomCode,
  )}?revision=${seatView.revision}`;

  return (
    <section
      aria-labelledby="game-end-title"
      className="grid gap-4 rounded-lg bg-surface-raised p-5"
    >
      <h2 id="game-end-title" className="text-xl font-semibold">
        终局结果
      </h2>
      <p className="text-lg font-semibold text-accent-text">
        {winnerStatement}
      </p>
      <p>{`你的身份：${role}`}</p>
      <p>
        {`存活座位：${
          livingSeats.length === 0 ? "无" : `${livingSeats.join("、")} 号`
        }`}
      </p>
      <p>
        {`出局座位：${
          deadSeats.length === 0 ? "无" : `${deadSeats.join("、")} 号`
        }`}
      </p>
      <Link
        className="inline-flex min-h-11 items-center justify-center rounded-lg bg-action px-4 font-semibold text-surface focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action"
        to={replayPath}
      >
        查看玩家回放
      </Link>
    </section>
  );
}
