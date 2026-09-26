from fastapi import APIRouter, Request, Response, status

from werewolf_dm.interfaces.http_ws.authorization import (
    authorize_bearer,
    registry_from_request,
)
from werewolf_dm.interfaces.http_ws.models import (
    DisplayPairingResponse,
    DisplaySessionExchangeRequest,
    DisplaySessionResponse,
)

router = APIRouter(prefix="/rooms", tags=["display"])


@router.post(
    "/{room_code}/display-pairings",
    response_model=DisplayPairingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_display_pairing(
    room_code: str,
    request: Request,
) -> DisplayPairingResponse:
    authorize_bearer(request, room_code, "host")
    pairing_code, expires_at, expires_in_seconds = registry_from_request(
        request
    ).create_display_pairing(room_code)
    return DisplayPairingResponse(
        pairing_code=pairing_code,
        expires_at=expires_at,
        expires_in_seconds=expires_in_seconds,
    )


@router.post(
    "/{room_code}/display-sessions",
    response_model=DisplaySessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_display_session(
    room_code: str,
    payload: DisplaySessionExchangeRequest,
    request: Request,
) -> DisplaySessionResponse:
    source = request.client.host if request.client is not None else "unknown"
    room_id, display_token, expires_at = registry_from_request(request).exchange_display_pairing(
        room_code,
        payload.pairing_code,
        source,
    )
    return DisplaySessionResponse(
        room_id=room_id,
        display_token=display_token,
        expires_at=expires_at,
    )


@router.delete(
    "/{room_code}/display-sessions/current",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_display_session(room_code: str, request: Request) -> Response:
    authorize_bearer(request, room_code, "host")
    registry_from_request(request).revoke_display(room_code)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
