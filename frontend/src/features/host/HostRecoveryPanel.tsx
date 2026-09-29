import { SlidersHorizontal } from "lucide-react";
import { useState } from "react";

import { HostCorrectionNotice } from "./HostCorrectionNotice";

const SEAT_IDS = [1, 2, 3, 4, 5, 6] as const;
const ROLES = ["WEREWOLF", "SEER", "WITCH", "VILLAGER"] as const;
const PHASES = [
  "LOBBY",
  "ROLE_REVEAL",
  "NIGHT_START",
  "NIGHT_WOLF",
  "NIGHT_SEER",
  "NIGHT_WITCH",
  "NIGHT_RESOLVE",
  "DAY_ANNOUNCE",
  "DAY_DISCUSSION",
  "DAY_VOTE",
  "DAY_PK_DISCUSSION",
  "DAY_PK_VOTE",
  "DAY_EXILE",
  "WIN_CHECK",
] as const;
const CHECKBOX_CLASS = "h-11 w-11 shrink-0";
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type RecoveryPatchType =
  | "SET_ALIVE"
  | "SET_ROLE"
  | "SET_POTION"
  | "SET_VOTE"
  | "SET_SEER_CHECKS"
  | "SET_PHASE";

export type HostRecoveryPatch =
  | { patch_type: "SET_ALIVE"; seat_id: number; alive: boolean }
  | { patch_type: "SET_ROLE"; seat_id: number; role: string }
  | {
      patch_type: "SET_POTION";
      antidote_available: boolean;
      poison_available: boolean;
    }
  | {
      patch_type: "SET_VOTE";
      voter_seat_id: number;
      round_id: string;
      target_seat_id: number | null;
    }
  | {
      patch_type: "SET_SEER_CHECKS";
      seer_seat_id: number;
      checks: Array<{
        day: number;
        target_seat_id: number;
        faction: "WEREWOLF" | "GOOD";
      }>;
    }
  | { patch_type: "SET_PHASE"; phase: string };

export interface HostRecoveryPayload {
  command_type: "HOST_PATCH";
  patch: HostRecoveryPatch;
}

export interface HostRecoveryPanelProps {
  paused: boolean;
  pending: boolean;
  commandPending?: boolean;
  onPatchSubmit: (payload: HostRecoveryPayload) => void;
}

function parseSeat(value: string): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 6 ? parsed : 1;
}

