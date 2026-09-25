from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, JsonValue, field_serializer, model_validator

from werewolf_dm.domain.enums import CommandType, EventType, Faction, Phase, Role
from werewolf_dm.domain.model import (
    SeatTally,
    SeerCheckRecord,
    StrictModel,
    _thaw_json,
    freeze_json_mapping,
)


class CommandErrorCode(StrEnum):
    REVISION_CONFLICT = "REVISION_CONFLICT"
    ACTOR_NOT_AUTHORIZED = "ACTOR_NOT_AUTHORIZED"
    ILLEGAL_PHASE = "ILLEGAL_PHASE"
    INVALID_TARGET = "INVALID_TARGET"
    PLAYER_DEAD = "PLAYER_DEAD"
    NOT_CURRENT_SPEAKER = "NOT_CURRENT_SPEAKER"
    VOTE_ROUND_CLOSED = "VOTE_ROUND_CLOSED"
    WOLF_CONSENSUS_PENDING = "WOLF_CONSENSUS_PENDING"
    POTION_ALREADY_USED = "POTION_ALREADY_USED"
    WITCH_SELF_RESCUE_FORBIDDEN = "WITCH_SELF_RESCUE_FORBIDDEN"
    GAME_ENDED = "GAME_ENDED"
    HOST_RECOVERY_NOT_IN_S1 = "HOST_RECOVERY_NOT_IN_S1"


class JoinRoomCommand(StrictModel):
    command_type: Literal[CommandType.JOIN_ROOM] = CommandType.JOIN_ROOM
    seat_id: int = Field(ge=1, le=6)
    display_name: str = Field(min_length=1, max_length=24)


class SetReadyCommand(StrictModel):
    command_type: Literal[CommandType.SET_READY] = CommandType.SET_READY
    ready: bool


class ConfirmRoleCommand(StrictModel):
    command_type: Literal[CommandType.CONFIRM_ROLE] = CommandType.CONFIRM_ROLE


class WolfNominateKillCommand(StrictModel):
    command_type: Literal[CommandType.WOLF_NOMINATE_KILL] = CommandType.WOLF_NOMINATE_KILL
    target_seat_id: int = Field(ge=1, le=6)


class SeerInspectCommand(StrictModel):
    command_type: Literal[CommandType.SEER_INSPECT] = CommandType.SEER_INSPECT
    target_seat_id: int = Field(ge=1, le=6)


class WitchUseAntidoteCommand(StrictModel):
    command_type: Literal[CommandType.WITCH_USE_ANTIDOTE] = CommandType.WITCH_USE_ANTIDOTE


class WitchUsePoisonCommand(StrictModel):
    command_type: Literal[CommandType.WITCH_USE_POISON] = CommandType.WITCH_USE_POISON
    target_seat_id: int = Field(ge=1, le=6)


class WitchSkipCommand(StrictModel):
    command_type: Literal[CommandType.WITCH_SKIP] = CommandType.WITCH_SKIP


class SpeakCommand(StrictModel):
    command_type: Literal[CommandType.SPEAK] = CommandType.SPEAK
    text: str = Field(min_length=1, max_length=1000)


class PassSpeechCommand(StrictModel):
    command_type: Literal[CommandType.PASS_SPEECH] = CommandType.PASS_SPEECH


class VoteCommand(StrictModel):
    command_type: Literal[CommandType.VOTE] = CommandType.VOTE
    target_seat_id: int = Field(ge=1, le=6)


class AbstainCommand(StrictModel):
    command_type: Literal[CommandType.ABSTAIN] = CommandType.ABSTAIN


class ReconnectCommand(StrictModel):
    command_type: Literal[CommandType.RECONNECT] = CommandType.RECONNECT


class SetAlivePatch(StrictModel):
    patch_type: Literal["SET_ALIVE"] = "SET_ALIVE"
    seat_id: int = Field(ge=1, le=6)
    alive: bool


class SetRolePatch(StrictModel):
    patch_type: Literal["SET_ROLE"] = "SET_ROLE"
    seat_id: int = Field(ge=1, le=6)
    role: Role


class SetPotionPatch(StrictModel):
    patch_type: Literal["SET_POTION"] = "SET_POTION"
    antidote_available: bool
    poison_available: bool


class SetVotePatch(StrictModel):
    patch_type: Literal["SET_VOTE"] = "SET_VOTE"
    voter_seat_id: int = Field(ge=1, le=6)
    round_id: UUID
    target_seat_id: int | None = Field(default=None, ge=1, le=6)


