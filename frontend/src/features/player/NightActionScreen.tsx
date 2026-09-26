import { useState } from "react";

import type {
  CommandEnvelope,
  LegalAction,
} from "../../protocol/models";

type NightActionName =
  | "WOLF_NOMINATE_KILL"
  | "SEER_INSPECT"
  | "WITCH_USE_ANTIDOTE"
  | "WITCH_USE_POISON"
  | "WITCH_SKIP";

export type NightCommandPayload =
  | { command_type: "WOLF_NOMINATE_KILL"; target_seat_id: number }
  | { command_type: "SEER_INSPECT"; target_seat_id: number }
  | { command_type: "WITCH_USE_ANTIDOTE" }
  | { command_type: "WITCH_USE_POISON"; target_seat_id: number }
  | { command_type: "WITCH_SKIP" };

const ROLE_ACTIONS: Record<string, ReadonlySet<NightActionName>> = {
  WEREWOLF: new Set(["WOLF_NOMINATE_KILL"]),
  SEER: new Set(["SEER_INSPECT"]),
  WITCH: new Set([
    "WITCH_USE_ANTIDOTE",
    "WITCH_USE_POISON",
    "WITCH_SKIP",
  ]),
};

const TARGET_ACTIONS: ReadonlySet<NightActionName> = new Set([
  "WOLF_NOMINATE_KILL",
  "SEER_INSPECT",
  "WITCH_USE_POISON",
]);

const ACTION_LABELS: Record<NightActionName, string> = {
  WOLF_NOMINATE_KILL: "袭击",
  SEER_INSPECT: "查验",
  WITCH_USE_ANTIDOTE: "使用解药",
  WITCH_USE_POISON: "使用毒药",
  WITCH_SKIP: "跳过行动",
};

const ROLE_LABELS: Record<string, string> = {
  WEREWOLF: "狼人",
  SEER: "预言家",
  WITCH: "女巫",
};

export interface NightActionScreenProps {
  role: string | null;
  actions: LegalAction[];
  disabled?: boolean;
  onAction: (action: LegalAction, targetSeatId?: number | null) => void;
}

function isNightActionName(action: string): action is NightActionName {
  return Object.hasOwn(ACTION_LABELS, action);
}

function isTargetAction(action: NightActionName): boolean {
  return TARGET_ACTIONS.has(action);
}

function uuidFromBytes(bytes: Uint8Array): string {
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}

function commandId(): string {
  const cryptoApi = globalThis.crypto as Crypto | undefined;
  if (typeof cryptoApi?.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }
  if (typeof cryptoApi?.getRandomValues === "function") {
    return uuidFromBytes(cryptoApi.getRandomValues(new Uint8Array(16)));
  }
  return `command-${Date.now().toString(36)}-${Math.random()
    .toString(36)
    .slice(2)}`;
}

function requiredTarget(
  action: LegalAction,
  seatId: number | null,
): number {
  if (seatId === null) {
    throw new Error("target seat is required");
  }
  if (!action.target_seat_ids.includes(seatId)) {
    throw new Error("target seat is not legal");
  }
  return seatId;
}

function envelope(
  roomId: string,
  expectedRevision: number,
  payload: NightCommandPayload,
): CommandEnvelope {
  return {
    schema_version: "command.v1",
    command_id: commandId(),
    room_id: roomId,
    expected_revision: expectedRevision,
    issued_at: new Date().toISOString(),
    payload,
  };
}