export function HostRecoveryPanel({
  paused,
  pending,
  commandPending = false,
  onPatchSubmit,
}: HostRecoveryPanelProps) {
  const [patchType, setPatchType] = useState<RecoveryPatchType>("SET_ALIVE");
  const [reason, setReason] = useState("");
  const [seatId, setSeatId] = useState("1");
  const [alive, setAlive] = useState(true);
  const [role, setRole] = useState<(typeof ROLES)[number]>("VILLAGER");
  const [antidote, setAntidote] = useState(true);
  const [poison, setPoison] = useState(true);
  const [voterSeatId, setVoterSeatId] = useState("1");
  const [roundId, setRoundId] = useState("");
  const [targetSeatId, setTargetSeatId] = useState("");
  const [seerSeatId, setSeerSeatId] = useState("1");
  const [seerCheckTargetId, setSeerCheckTargetId] = useState("1");
  const [checkDay, setCheckDay] = useState("1");
  const [checkFaction, setCheckFaction] = useState<"WEREWOLF" | "GOOD">("GOOD");
  const [phase, setPhase] = useState<(typeof PHASES)[number]>("DAY_DISCUSSION");

  const controlsDisabled = !paused || pending || commandPending;
  const reasonReady = reason.trim().length > 0 && reason.trim().length <= 200;

  function changePatchType(next: RecoveryPatchType) {
    setPatchType(next);
    if (next === "SET_VOTE") {
      setVoterSeatId("1");
      setRoundId("");
      setTargetSeatId("");
    }
    if (next === "SET_SEER_CHECKS") {
      setSeerCheckTargetId("1");
    }
  }

  function currentPatch(): HostRecoveryPatch | null {
    switch (patchType) {
      case "SET_ALIVE":
        return { patch_type: "SET_ALIVE", seat_id: parseSeat(seatId), alive };
      case "SET_ROLE":
        return { patch_type: "SET_ROLE", seat_id: parseSeat(seatId), role };
      case "SET_POTION":
        return {
          patch_type: "SET_POTION",
          antidote_available: antidote,
          poison_available: poison,
        };
      case "SET_VOTE": {
        const target = targetSeatId === "" ? null : parseSeat(targetSeatId);
        if (!UUID_PATTERN.test(roundId.trim())) return null;
        return {
          patch_type: "SET_VOTE",
          voter_seat_id: parseSeat(voterSeatId),
          round_id: roundId.trim(),
          target_seat_id: target,
        };
      }
      case "SET_SEER_CHECKS": {
        const day = Number(checkDay);
        if (!Number.isInteger(day) || day < 0) return null;
        return {
          patch_type: "SET_SEER_CHECKS",
          seer_seat_id: parseSeat(seerSeatId),
          checks: [
            {
              day,
              target_seat_id: parseSeat(seerCheckTargetId),
              faction: checkFaction,
            },
          ],
        };
      }
      case "SET_PHASE":
        return { patch_type: "SET_PHASE", phase };
    }
  }

  const patch = currentPatch();
  const canSubmit =
    paused && !pending && !commandPending && reasonReady && patch !== null;

  function submit() {
    if (!canSubmit || patch === null) return;
    onPatchSubmit({ command_type: "HOST_PATCH", patch });
  }

  return (
    <section aria-labelledby="host-recovery-title" className="grid gap-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <SlidersHorizontal aria-hidden="true" className="mt-1" size={21} />
          <div className="grid gap-1">
            <h2 className="text-lg font-semibold" id="host-recovery-title">
              主持人纠错
            </h2>
          </div>
        </div>
      </div>

      <HostCorrectionNotice paused={paused} pending={pending} />

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="grid gap-1 text-sm font-medium">
          修正类型
          <select
            className="min-h-11 rounded-md border border-text-muted/40 bg-surface px-3 text-text"
            disabled={controlsDisabled}
            onChange={(event) =>
              changePatchType(event.target.value as RecoveryPatchType)
            }
            value={patchType}
          >
            <option value="SET_ALIVE">存活状态</option>
            <option value="SET_ROLE">角色</option>
            <option value="SET_POTION">药水</option>
            <option value="SET_VOTE">投票</option>
            <option value="SET_SEER_CHECKS">预言家查验</option>
            <option value="SET_PHASE">阶段</option>
          </select>
        </label>

        {patchType === "SET_POTION" ? (
          <div className="grid content-end gap-2">
            <label className="inline-flex min-h-11 items-center gap-2 text-sm font-medium">
              <input
                checked={antidote}
                className={CHECKBOX_CLASS}
                disabled={controlsDisabled}
                onChange={(event) => setAntidote(event.target.checked)}
                type="checkbox"
              />
              解药可用
            </label>
            <label className="inline-flex min-h-11 items-center gap-2 text-sm font-medium">
              <input
                checked={poison}
                className={CHECKBOX_CLASS}
                disabled={controlsDisabled}
                onChange={(event) => setPoison(event.target.checked)}
                type="checkbox"
              />
              毒药可用
            </label>
          </div>
        ) : patchType === "SET_ALIVE" || patchType === "SET_ROLE" ? (
          <label className="grid gap-1 text-sm font-medium">
            座位
            <select
              className="min-h-11 rounded-md border border-text-muted/40 bg-surface px-3 text-text"
              disabled={controlsDisabled}
              onChange={(event) => setSeatId(event.target.value)}
              value={seatId}
            >
              {SEAT_IDS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        ) : patchType === "SET_SEER_CHECKS" ? (
          <label className="grid gap-1 text-sm font-medium">
            查验目标
            <select
              className="min-h-11 rounded-md border border-text-muted/40 bg-surface px-3 text-text"
              disabled={controlsDisabled}
              onChange={(event) => setSeerCheckTargetId(event.target.value)}
              value={seerCheckTargetId}
            >
              {SEAT_IDS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {patchType === "SET_ALIVE" ? (
          <label className="inline-flex min-h-11 items-center gap-2 text-sm font-medium">
            <input
              checked={alive}
              className={CHECKBOX_CLASS}
              disabled={controlsDisabled}
              onChange={(event) => setAlive(event.target.checked)}
              type="checkbox"
            />
            存活
          </label>
        ) : null}

        {patchType === "SET_ROLE" ? (
          <label className="grid gap-1 text-sm font-medium">
            角色
            <select
              className="min-h-11 rounded-md border border-text-muted/40 bg-surface px-3 text-text"
              disabled={controlsDisabled}
              onChange={(event) =>
                setRole(event.target.value as (typeof ROLES)[number])
              }
              value={role}
            >
              {ROLES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        ) : null}

        {patchType === "SET_VOTE" ? (
          <>
            <label className="grid gap-1 text-sm font-medium">
              投票轮次
              <input
                className="min-h-11 rounded-md border border-text-muted/40 bg-surface px-3 font-mono text-sm text-text"
                disabled={controlsDisabled}
                onChange={(event) => setRoundId(event.target.value)}
                value={roundId}
              />
            </label>
            <label className="grid gap-1 text-sm font-medium">
              投票者
              <select
                className="min-h-11 rounded-md border border-text-muted/40 bg-surface px-3 text-text"
                disabled={controlsDisabled}
                onChange={(event) => setVoterSeatId(event.target.value)}
                value={voterSeatId}
              >
                {SEAT_IDS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1 text-sm font-medium">
              投票目标
              <select
                className="min-h-11 rounded-md border border-text-muted/40 bg-surface px-3 text-text"
                disabled={controlsDisabled}
                onChange={(event) => setTargetSeatId(event.target.value)}
                value={targetSeatId}
              >
                <option value="">弃票</option>
                {SEAT_IDS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
          </>
        ) : null}

        {patchType === "SET_SEER_CHECKS" ? (
          <>
            <label className="grid gap-1 text-sm font-medium">
              预言家座位
              <select
                className="min-h-11 rounded-md border border-text-muted/40 bg-surface px-3 text-text"
                disabled={controlsDisabled}
                onChange={(event) => setSeerSeatId(event.target.value)}
                value={seerSeatId}
              >
                {SEAT_IDS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1 text-sm font-medium">
              查验天数
              <input
                className="min-h-11 rounded-md border border-text-muted/40 bg-surface px-3 text-text"
                disabled={controlsDisabled}
                min={1}
                onChange={(event) => setCheckDay(event.target.value)}
                type="number"
                value={checkDay}
              />
            </label>
            <label className="grid gap-1 text-sm font-medium">
              查验结果
              <select
                className="min-h-11 rounded-md border border-text-muted/40 bg-surface px-3 text-text"
                disabled={controlsDisabled}
                onChange={(event) =>
                  setCheckFaction(event.target.value as "WEREWOLF" | "GOOD")
                }
                value={checkFaction}
              >
                <option value="GOOD">好人</option>
                <option value="WEREWOLF">狼人</option>
              </select>
            </label>
          </>
        ) : null}

        {patchType === "SET_PHASE" ? (
          <label className="grid gap-1 text-sm font-medium">
            阶段
            <select
              className="min-h-11 rounded-md border border-text-muted/40 bg-surface px-3 text-text"
              disabled={controlsDisabled}
              onChange={(event) =>
                setPhase(event.target.value as (typeof PHASES)[number])
              }
              value={phase}
            >
              {PHASES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      <label className="grid gap-1 text-sm font-medium">
        确认原因
        <input
          className="min-h-11 rounded-md border border-text-muted/40 bg-surface px-3 text-text"
          disabled={controlsDisabled}
          maxLength={200}
          onChange={(event) => setReason(event.target.value)}
          value={reason}
        />
      </label>

      <button
        className="inline-flex min-h-11 items-center justify-center rounded-lg bg-action px-4 font-semibold text-surface disabled:cursor-not-allowed disabled:opacity-50"
        disabled={!canSubmit}
        onClick={submit}
        type="button"
      >
        {pending ? "正在提交" : "提交主持人纠错"}
      </button>
    </section>
  );
}
