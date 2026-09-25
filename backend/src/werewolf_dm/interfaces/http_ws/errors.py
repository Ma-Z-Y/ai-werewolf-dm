from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from fastapi.exceptions import RequestValidationError
from starlette.websockets import WebSocketDisconnect

from werewolf_dm.domain.contracts import CommandErrorCode
from werewolf_dm.domain.model import StrictModel
from werewolf_dm.domain.visibility import ProjectionAccessError

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    BAD_REQUEST = "BAD_REQUEST"
    ROOM_NOT_FOUND = "ROOM_NOT_FOUND"
    ROOM_FULL = "ROOM_FULL"
    ROOM_LIMIT_REACHED = "ROOM_LIMIT_REACHED"
    TOKEN_INVALID = "TOKEN_INVALID"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    CHANNEL_FORBIDDEN = "CHANNEL_FORBIDDEN"
    ACTOR_NOT_AUTHORIZED = CommandErrorCode.ACTOR_NOT_AUTHORIZED.value
    ILLEGAL_PHASE = CommandErrorCode.ILLEGAL_PHASE.value
    INVALID_TARGET = CommandErrorCode.INVALID_TARGET.value
    REVISION_CONFLICT = CommandErrorCode.REVISION_CONFLICT.value
    PLAYER_DEAD = CommandErrorCode.PLAYER_DEAD.value
    NOT_CURRENT_SPEAKER = CommandErrorCode.NOT_CURRENT_SPEAKER.value
    VOTE_ROUND_CLOSED = CommandErrorCode.VOTE_ROUND_CLOSED.value
    WOLF_CONSENSUS_PENDING = CommandErrorCode.WOLF_CONSENSUS_PENDING.value
    POTION_ALREADY_USED = CommandErrorCode.POTION_ALREADY_USED.value
    WITCH_SELF_RESCUE_FORBIDDEN = CommandErrorCode.WITCH_SELF_RESCUE_FORBIDDEN.value
    GAME_ENDED = CommandErrorCode.GAME_ENDED.value
    HOST_RECOVERY_NOT_IN_S1 = CommandErrorCode.HOST_RECOVERY_NOT_IN_S1.value
    RATE_LIMITED = "RATE_LIMITED"
    CONNECTION_LAG = "CONNECTION_LAG"
    INTERNAL_ERROR = "INTERNAL_ERROR"


SAFE_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.BAD_REQUEST: "请求不合法",
    ErrorCode.ROOM_NOT_FOUND: "房间不存在",
    ErrorCode.ROOM_FULL: "房间已满",
    ErrorCode.ROOM_LIMIT_REACHED: "服务器房间容量已达上限",
    ErrorCode.TOKEN_INVALID: "令牌无效",
    ErrorCode.TOKEN_EXPIRED: "令牌已过期",
    ErrorCode.CHANNEL_FORBIDDEN: "频道不可用",
    ErrorCode.ACTOR_NOT_AUTHORIZED: "操作未授权",
    ErrorCode.ILLEGAL_PHASE: "当前阶段不允许该操作",
    ErrorCode.INVALID_TARGET: "目标不合法",
    ErrorCode.REVISION_CONFLICT: "状态版本冲突",
    ErrorCode.PLAYER_DEAD: "玩家已出局",
    ErrorCode.NOT_CURRENT_SPEAKER: "尚未轮到你发言",
    ErrorCode.VOTE_ROUND_CLOSED: "投票已结束",
    ErrorCode.WOLF_CONSENSUS_PENDING: "狼人尚未达成一致",
    ErrorCode.POTION_ALREADY_USED: "药水已使用",
    ErrorCode.WITCH_SELF_RESCUE_FORBIDDEN: "女巫不能自救",
    ErrorCode.GAME_ENDED: "游戏已结束",
    ErrorCode.HOST_RECOVERY_NOT_IN_S1: "该功能尚未开放",
    ErrorCode.RATE_LIMITED: "请求过于频繁",
    ErrorCode.CONNECTION_LAG: "连接已断开",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误",
}


class ErrorResponse(StrictModel):
    code: ErrorCode
    message: str
    request_id: UUID


class ErrorMessage(ErrorResponse):
    type: Literal["error"] = "error"


def _exception_code(exc: Exception) -> str | None:
    if len(exc.args) != 1 or not isinstance(exc.args[0], str):
        return None
    return exc.args[0]


def _map_exception(exc: Exception) -> ErrorCode:
    if isinstance(exc, RequestValidationError):
        return ErrorCode.BAD_REQUEST
    if isinstance(exc, WebSocketDisconnect):
        return ErrorCode.CONNECTION_LAG
    if isinstance(exc, ProjectionAccessError):
        code = _exception_code(exc)
        if code in {"SEAT_VIEW_FORBIDDEN", "PLAYER_REPLAY_FORBIDDEN", "HOST_AUDIT_FORBIDDEN"}:
            return ErrorCode.ACTOR_NOT_AUTHORIZED
        return ErrorCode.INTERNAL_ERROR
    if isinstance(exc, ValueError):
        code = _exception_code(exc)
        if code is not None:
            try:
                return ErrorCode(code)
            except ValueError:
                pass
    return ErrorCode.INTERNAL_ERROR


def sanitize_error(
    exc: Exception,
    *,
    request_id: UUID | None = None,
) -> ErrorResponse:
    code = _map_exception(exc)
    resolved_request_id = request_id or uuid4()
    if code is ErrorCode.INTERNAL_ERROR:
        logger.error(
            "Unhandled server error sanitized for client request_id=%s exception_type=%s",
            resolved_request_id,
            type(exc).__name__,
        )
    return ErrorResponse(
        code=code,
        message=SAFE_MESSAGES[code],
        request_id=resolved_request_id,
    )


class TokenBucket:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        capacity: int,
        refill_per_second: float,
    ) -> None:
        self.clock = clock
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens = float(capacity)
        self.updated_at = clock()

    def consume(self) -> bool:
        now = self.clock()
        elapsed = max(0.0, (now - self.updated_at).total_seconds())
        if elapsed:
            self.tokens = min(
                float(self.capacity),
                self.tokens + elapsed * self.refill_per_second,
            )
            self.updated_at = now
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True


class ConnectionRateLimiter:
    def __init__(
        self,
        clock: Callable[[], datetime],
        *,
        command_capacity: int = 20,
        command_refill_per_second: float = 5.0,
        control_capacity: int = 3,
        control_refill_per_second: float = 0.5,
        strike_limit: int = 3,
    ) -> None:
        self.command_bucket = TokenBucket(
            clock=clock,
            capacity=command_capacity,
            refill_per_second=command_refill_per_second,
        )
        self.control_bucket = TokenBucket(
            clock=clock,
            capacity=control_capacity,
            refill_per_second=control_refill_per_second,
        )
        self.strike_limit = strike_limit
        self.overflow_strikes = 0

    def allow_command(self) -> bool:
        allowed = self.command_bucket.consume()
        if allowed:
            self.overflow_strikes = 0
        return allowed

    def allow_control(self) -> bool:
        allowed = self.control_bucket.consume()
        if allowed:
            self.overflow_strikes = 0
        return allowed

    def record_overflow(self) -> bool:
        self.overflow_strikes += 1
        return self.overflow_strikes >= self.strike_limit