class SetSeerChecksPatch(StrictModel):
    patch_type: Literal["SET_SEER_CHECKS"] = "SET_SEER_CHECKS"
    seer_seat_id: int = Field(ge=1, le=6)
    checks: list[SeerCheckRecord]


class SetPhasePatch(StrictModel):
    patch_type: Literal["SET_PHASE"] = "SET_PHASE"
    phase: Phase


HostPatch = Annotated[
    SetAlivePatch
    | SetRolePatch
    | SetPotionPatch
    | SetVotePatch
    | SetSeerChecksPatch
    | SetPhasePatch,
    Field(discriminator="patch_type"),
]


class HostPauseCommand(StrictModel):
    command_type: Literal[CommandType.HOST_PAUSE] = CommandType.HOST_PAUSE
    reason: str = Field(min_length=1, max_length=200)


class HostResumeCommand(StrictModel):
    command_type: Literal[CommandType.HOST_RESUME] = CommandType.HOST_RESUME


class HostPatchCommand(StrictModel):
    command_type: Literal[CommandType.HOST_PATCH] = CommandType.HOST_PATCH
    patch: HostPatch


class HostRewindToSnapshotCommand(StrictModel):
    command_type: Literal[CommandType.HOST_REWIND_TO_SNAPSHOT] = CommandType.HOST_REWIND_TO_SNAPSHOT
    snapshot_id: UUID


class HostForceTemplateCommand(StrictModel):
    command_type: Literal[CommandType.HOST_FORCE_TEMPLATE] = CommandType.HOST_FORCE_TEMPLATE


PlayerCommand = (
    JoinRoomCommand
    | SetReadyCommand
    | ConfirmRoleCommand
    | WolfNominateKillCommand
    | SeerInspectCommand
    | WitchUseAntidoteCommand
    | WitchUsePoisonCommand
    | WitchSkipCommand
    | SpeakCommand
    | PassSpeechCommand
    | VoteCommand
    | AbstainCommand
    | ReconnectCommand
)

HostCommand = (
    HostPauseCommand
    | HostResumeCommand
    | HostPatchCommand
    | HostRewindToSnapshotCommand
    | HostForceTemplateCommand
)

CommandPayload = Annotated[PlayerCommand | HostCommand, Field(discriminator="command_type")]


class AuthenticatedActor(StrictModel):
    actor_type: Literal["seat", "host"]
    seat_id: int | None = Field(default=None, ge=1, le=6)
    room_id: UUID

    @model_validator(mode="after")
    def validate_seat_shape(self) -> Self:
        if self.actor_type == "seat" and self.seat_id is None:
            raise ValueError("seat actor requires seat_id")
        if self.actor_type == "host" and self.seat_id is not None:
            raise ValueError("host actor cannot carry seat_id")
        return self


class CommandEnvelope(StrictModel):
    schema_version: Literal["command.v1"] = "command.v1"
    command_id: UUID
    room_id: UUID
    expected_revision: int = Field(ge=0)
    issued_at: datetime
    payload: CommandPayload


class CommandResult(StrictModel):
    schema_version: Literal["command-result.v1"] = "command-result.v1"
    command_id: UUID
    accepted: bool
    revision: int = Field(ge=0)
    event_ids: tuple[UUID, ...]
    error_code: CommandErrorCode | None = None


class PublicVisibility(StrictModel):
    scope: Literal["public"] = "public"


class SeatVisibility(StrictModel):
    scope: Literal["seat"] = "seat"
    seat_id: int = Field(ge=1, le=6)


class FactionVisibility(StrictModel):
    scope: Literal["faction"] = "faction"
    faction: Faction


class HostVisibility(StrictModel):
    scope: Literal["host"] = "host"


EventVisibility = Annotated[
    PublicVisibility | SeatVisibility | FactionVisibility | HostVisibility,
    Field(discriminator="scope"),
]


class EventPayload(StrictModel):
    pass


class RoomJoinedPayload(EventPayload):
    seat_id: int = Field(ge=1, le=6)
    display_name: str = Field(min_length=1, max_length=24)


class ReadyChangedPayload(EventPayload):
    seat_id: int = Field(ge=1, le=6)
    ready: bool


class RoleAssignedPayload(EventPayload):
    seat_id: int = Field(ge=1, le=6)
    role: Role


class RoleConfirmedPayload(EventPayload):
    seat_id: int = Field(ge=1, le=6)


class PhaseChangedPayload(EventPayload):
    previous_phase: Phase | None
    next_phase: Phase
    day: int = Field(ge=0)


class WolfNominationPayload(EventPayload):
    seat_id: int = Field(ge=1, le=6)
    target_seat_id: int = Field(ge=1, le=6)


