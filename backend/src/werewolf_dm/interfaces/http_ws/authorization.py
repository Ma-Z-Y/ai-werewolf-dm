from typing import Literal, cast

from fastapi import Request

from werewolf_dm.application.rooms import RoomActor, RoomRegistry, TokenRecord
from werewolf_dm.domain.contracts import AuthenticatedActor


def registry_from_request(request: Request) -> RoomRegistry:
    return cast(RoomRegistry, request.app.state.room_registry)


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, separator, raw_token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not raw_token.strip():
        raise ValueError("TOKEN_INVALID")
    return raw_token.strip()


def authorize_bearer(
    request: Request,
    room_code: str,
    expected_actor_type: Literal["seat", "host"],
) -> tuple[RoomActor, AuthenticatedActor, TokenRecord]:
    registry = registry_from_request(request)
    record = registry.tokens.resolve(_bearer_token(request))
    room = registry.get_by_code(room_code)
    if record.room_id != room.room_id:
        raise ValueError("ACTOR_NOT_AUTHORIZED")
    if record.actor_type != expected_actor_type:
        raise ValueError("ACTOR_NOT_AUTHORIZED")
    return room, registry.actor_for(record), record
