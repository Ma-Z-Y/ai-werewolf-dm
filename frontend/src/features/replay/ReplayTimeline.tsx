import type { PrivateFact, PublicTimelineItem } from "../../protocol/models";

export interface ReplayTimelineProps {
  publicTimeline: PublicTimelineItem[];
  privateFacts: PrivateFact[];
}

function privateFactStatement(fact: PrivateFact): string {
  const targetSeatId = fact.payload.target_seat_id;

  if (fact.fact_type === "SEER_CHECK") {
    if (typeof targetSeatId !== "number") {
      return "有一条私密查验记录";
    }
    const faction =
      fact.payload.faction === "WEREWOLF"
        ? "狼人"
        : fact.payload.faction === "GOOD"
          ? "好人"
          : "未知阵营";
    return `预言家查验 ${targetSeatId} 号为${faction}`;
  }

  if (fact.fact_type === "WITCH_POTIONS") {
    const antidote =
      fact.payload.antidote_available === true ? "解药可用" : "解药不可用";
    const poison =
      fact.payload.poison_available === true ? "毒药可用" : "毒药不可用";
    return `${antidote}，${poison}`;
  }

  if (fact.fact_type === "WITCH_KILL_TARGET") {
    return typeof targetSeatId === "number"
      ? `今夜狼人袭击 ${targetSeatId} 号`
      : "今夜狼人选择空刀";
  }

  if (fact.fact_type === "WOLF_TEAM") {
    const seatIds = Array.isArray(fact.payload.seat_ids)
      ? fact.payload.seat_ids.filter(
          (seatId): seatId is number => typeof seatId === "number",
        )
      : [];
    return seatIds.length === 0
      ? "狼人队友信息已记录"
      : `狼人队友：${seatIds.join("、")} 号`;
  }

  if (fact.fact_type === "WOLF_DECISION") {
    return typeof targetSeatId === "number"
      ? `狼队决定袭击 ${targetSeatId} 号`
      : "狼队决定空刀";
  }

  return "有一条私密记录";
}

export function ReplayTimeline({
  publicTimeline,
  privateFacts,
}: ReplayTimelineProps) {
  return (
    <div className="grid gap-5">
      <section
        aria-labelledby="replay-public-title"
        className="grid gap-3 rounded-lg bg-surface-raised p-5"
      >
        <h2 id="replay-public-title" className="text-xl font-semibold">
          公开时间线
        </h2>
        {publicTimeline.length === 0 ? (
          <p className="text-sm text-text-muted">暂无公开事件</p>
        ) : (
          <ol className="grid gap-3" data-testid="replay-public-timeline">
            {publicTimeline.map((item) => (
              <li
                className="border-l-2 border-action pl-4 text-base leading-relaxed"
                key={item.event_id}
              >
                {item.statement}
              </li>
            ))}
          </ol>
        )}
      </section>

      <section
        aria-labelledby="replay-private-title"
        className="grid gap-3 rounded-lg bg-surface-raised p-5"
      >
        <h2 id="replay-private-title" className="text-xl font-semibold">
          我的私密记录
        </h2>
        {privateFacts.length === 0 ? (
          <p className="text-sm text-text-muted">暂无本人私密记录</p>
        ) : (
          <ol className="grid gap-3" data-testid="replay-private-timeline">
            {privateFacts.map((fact) => (
              <li
                className="border-l-2 border-accent pl-4 text-base leading-relaxed"
                key={fact.fact_id}
              >
                {privateFactStatement(fact)}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
