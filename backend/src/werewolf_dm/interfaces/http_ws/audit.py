from typing import Literal, cast

from fastapi import APIRouter, Request

from werewolf_dm.application.rooms import RoomActor, RoomRegistry
from werewolf_dm.domain.contracts import AuthenticatedActor
from werewolf_dm.domain.visibility import (
    HostAuditExport,
    PlayerReplay,
    project_host_audit,
    project_player_replay,
)

router = APIRouter(prefix="/rooms", tags=["exports"])


def _registry(request: Request) -> RoomRegistry:
    return cast(RoomRegistry, request.app.state.room_registry)


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, separator, raw_token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not raw_token.strip():
        raise ValueError("TOKEN_INVALID")
    return raw_token.strip()


def _authorized_export(
    request: Request,
    room_code: str,
    expected_actor_type: Literal["seat", "host"],
) -> tuple[RoomActor, AuthenticatedActor]:
    registry = _registry(request)
    record = registry.tokens.resolve(_bearer_token(request))
    actor = registry.get_by_code(room_code)
    if record.room_id != actor.room_id or record.actor_type != expected_actor_type:
        raise ValueError("ACTOR_NOT_AUTHORIZED")
    return actor, registry.actor_for(record)


@router.get("/{room_code}/replay", response_model=PlayerReplay)
async def replay(room_code: str, request: Request) -> PlayerReplay:
    actor, authenticated = _authorized_export(request, room_code, "seat")
    return project_player_replay(actor.core.state, actor.core.events, authenticated)


@router.get("/{room_code}/audit", response_model=HostAuditExport)
async def audit(room_code: str, request: Request) -> HostAuditExport:
    actor, authenticated = _authorized_export(request, room_code, "host")
    return project_host_audit(actor.core.state, actor.core.events, authenticated)