// The builder is exported so the pure payload contract can be tested directly.
// eslint-disable-next-line react-refresh/only-export-components
export function nightCommand(
  action: LegalAction,
  targetSeatId: number | null,
  roomId: string,
  revision: number,
): CommandEnvelope {
  return envelope(
    roomId,
    revision,
    nightPayload(action, targetSeatId),
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function nightPayload(
  action: LegalAction,
  targetSeatId: number | null,
): NightCommandPayload {
  if (!isNightActionName(action.action)) {
    throw new Error(`unsupported night action: ${action.action}`);
  }

  switch (action.action) {
    case "WOLF_NOMINATE_KILL":
      return {
        command_type: action.action,
        target_seat_id: requiredTarget(action, targetSeatId),
      };
    case "SEER_INSPECT":
      return {
        command_type: action.action,
        target_seat_id: requiredTarget(action, targetSeatId),
      };
    case "WITCH_USE_ANTIDOTE":
    case "WITCH_SKIP":
      return { command_type: action.action };
    case "WITCH_USE_POISON":
      return {
        command_type: action.action,
        target_seat_id: requiredTarget(action, targetSeatId),
      };
  }
}

function targetLabel(action: NightActionName, seatId: number): string {
  if (action === "SEER_INSPECT") return `查验 ${seatId} 号`;
  if (action === "WITCH_USE_POISON") return `毒杀 ${seatId} 号`;
  return `袭击 ${seatId} 号`;
}

export function NightActionScreen({
  role,
  actions,
  disabled = false,
  onAction,
}: NightActionScreenProps) {
  const allowedActions = ROLE_ACTIONS[role ?? ""] ?? new Set<NightActionName>();
  const legalActions = actions.filter(
    (action): action is LegalAction & { action: NightActionName } =>
      isNightActionName(action.action) && allowedActions.has(action.action),
  );
  const onlyAction =
    legalActions.length === 1 ? legalActions[0] : null;
  const [selectedAction, setSelectedAction] =
    useState<(LegalAction & { action: NightActionName }) | null>(() =>
      onlyAction !== null && isTargetAction(onlyAction.action)
        ? onlyAction
        : null,
    );
  const [selectedTarget, setSelectedTarget] = useState<number | null>(null);

  if (legalActions.length === 0) {
    return <p className="text-sm text-text-muted">等待下一步行动</p>;
  }

  const targetRequired =
    selectedAction !== null && isTargetAction(selectedAction.action);
  const canConfirm =
    selectedAction !== null &&
    (!targetRequired || selectedTarget !== null) &&
    !disabled;

  function selectAction(action: LegalAction & { action: NightActionName }) {
    setSelectedAction(action);
    setSelectedTarget(null);
  }

  function confirmAction() {
    if (selectedAction === null || !canConfirm) return;

    nightPayload(selectedAction, selectedTarget);
    onAction(selectedAction, targetRequired ? selectedTarget : null);
  }

  return (
    <section
      aria-labelledby="night-action-title"
      className="grid gap-4 rounded-lg bg-surface-raised p-5"
    >
      <div className="grid gap-1">
        <h2 id="night-action-title" className="text-lg font-semibold">
          {ROLE_LABELS[role ?? ""] ?? "夜间"}行动
        </h2>
        <p className="text-sm text-text-muted">
          {targetRequired ? "选择合法的行动目标" : "选择一项行动并确认"}
        </p>
      </div>

      <div className="grid gap-2">
        {legalActions.map((action) => (
          <button
            aria-pressed={selectedAction?.action === action.action}
            className="min-h-11 rounded-lg border border-text-muted/40 px-4 text-left font-semibold text-text disabled:cursor-not-allowed disabled:opacity-60 data-[selected=true]:bg-action data-[selected=true]:text-surface"
            data-action={action.action}
            data-selected={selectedAction?.action === action.action}
            key={action.action}
            type="button"
            onClick={() => selectAction(action)}
          >
            {ACTION_LABELS[action.action]}
          </button>
        ))}
      </div>

      {targetRequired && selectedAction !== null ? (
        <div className="grid grid-cols-2 gap-2">
          {selectedAction.target_seat_ids.map((seatId) => (
            <button
              aria-pressed={selectedTarget === seatId}
              className="min-h-11 rounded-lg border border-text-muted/40 px-3 text-sm font-semibold text-text aria-pressed:bg-action aria-pressed:text-surface"
              data-testid={`night-target-${seatId}`}
              key={seatId}
              type="button"
              onClick={() =>
                setSelectedTarget((current) =>
                  current === seatId ? null : seatId,
                )
              }
            >
              {targetLabel(selectedAction.action, seatId)}
            </button>
          ))}
        </div>
      ) : null}

      <button
        className="min-h-11 rounded-lg bg-action px-4 font-semibold text-surface disabled:cursor-not-allowed disabled:opacity-60"
        disabled={!canConfirm}
        type="button"
        onClick={confirmAction}
      >
        确认行动
      </button>
    </section>
  );
}