class WolfTargetLockedPayload(EventPayload):
    target_seat_id: int | None = Field(default=None, ge=1, le=6)


class SeerCheckedPayload(EventPayload):
    seer_seat_id: int = Field(ge=1, le=6)
    target_seat_id: int = Field(ge=1, le=6)
    target_faction: Faction


class WitchActionPayload(EventPayload):
    witch_seat_id: int = Field(ge=1, le=6)
    action: Literal["ANTIDOTE", "POISON", "SKIP"]
    target_seat_id: int | None = Field(default=None, ge=1, le=6)


class PlayersDiedPayload(EventPayload):
    seat_ids: tuple[int, ...]
    cause: Literal["WOLF", "POISON", "MIXED"] | None = None


class SpeechRecordedPayload(EventPayload):
    seat_id: int = Field(ge=1, le=6)
    text: str = Field(min_length=1, max_length=1000)


class SpeechPassedPayload(EventPayload):
    seat_id: int = Field(ge=1, le=6)
    timed_out: bool


class VoteRecordedPayload(EventPayload):
    voter_seat_id: int = Field(ge=1, le=6)
    target_seat_id: int = Field(ge=1, le=6)


class AbstainRecordedPayload(EventPayload):
    voter_seat_id: int = Field(ge=1, le=6)


class VoteRoundResolvedPayload(EventPayload):
    round_id: UUID
    tallies: tuple[SeatTally, ...]
    exiled_seat_id: int | None = Field(default=None, ge=1, le=6)
    tie: bool

    @model_validator(mode="after")
    def reject_duplicate_tallies(self) -> Self:
        seat_ids = [tally.seat_id for tally in self.tallies]
        if len(set(seat_ids)) != len(seat_ids):
            raise ValueError("vote round tallies must have unique seats")
        return self


class PlayerExiledPayload(EventPayload):
    seat_id: int = Field(ge=1, le=6)


class NoExilePayload(EventPayload):
    reason: Literal["NO_VOTES", "PK_TIE", "NO_VALID_PK_VOTES"]


class TimeoutAppliedPayload(EventPayload):
    phase: Phase
    timeout_reason: Literal[
        "WOLF",
        "SEER",
        "WITCH",
        "SPEECH",
        "VOTE",
        "PK_VOTE",
        "ROLE_CONFIRM",
    ]


class HostPausedPayload(EventPayload):
    reason: str = Field(min_length=1, max_length=200)


class HostResumedPayload(EventPayload):
    pass


class GameEndedPayload(EventPayload):
    winner: Faction


EVENT_PAYLOAD_MODELS: dict[EventType, type[EventPayload]] = {
    EventType.ROOM_JOINED: RoomJoinedPayload,
    EventType.READY_CHANGED: ReadyChangedPayload,
    EventType.ROLE_ASSIGNED: RoleAssignedPayload,
    EventType.ROLE_CONFIRMED: RoleConfirmedPayload,
    EventType.PHASE_CHANGED: PhaseChangedPayload,
    EventType.WOLF_NOMINATION_RECORDED: WolfNominationPayload,
    EventType.WOLF_TARGET_LOCKED: WolfTargetLockedPayload,
    EventType.SEER_CHECKED: SeerCheckedPayload,
    EventType.WITCH_ACTION_RECORDED: WitchActionPayload,
    EventType.PLAYERS_DIED: PlayersDiedPayload,
    EventType.SPEECH_RECORDED: SpeechRecordedPayload,
    EventType.SPEECH_PASSED: SpeechPassedPayload,
    EventType.VOTE_RECORDED: VoteRecordedPayload,
    EventType.ABSTAIN_RECORDED: AbstainRecordedPayload,
    EventType.VOTE_ROUND_RESOLVED: VoteRoundResolvedPayload,
    EventType.PLAYER_EXILED: PlayerExiledPayload,
    EventType.NO_EXILE: NoExilePayload,
    EventType.TIMEOUT_APPLIED: TimeoutAppliedPayload,
    EventType.HOST_PAUSED: HostPausedPayload,
    EventType.HOST_RESUMED: HostResumedPayload,
    EventType.GAME_ENDED: GameEndedPayload,
}

