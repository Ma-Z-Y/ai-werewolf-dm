import type { PublicTimelineItem } from "../../protocol/models";

export interface StageTimelineProps {
  items: PublicTimelineItem[];
}

export function StageTimeline({ items }: StageTimelineProps) {
  if (items.length === 0) {
    return <p className="text-base text-text-muted day:text-surface">暂无公开事件</p>;
  }

  return (
    <ol className="grid gap-3" data-testid="stage-timeline">
      {items.map((item) => (
        <li
          className="border-l-2 border-action pl-4 text-lg leading-relaxed text-text day:border-text day:text-surface"
          key={item.event_id}
        >
          {item.statement}
        </li>
      ))}
    </ol>
  );
}
