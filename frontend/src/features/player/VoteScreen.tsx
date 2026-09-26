import { useState } from "react";

import type { LegalAction, VoteSummary } from "../../protocol/models";

export interface VoteScreenProps {
  phase: string;
  actions: LegalAction[];
  voteSummary: VoteSummary | null;
  disabled?: boolean;
  onAction: (action: LegalAction, targetSeatId?: number | null) => void;
}

export function VoteScreen({
  phase,
  actions,
  voteSummary,
  disabled = false,
  onAction,
}: VoteScreenProps) {
  const [selectedTarget, setSelectedTarget] = useState<number | null>(null);
  const voteAction =
    actions.find((action) => action.action === "VOTE") ?? null;
  const abstainAction =
    actions.find((action) => action.action === "ABSTAIN") ?? null;
  const targets = voteAction?.target_seat_ids ?? [];
  const isPk = phase === "DAY_PK_VOTE";
  const effectiveSelectedTarget =
    selectedTarget !== null && targets.includes(selectedTarget)
      ? selectedTarget
      : null;
  const canConfirm =
    !disabled &&
    voteAction !== null &&
    effectiveSelectedTarget !== null;

  return (
    <section
      aria-labelledby="vote-title"
      className="grid gap-4 rounded-lg bg-surface-raised p-5"
    >
      <div className="grid gap-1">
        <h2 id="vote-title" className="text-lg font-semibold">
          投票
        </h2>
        {isPk ? (
          <p data-testid="pk-phase" className="text-sm font-semibold text-action">
            PK 投票
          </p>
        ) : null}
        {voteSummary === null ? null : (
          <p className="text-sm text-text-muted">
            {`已提交 ${voteSummary.submitted_count} / ${voteSummary.eligible_count}`}
          </p>
        )}
      </div>

      {voteAction === null && abstainAction === null ? (
        <p className="text-sm text-text-muted">等待其他玩家投票</p>
      ) : null}

      {targets.length === 0 ? null : (
        <div className="grid grid-cols-2 gap-2">
          {targets.map((seatId) => (
            <button
              aria-pressed={effectiveSelectedTarget === seatId}
              className="min-h-11 rounded-lg border border-text-muted/40 px-3 text-sm font-semibold text-text aria-pressed:bg-action aria-pressed:text-surface disabled:cursor-not-allowed disabled:opacity-60"
              data-testid={`vote-target-${seatId}`}
              disabled={disabled}
              key={seatId}
              type="button"
              onClick={() =>
                setSelectedTarget((current) =>
                  current === seatId ? null : seatId,
                )
              }
            >
              {`投给 ${seatId} 号`}
            </button>
          ))}
        </div>
      )}

      {voteAction === null ? null : (
        <button
          className="min-h-11 rounded-lg bg-action px-4 font-semibold text-surface disabled:cursor-not-allowed disabled:opacity-60"
          disabled={!canConfirm}
          type="button"
          onClick={() => {
            if (!canConfirm) return;
            onAction(voteAction, effectiveSelectedTarget);
          }}
        >
          确认投票
        </button>
      )}

      {abstainAction === null ? null : (
        <button
          className="min-h-11 rounded-lg border border-text-muted/40 px-4 font-semibold text-text disabled:cursor-not-allowed disabled:opacity-60"
          disabled={disabled}
          type="button"
          onClick={() => onAction(abstainAction, null)}
        >
          弃票
        </button>
      )}
    </section>
  );
}
