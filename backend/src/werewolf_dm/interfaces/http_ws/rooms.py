from typing import cast

from fastapi import APIRouter, Request, status

from werewolf_dm.application.rooms import RoomRegistry
from werewolf_dm.interfaces.http_ws.models import (
    CreateRoomRequest,
    CreateRoomResponse,
    JoinRoomRequest,
    JoinRoomResponse,
)

router = APIRouter(prefix="/rooms", tags=["rooms"])


def registry(request: Request) -> RoomRegistry:
    return cast(RoomRegistry, request.app.state.room_registry)


@router.post("", response_model=CreateRoomResponse, status_code=status.HTTP_201_CREATED)
async def create_room(payload: CreateRoomRequest, request: Request) -> CreateRoomResponse:
    del payload
    room_registry = registry(request)
    created = room_registry.create_room(request.app.state.token_ttl)
    await room_registry.start_room(created.room_code)
    return CreateRoomResponse.model_validate(created.model_dump())


@router.post("/{room_code}/join", response_model=JoinRoomResponse)
async def join_room(
    room_code: str,
    payload: JoinRoomRequest,
    request: Request,
) -> JoinRoomResponse:
    joined = registry(request).join_room(room_code, payload.display_name)
    return JoinRoomResponse(
        room_id=joined.room_id,
        seat_id=joined.seat_id,
        seat_token=joined.seat_token,
        expires_at=joined.expires_at,
    )
