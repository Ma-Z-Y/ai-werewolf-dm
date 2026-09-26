export type ErrorCode =
  | "BAD_REQUEST"
  | "ROOM_NOT_FOUND"
  | "ROOM_FULL"
  | "ROOM_LIMIT_REACHED"
  | "TOKEN_INVALID"
  | "TOKEN_EXPIRED"
  | "CHANNEL_FORBIDDEN"
  | "ACTOR_NOT_AUTHORIZED"
  | "ILLEGAL_PHASE"
  | "INVALID_TARGET"
  | "REVISION_CONFLICT"
  | "PLAYER_DEAD"
  | "NOT_CURRENT_SPEAKER"
  | "VOTE_ROUND_CLOSED"
  | "WOLF_CONSENSUS_PENDING"
  | "POTION_ALREADY_USED"
  | "WITCH_SELF_RESCUE_FORBIDDEN"
  | "GAME_ENDED"
  | "HOST_RECOVERY_NOT_IN_S1"
  | "RATE_LIMITED"
  | "CONNECTION_LAG"
  | "INTERNAL_ERROR"
  | "NETWORK_ERROR"
  | "UNKNOWN_ERROR";

const SAFE_MESSAGES: Record<ErrorCode, string> = {
  BAD_REQUEST: "请求不合法",
  ROOM_NOT_FOUND: "房间不存在",
  ROOM_FULL: "房间已满",
  ROOM_LIMIT_REACHED: "服务器房间容量已达上限",
  TOKEN_INVALID: "令牌无效",
  TOKEN_EXPIRED: "令牌已过期",
  CHANNEL_FORBIDDEN: "频道不可用",
  ACTOR_NOT_AUTHORIZED: "操作未授权",
  ILLEGAL_PHASE: "当前阶段不允许该操作",
  INVALID_TARGET: "目标不合法",
  REVISION_CONFLICT: "状态版本冲突",
  PLAYER_DEAD: "玩家已出局",
  NOT_CURRENT_SPEAKER: "尚未轮到你发言",
  VOTE_ROUND_CLOSED: "投票已结束",
  WOLF_CONSENSUS_PENDING: "狼人尚未达成一致",
  POTION_ALREADY_USED: "药水已使用",
  WITCH_SELF_RESCUE_FORBIDDEN: "女巫不能自救",
  GAME_ENDED: "游戏已结束",
  HOST_RECOVERY_NOT_IN_S1: "该功能尚未开放",
  RATE_LIMITED: "请求过于频繁",
  CONNECTION_LAG: "连接已断开",
  INTERNAL_ERROR: "服务器内部错误",
  NETWORK_ERROR: "网络连接失败",
  UNKNOWN_ERROR: "发生未知错误",
};

export class AppError extends Error {
  readonly code: ErrorCode;
  readonly status: number;
  readonly requestId: string | null;

  constructor(
    code: ErrorCode,
    status: number,
    requestId: string | null = null,
  ) {
    super(SAFE_MESSAGES[code]);
    this.name = "AppError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
  }
}

function isErrorCode(value: unknown): value is ErrorCode {
  return typeof value === "string" && Object.hasOwn(SAFE_MESSAGES, value);
}

export function toAppError(payload: unknown, status: number): AppError {
  if (typeof payload !== "object" || payload === null) {
    return new AppError("UNKNOWN_ERROR", status);
  }

  const record = payload as Record<string, unknown>;
  const code = isErrorCode(record.code) ? record.code : "UNKNOWN_ERROR";
  const requestId =
    typeof record.request_id === "string" ? record.request_id : null;
  return new AppError(code, status, requestId);
}
