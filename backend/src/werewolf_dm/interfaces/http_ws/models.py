from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from werewolf_dm.application.rooms import RoomSnapshot
from werewolf_dm.domain.contracts import CommandEnvelope, CommandErrorCode
from werewolf_dm.domain.model import StrictModel
from werewolf_dm.domain.visibility import PublicView


class HealthResponse(StrictModel):
    status: Literal["ok"] = "ok"
    version: str
    active_rooms: int


class CreateRoomRequest(StrictModel):
    display_name: str = Field(min_length=1, max_length=24)


class CreateRoomResponse(StrictModel):
    room_id: UUID
    room_code: str
    host_token: str
    expires_at: datetime


class JoinRoomRequest(StrictModel):
    display_name: str = Field(min_length=1, max_length=24)


class JoinRoomResponse(StrictModel):
    room_id: UUID
    seat_id: int = Field(ge=1, le=6)
    seat_token: str
    expires_at: datetime


class DisplayPairingResponse(StrictModel):
    pairing_code: str = Field(pattern=r"^[0-9]{6}$")
    expires_at: datetime
    expires_in_seconds: int = Field(ge=0)


class DisplaySessionExchangeRequest(StrictModel):
    pairing_code: str = Field(pattern=r"^[0-9]{6}$")


class DisplaySessionResponse(StrictModel):
    room_id: UUID
    display_token: str
    expires_at: datetime


class AuthRequiredMessage(StrictModel):
    type: Literal["auth.required"] = "auth.required"


class AuthMessage(StrictModel):
    type: Literal["auth"] = "auth"
    token: str = Field(min_length=1)
    last_seq: int = Field(default=0, ge=0)


class SessionReadyMessage(StrictModel):
    type: Literal["session.ready"] = "session.ready"
    server_time: datetime
    snapshot: RoomSnapshot


class SubscribeMessage(StrictModel):
    type: Literal["subscribe"] = "subscribe"
    channel: Literal["public"]


class PublicViewMessage(StrictModel):
    type: Literal["public.view.updated"] = "public.view.updated"
    server_time: datetime
    outbox_seq: int
    public_view: PublicView


class CommandMessage(StrictModel):
    type: Literal["command"] = "command"
    command: CommandEnvelope


class CommandAckMessage(StrictModel):
    type: Literal["command.ack"] = "command.ack"
    command_id: UUID
    accepted: bool
    revision: int
    error_code: CommandErrorCode | None = None
    outbox_seq: int
