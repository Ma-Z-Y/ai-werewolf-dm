from __future__ import annotations

import asyncio
import contextlib
from typing import Literal, cast
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import Field, ValidationError

from werewolf_dm.application.rooms import (
    RoomActor,
    RoomClosedError,
    RoomRegistry,
    RoomSubscriber,
    TokenRecord,
    can_subscribe,
)
from werewolf_dm.domain.contracts import AuthenticatedActor
from werewolf_dm.domain.model import StrictModel
from werewolf_dm.interfaces.http_ws.auth import resolve_socket_token
from werewolf_dm.interfaces.http_ws.errors import (
    ConnectionRateLimiter,
    ErrorCode,
    ErrorMessage,
    sanitize_error,
)
from werewolf_dm.interfaces.http_ws.models import (
    AuthMessage,
    AuthRequiredMessage,
    CommandAckMessage,
    CommandMessage,
    SubscribeMessage,
)
from werewolf_dm.interfaces.http_ws.runtime import ConnectionSink

AUTH_TIMEOUT_SECONDS = 5.0
IDLE_TIMEOUT_SECONDS = 60.0

router = APIRouter()


class SeatSubscribeMessage(StrictModel):
    type: Literal["subscribe"] = "subscribe"
    channel: Literal["seat"] = "seat"
    seat_id: int = Field(ge=1, le=6)


class PingMessage(StrictModel):
    type: Literal["ping"] = "ping"


class PongMessage(StrictModel):
    type: Literal["pong"] = "pong"


async def _send_rate_limited(
    sink: ConnectionSink,
    limiter: ConnectionRateLimiter,
) -> bool:
    response = sanitize_error(ValueError("RATE_LIMITED"))
    sink.offer(
        ErrorMessage(
            code=response.code,
            message=response.message,
            request_id=response.request_id,
        )
    )
    if not limiter.record_overflow():
        return False
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(sink.send_queue.join(), timeout=0.25)
    await sink.close(1008)
    return True


async def message_loop(
    websocket: WebSocket,
    sink: ConnectionSink,
    *,
    room: RoomActor | None = None,
    record: TokenRecord | None = None,
    actor: AuthenticatedActor | None = None,
    idle_timeout: float = IDLE_TIMEOUT_SECONDS,
    limiter: ConnectionRateLimiter | None = None,
) -> None:
    limiter = limiter or ConnectionRateLimiter(sink.clock)
    while True:
        now = sink.clock()
        if room is not None and (room.closed or room.expires_at <= now):
            await sink.close(code=4001)
            return
        if record is not None and record.expires_at <= now:
            await sink.close(code=4001)
            return
        token_remaining = (
            (record.expires_at - now).total_seconds() if record is not None else idle_timeout
        )
        remaining = max(
            0.0,
            min(
                idle_timeout - (sink.clock() - sink.last_client_activity_at).total_seconds(),
                token_remaining,
            ),
        )
        try:
            raw = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=remaining,
            )
        except TimeoutError:
            if record is not None and record.expires_at <= sink.clock():
                await sink.close(code=4001)
                return
            if sink.idle_expired(idle_timeout):
                await sink.close(code=1001)
                return
            continue
        except (KeyError, ValueError, TypeError):
            await sink.close(code=1003)
            return
        except WebSocketDisconnect:
            await sink.close(code=1000)
            return
        if room is not None and (room.closed or room.expires_at <= sink.clock()):
            await sink.close(code=4001)
            return
        if record is not None and record.expires_at <= sink.clock():
            await sink.close(code=4001)
            return
        if isinstance(raw, dict) and raw.get("type") == "ping":
            if not limiter.allow_control():
                if await _send_rate_limited(sink, limiter):
                    return
                continue
            try:
                PingMessage.model_validate(raw)
            except ValidationError:
                continue
            sink.record_client_activity()
            sink.offer(PongMessage())
            continue
        if (
            isinstance(raw, dict)
            and raw.get("type") == "subscribe"
            and raw.get("channel") == "seat"
        ):
            try:
                seat_subscribe = SeatSubscribeMessage.model_validate(raw)
            except ValidationError:
                continue
            sink.record_client_activity()
            if record is None or not can_subscribe(
                record,
                seat_subscribe.channel,
                seat_subscribe.seat_id,
            ):
                sink.offer(
                    ErrorMessage(
                        code=ErrorCode.CHANNEL_FORBIDDEN,
                        message="频道不可用",
                        request_id=uuid4(),
                    )
                )
                continue
            sink.set_channels(sink.channels | frozenset({"seat"}))
            if room is not None:
                await room.publish_current(cast(RoomSubscriber, sink))
            continue
        if isinstance(raw, dict) and raw.get("type") == "command" and not limiter.allow_command():
            if await _send_rate_limited(sink, limiter):
                return
            continue
        try:
            command_message = (
                CommandMessage.validate_json_payload(raw) if isinstance(raw, dict) else None
            )
        except (TypeError, ValueError):
            command_message = None
        if command_message is not None:
            sink.record_client_activity()
            if room is not None and actor is not None:
                try:
                    ack = await room.submit_command(command_message.command, actor)
                except Exception as exc:
                    error = sanitize_error(exc)
                    sink.offer(
                        ErrorMessage(
                            code=error.code,
                            message=error.message,
                            request_id=error.request_id,
                        )
                    )
                else:
                    sink.offer(CommandAckMessage.model_validate(ack.model_dump()))
            continue
        try:
            subscribe = SubscribeMessage.model_validate(raw)
        except ValidationError:
            continue
        sink.record_client_activity()
        if subscribe.channel == "public":
            sink.set_channels(sink.channels | frozenset({"public"}))
            if room is not None:
                await room.publish_current(cast(RoomSubscriber, sink))


