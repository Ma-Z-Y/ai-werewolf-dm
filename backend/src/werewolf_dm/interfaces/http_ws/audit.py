from fastapi import APIRouter, Query, Request

from werewolf_dm.application.dm_metrics import clip_dm_trace
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
async def audit(
    room_code: str,
    request: Request,
    include: str = Query(
        default="",
        description="Comma-separated audit sections; supports dm_trace.",
    ),
) -> HostAuditExport:
    actor, authenticated, _ = authorize_bearer(request, room_code, "host")
    requested = frozenset(value.strip() for value in include.split(",") if value.strip())
    return project_host_audit(
        actor.core.state,
        actor.core.events,
        authenticated,
        dm_trace=(
            clip_dm_trace(actor.dm_trace, actor.dm_transport_trace)
            if "dm_trace" in requested
            else ()
        ),
        recovery_audit=actor.host_recovery_audit() if "recovery_audit" in requested else (),
        snapshots=actor.host_snapshots() if "snapshots" in requested else (),
    )
