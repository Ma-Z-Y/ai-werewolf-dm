import { useState } from "react";

import type { LegalAction } from "../../protocol/models";

const MAX_SPEECH_LENGTH = 1000;

type DayActionName = "SPEAK" | "PASS_SPEECH" | "VOTE" | "ABSTAIN";

export type DayCommandPayload =
  | { command_type: "SPEAK"; text: string }
  | { command_type: "PASS_SPEECH" }
  | { command_type: "VOTE"; target_seat_id: number }
  | { command_type: "ABSTAIN" };

export interface DayDiscussionScreenProps {
  seatId: number;
  currentSpeakerSeatId: number | null;
  phase: string;
  actions: LegalAction[];
  deadlineAt: string | null;
  disabled?: boolean;
  onAction: (
    action: LegalAction,
    targetSeatId?: number | null,
    text?: string | null,
  ) => void;
}

// eslint-disable-next-line react-refresh/only-export-components
export function isDayActionName(action: string): action is DayActionName {
  return (
    action === "SPEAK" ||
    action === "PASS_SPEECH" ||
    action === "VOTE" ||
    action === "ABSTAIN"
  );
}

function requiredTarget(
  action: LegalAction,
  seatId: number | null,
): number {
  if (seatId === null || !action.target_seat_ids.includes(seatId)) {
    throw new Error("target seat is not legal");
  }
  return seatId;
}

// eslint-disable-next-line react-refresh/only-export-components
export function dayPayload(
  action: LegalAction,
  targetSeatId: number | null = null,
  text: string | null = null,
): DayCommandPayload {
  if (!isDayActionName(action.action)) {
    throw new Error(`unsupported day action: ${action.action}`);
  }

  switch (action.action) {
    case "SPEAK": {
      const trimmed = text?.trim() ?? "";
      if (trimmed.length === 0 || trimmed.length > MAX_SPEECH_LENGTH) {
        throw new Error("speech text is invalid");
      }
      return { command_type: action.action, text: trimmed };
    }
    case "PASS_SPEECH":
    case "ABSTAIN":
      return { command_type: action.action };
    case "VOTE":
      return {
        command_type: action.action,
        target_seat_id: requiredTarget(action, targetSeatId),
      };
  }
}

function formatDeadline(deadlineAt: string | null): string {
  if (deadlineAt === null) return "等待同步";
  const deadline = new Date(deadlineAt);
  if (Number.isNaN(deadline.getTime())) return "等待同步";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(deadline);
}

export function DayDiscussionScreen({
  seatId,
  currentSpeakerSeatId,
  phase,
  actions,
  deadlineAt,
  disabled = false,
  onAction,
}: DayDiscussionScreenProps) {
  const [text, setText] = useState("");
  const speakAction =
    actions.find((action) => action.action === "SPEAK") ?? null;
  const passAction =
    actions.find((action) => action.action === "PASS_SPEECH") ?? null;
  const isPk =
    phase === "DAY_PK_DISCUSSION" || phase === "DAY_PK_VOTE";
  const isCurrentSpeaker = currentSpeakerSeatId === seatId;
  const trimmedText = text.trim();

  return (
    <section
      aria-labelledby="day-discussion-title"
      className="grid gap-4 rounded-lg bg-surface-raised p-5"
    >
      <div className="grid gap-1">
        <h2 id="day-discussion-title" className="text-lg font-semibold">
          {isCurrentSpeaker ? "轮到你发言" : "当前玩家发言"}
        </h2>
        {isPk ? (
          <p data-testid="pk-phase" className="text-sm font-semibold text-action">
            PK 发言
          </p>
        ) : null}
        <time className="text-sm text-text-muted" dateTime={deadlineAt ?? undefined}>
          {`截止时间 ${formatDeadline(deadlineAt)}`}
        </time>
      </div>

      {!isCurrentSpeaker || (speakAction === null && passAction === null) ? (
        <p className="text-sm text-text-muted">等待当前玩家完成发言</p>
      ) : (
        <div className="grid gap-3" data-testid="speech-action">
          {speakAction === null ? null : (
            <>
              <label
                className="grid gap-2 text-sm font-medium"
                htmlFor="speech-text"
              >
                发言内容
              </label>
              <textarea
                aria-label="发言内容"
                className="min-h-28 rounded-lg border border-text-muted/40 bg-surface px-3 py-2 text-base text-text"
                id="speech-text"
                maxLength={MAX_SPEECH_LENGTH}
                value={text}
                onChange={(event) =>
                  setText(event.target.value.slice(0, MAX_SPEECH_LENGTH))
                }
              />
              <button
                className="min-h-11 rounded-lg bg-action px-4 font-semibold text-surface disabled:cursor-not-allowed disabled:opacity-60"
                disabled={disabled || trimmedText.length === 0}
                type="button"
                onClick={() => {
                  dayPayload(speakAction, null, trimmedText);
                  onAction(speakAction, null, trimmedText);
                }}
              >
                发送发言
              </button>
            </>
          )}

          {passAction === null ? null : (
            <button
              className="min-h-11 rounded-lg border border-text-muted/40 px-4 font-semibold text-text disabled:cursor-not-allowed disabled:opacity-60"
              disabled={disabled}
              type="button"
              onClick={() => onAction(passAction, null, null)}
            >
              跳过发言
            </button>
          )}
        </div>
      )}
    </section>
  );
}
