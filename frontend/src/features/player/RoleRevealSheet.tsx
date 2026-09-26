import { useEffect, useId, useState } from "react";

import type { PrivateFact } from "../../protocol/models";

export interface RoleRevealSheetProps {
  role: string | null;
  privateFacts?: PrivateFact[];
  revealed?: boolean;
}

const ROLE_LABELS: Record<string, string> = {
  WEREWOLF: "狼人",
  SEER: "预言家",
  WITCH: "女巫",
  VILLAGER: "村民",
};

function isActivationKey(key: string): boolean {
  return key === "Enter" || key === " ";
}

function seatList(value: unknown): number[] {
  return Array.isArray(value)
    ? value.filter((item): item is number => Number.isInteger(item))
    : [];
}

function formatPrivateFact(fact: PrivateFact): string {
  if (fact.fact_type === "WOLF_TEAM") {
    const seats = seatList(fact.payload.seat_ids);
    return seats.length === 0
      ? "狼人队友：无"
      : `狼人队友：${seats.join("、")} 号`;
  }
  if (fact.fact_type === "WITCH_POTIONS") {
    const antidote = fact.payload.antidote_available === true ? "可用" : "已用";
    const poison = fact.payload.poison_available === true ? "可用" : "已用";
    return `解药：${antidote}；毒药：${poison}`;
  }
  if (fact.fact_type === "SEER_CHECK") {
    const day = Number.isInteger(fact.payload.day) ? fact.payload.day : "?";
    const target = Number.isInteger(fact.payload.target_seat_id)
      ? fact.payload.target_seat_id
      : "?";
    const faction =
      fact.payload.faction === "WEREWOLF"
        ? "狼人阵营"
        : fact.payload.faction === "GOOD"
          ? "好人阵营"
          : "未知阵营";
    return `第 ${String(day)} 夜查验：${String(target)} 号是${faction}`;
  }
  return "有一条私密信息";
}

export function RoleRevealSheet({
  role,
  privateFacts = [],
  revealed = false,
}: RoleRevealSheetProps) {
  const [open, setOpen] = useState(revealed);
  const contentId = useId();

  useEffect(() => {
    const hide = () => setOpen(false);
    const handleVisibility = () => {
      if (document.visibilityState === "hidden") hide();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.target === document.body && isActivationKey(event.key)) {
        setOpen(true);
      }
    };
    const handleKeyUp = (event: KeyboardEvent) => {
      if (event.target === document.body && isActivationKey(event.key)) {
        setOpen(false);
      }
    };

    window.addEventListener("blur", hide);
    window.addEventListener("pointerup", hide);
    window.addEventListener("pointercancel", hide);
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      window.removeEventListener("blur", hide);
      window.removeEventListener("pointerup", hide);
      window.removeEventListener("pointercancel", hide);
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, []);

  const roleLabel = ROLE_LABELS[role ?? ""] ?? "身份未知";

  return (
    <section className="grid gap-4 rounded-lg bg-surface-raised p-5">
      <button
        aria-controls={contentId}
        aria-expanded={open}
        className="min-h-11 touch-none rounded-lg border border-text-muted/40 px-4 font-semibold text-text"
        type="button"
        onBlur={() => setOpen(false)}
        onClick={(event) => {
          if (event.detail === 0) setOpen((value) => !value);
        }}
        onKeyDown={(event) => {
          if (isActivationKey(event.key)) {
            event.preventDefault();
            setOpen(true);
          }
        }}
        onKeyUp={(event) => {
          if (isActivationKey(event.key)) {
            event.preventDefault();
            setOpen(false);
          }
        }}
        onPointerDown={(event) => {
          if (event.button === 0) setOpen(true);
        }}
      >
        按住查看身份
      </button>

      {open ? (
        <div
          aria-live="polite"
          className="grid gap-3 border-t border-text-muted/30 pt-4"
          id={contentId}
        >
          <p className="text-2xl font-semibold">{roleLabel}</p>
          {privateFacts.length === 0 ? null : (
            <ul className="grid gap-2 text-sm text-text-muted">
              {privateFacts.map((fact) => (
                <li key={fact.fact_id}>{formatPrivateFact(fact)}</li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </section>
  );
}