@router.websocket("/ws")
async def websocket_session(websocket: WebSocket) -> None:
    registry = cast(RoomRegistry, websocket.app.state.room_registry)
    sink = ConnectionSink(websocket, clock=registry.clock)
    limiter = ConnectionRateLimiter(registry.clock)
    await sink.start()
    if not registry.connection_opened():
        await sink.close(code=1013)
        return
    room = None
    try:
        sink.offer(AuthRequiredMessage())
        try:
            raw = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=AUTH_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            registry.auth_failures += 1
            await sink.close(code=4002)
            return
        except (KeyError, ValueError, TypeError):
            registry.auth_failures += 1
            await sink.close(code=4001)
            return
        except WebSocketDisconnect:
            await sink.close(code=1000)
            return
        if not limiter.allow_control():
            await sink.close(code=1008)
            return

        try:
            auth = AuthMessage.model_validate(raw)
            record = resolve_socket_token(registry.tokens, auth.token)
            actor = registry.actor_for(record)
            room = registry.rooms_by_id().get(record.room_id)
            if room is None:
                raise ValueError("ROOM_NOT_FOUND")
        except (ValidationError, ValueError):
            registry.auth_failures += 1
            await sink.close(code=4001)
            return

        sink.record_client_activity()
        sink.bind_actor(actor_type=actor.actor_type, seat_id=actor.seat_id)
        try:
            await room.attach_subscriber(cast(RoomSubscriber, sink))
        except RoomClosedError:
            await sink.close(code=4001)
            return
        await message_loop(
            websocket,
            sink,
            room=room,
            record=record,
            actor=actor,
            idle_timeout=IDLE_TIMEOUT_SECONDS,
            limiter=limiter,
        )
    finally:
        try:
            if sink.close_code is None:
                await sink.close(code=1011)
        finally:
            try:
                if room is not None:
                    await room.detach_subscriber(sink.subscription_id)
            finally:
                registry.connection_closed(slow=sink.close_code == 1013)