EVENT_VISIBILITY_SCOPES: dict[EventType, frozenset[str]] = {
    EventType.ROOM_JOINED: frozenset({"public"}),
    EventType.READY_CHANGED: frozenset({"public"}),
    EventType.ROLE_ASSIGNED: frozenset({"seat"}),
    EventType.ROLE_CONFIRMED: frozenset({"public"}),
    EventType.PHASE_CHANGED: frozenset({"public"}),
    EventType.WOLF_NOMINATION_RECORDED: frozenset({"faction"}),
    EventType.WOLF_TARGET_LOCKED: frozenset({"faction"}),
    EventType.SEER_CHECKED: frozenset({"seat"}),
    EventType.WITCH_ACTION_RECORDED: frozenset({"seat"}),
    EventType.PLAYERS_DIED: frozenset({"public", "host"}),
    EventType.SPEECH_RECORDED: frozenset({"public"}),
    EventType.SPEECH_PASSED: frozenset({"public"}),
    EventType.VOTE_RECORDED: frozenset({"host"}),
    EventType.ABSTAIN_RECORDED: frozenset({"host"}),
    EventType.VOTE_ROUND_RESOLVED: frozenset({"public"}),
    EventType.PLAYER_EXILED: frozenset({"public"}),
    EventType.NO_EXILE: frozenset({"public"}),
    EventType.TIMEOUT_APPLIED: frozenset({"public"}),
    EventType.HOST_PAUSED: frozenset({"public"}),
    EventType.HOST_RESUMED: frozenset({"public"}),
    EventType.GAME_ENDED: frozenset({"public"}),
}

EVENT_SEAT_VISIBILITY_FIELDS: dict[EventType, str] = {
    EventType.ROLE_ASSIGNED: "seat_id",
    EventType.SEER_CHECKED: "seer_seat_id",
    EventType.WITCH_ACTION_RECORDED: "witch_seat_id",
}


class DomainEvent(StrictModel):
    schema_version: Literal["event.v1"] = "event.v1"
    event_id: UUID
    room_id: UUID
    revision: int = Field(ge=0)
    event_type: EventType
    visibility: EventVisibility
    fact_payload: Mapping[str, JsonValue]
    causation_id: UUID | None
    correlation_id: UUID
    created_at: datetime

    @model_validator(mode="after")
    def validate_fact_payload(self) -> Self:
        payload_model = EVENT_PAYLOAD_MODELS[self.event_type]
        validated = payload_model.validate_json_payload(self.fact_payload)
        allowed_scopes = EVENT_VISIBILITY_SCOPES[self.event_type]
        if self.visibility.scope not in allowed_scopes:
            raise ValueError(
                f"{self.event_type.value} cannot use {self.visibility.scope} visibility"
            )
        if isinstance(validated, PlayersDiedPayload):
            if self.visibility.scope == "public" and validated.cause is not None:
                raise ValueError("public death events cannot contain cause")
            if self.visibility.scope == "host" and validated.cause is None:
                raise ValueError("host death-audit events require cause")
        if isinstance(self.visibility, SeatVisibility):
            seat_payload_field = EVENT_SEAT_VISIBILITY_FIELDS.get(self.event_type)
            if (
                seat_payload_field is not None
                and validated.model_dump(mode="json").get(seat_payload_field)
                != self.visibility.seat_id
            ):
                raise ValueError("seat visibility must match payload seat")
        object.__setattr__(
            self,
            "fact_payload",
            freeze_json_mapping(
                validated.model_dump(
                    mode="json",
                    exclude=(
                        {"cause"}
                        if isinstance(validated, PlayersDiedPayload)
                        and self.visibility.scope == "public"
                        else None
                    ),
                )
            ),
        )
        return self

    @field_serializer("fact_payload")
    def serialize_fact_payload(
        self,
        fact_payload: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], _thaw_json(fact_payload))


class SystemTimeout(StrictModel):
    schema_version: Literal["system-timeout.v1"] = "system-timeout.v1"
    timeout_id: UUID
    room_id: UUID
    expected_revision: int = Field(ge=0)
    phase: Phase
    occurred_at: datetime


def build_event(
    *,
    cause_id: UUID,
    event_ordinal: int,
    room_id: UUID,
    revision: int,
    event_type: EventType,
    visibility: EventVisibility,
    payload: EventPayload,
    created_at: datetime,
) -> DomainEvent:
    payload_model = EVENT_PAYLOAD_MODELS[event_type]
    validated = payload_model.model_validate(payload)
    return DomainEvent(
        event_id=uuid5(
            NAMESPACE_URL,
            f"{cause_id}:{event_ordinal}:{event_type.value}",
        ),
        room_id=room_id,
        revision=revision,
        event_type=event_type,
        visibility=visibility,
        fact_payload=validated.model_dump(mode="json"),
        causation_id=cause_id,
        correlation_id=cause_id,
        created_at=created_at,
    )
