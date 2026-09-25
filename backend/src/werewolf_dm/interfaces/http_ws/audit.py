from fastapi import APIRouter, Request

from werewolf_dm.domain.visibility import (
    HostAuditExport,
    PlayerReplay,
    project_host_audit,
    project_player_replay,
)
from werewolf_dm.interfaces.http_ws.authorization import authorize_bearer

router = APIRouter(prefix="/rooms", tags=["exports"])


@router.get("/{room_code}/replay", response_model=PlayerReplay)
async def replay(room_code: str, request: Request) -> PlayerReplay:
    actor, authenticated, _ = authorize_bearer(request, room_code, "seat")
    return project_player_replay(actor.core.state, actor.core.events, authenticated)


@router.get("/{room_code}/audit", response_model=HostAuditExport)
async def audit(room_code: str, request: Request) -> HostAuditExport:
    actor, authenticated, _ = authorize_bearer(request, room_code, "host")
    return project_host_audit(actor.core.state, actor.core.events, authenticated)
