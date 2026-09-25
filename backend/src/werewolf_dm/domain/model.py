import copy
import json
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType, UnionType
from typing import Annotated, Any, Literal, Self, Union, cast, get_args, get_origin
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    field_validator,
    model_validator,
)

from werewolf_dm.domain.enums import EventType, Faction, Phase, Role


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @classmethod
    def revalidate(cls, value: Self) -> Self:
        data = BaseModel.model_dump(value, mode="python", warnings=False)
        return cls.model_validate(data, strict=True)

    @classmethod
    def validate_json_payload(cls, payload: Mapping[str, JsonValue]) -> Self:
        raw_payload = _thaw_json(payload)
        if not isinstance(raw_payload, dict):
            raise ValueError("JSON payload must be a mapping")
        _validate_json_scalar_coercions(cls, raw_payload)
        return cls.model_validate_json(
            json.dumps(raw_payload, separators=(",", ":"), allow_nan=False),
            strict=False,
        )

    @model_validator(mode="after")
    def validate_strict_field_types(self) -> Self:
        for field_name, field in type(self).model_fields.items():
            _validate_exact_field_type(
                field.annotation,
                getattr(self, field_name),
                field_name,
            )
        return self

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = BaseModel.model_dump(self, mode="python", warnings=False)
        if update is not None:
            data.update(update)
        if deep:
            data = copy.deepcopy(data)
        candidate = type(self).model_validate(data, strict=True)
        return type(self).revalidate(candidate)


def _validate_exact_field_type(
    annotation: object,
    value: object,
    field_name: str,
) -> None:
    origin = get_origin(annotation)
    if origin is Annotated:
        _validate_exact_field_type(get_args(annotation)[0], value, field_name)
        return
    if origin in (Union, UnionType):
        arguments = get_args(annotation)
        if (
            _annotation_contains_int(annotation)
            and isinstance(value, int)
            and type(value) is not int
            and not any(_annotation_accepts_exact_value(argument, value) for argument in arguments)
        ):
            raise ValueError(f"{field_name} must be an exact integer")
        if (
            _annotation_contains_uuid(annotation)
            and isinstance(value, UUID)
            and type(value) is not UUID
            and not any(_annotation_accepts_exact_value(argument, value) for argument in arguments)
        ):
            raise ValueError(f"{field_name} must be a standard UUID instance")
        for argument in arguments:
            if _validate_container_elements(argument, value, field_name):
                break
        return
    if annotation is int:
        if value is not None and type(value) is not int:
            raise ValueError(f"{field_name} must be an exact integer")
        return
    if annotation is UUID:
        if value is not None and type(value) is not UUID:
            raise ValueError(f"{field_name} must be a standard UUID instance")
        return
    _validate_container_elements(annotation, value, field_name)


def _validate_container_elements(
    annotation: object,
    value: object,
    field_name: str,
) -> bool:
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in (list, tuple, set, frozenset):
        if not isinstance(value, origin):
            return False
        if not arguments:
            return True
        if origin is tuple and len(arguments) == 2 and arguments[1] is Ellipsis:
            for item in cast(tuple[object, ...], value):
                _validate_exact_field_type(arguments[0], item, field_name)
            return True
        if origin is tuple and len(arguments) > 1:
            for item_annotation, item in zip(
                arguments,
                cast(tuple[object, ...], value),
                strict=True,
            ):
                _validate_exact_field_type(item_annotation, item, field_name)
            return True
        for item in cast(tuple[object, ...], value):
            _validate_exact_field_type(arguments[0], item, field_name)
        return True
    if origin in (dict, Mapping):
        if not isinstance(value, Mapping):
            return False
        if not arguments:
            return True
        key_annotation, item_annotation = arguments[:2]
        for key, item in value.items():
            _validate_exact_field_type(key_annotation, key, field_name)
            _validate_exact_field_type(item_annotation, item, field_name)
        return True
    return False


def _annotation_accepts_exact_value(annotation: object, value: object) -> bool:
    origin = get_origin(annotation)
    if origin is Annotated:
        return _annotation_accepts_exact_value(get_args(annotation)[0], value)
    if origin in (Union, UnionType):
        return any(
            _annotation_accepts_exact_value(argument, value) for argument in get_args(annotation)
        )
    if annotation is type(None):
        return value is None
    if annotation in (bool, int, UUID):
        return type(value) is annotation
    if isinstance(annotation, type):
        return type(value) is annotation
    if origin is not None and isinstance(origin, type):
        return type(value) is origin
    return False


def _annotation_contains_int(annotation: object) -> bool:
    if annotation is int:
        return True
    if get_origin(annotation) in (Union, UnionType):
        return any(_annotation_contains_int(argument) for argument in get_args(annotation))
    return False


def _annotation_contains_uuid(annotation: object) -> bool:
    if annotation is UUID:
        return True
    if get_origin(annotation) in (Union, UnionType):
        return any(_annotation_contains_uuid(argument) for argument in get_args(annotation))
    return False


def _validate_json_scalar_coercions(
    model_type: type[BaseModel],
    raw_payload: Mapping[str, object],
) -> None:
    for field_name, field in model_type.model_fields.items():
        if field_name in raw_payload:
            _validate_json_value(field.annotation, raw_payload[field_name], field_name)


def _validate_json_value(
    annotation: object,
    value: object,
    field_name: str,
) -> None:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        arguments = get_args(annotation)
        int_arguments = tuple(
            argument for argument in arguments if _annotation_contains_int(argument)
        )
        if (
            int_arguments
            and value is not None
            and not any(
                _annotation_accepts_non_int_value(argument, value) for argument in arguments
            )
        ):
            raise ValueError(f"{field_name} must be an integer, not bool or float")
        return
    if annotation is int:
        if type(value) is not int:
            raise ValueError(f"{field_name} must be an integer, not bool or float")
        return
    if annotation is bool and type(value) is not bool:
        raise ValueError(f"{field_name} must be a boolean")
    if origin in (list, tuple, set, frozenset) and isinstance(value, (list, tuple)):
        arguments = get_args(annotation)
        if arguments:
            for item in value:
                _validate_json_value(arguments[0], item, field_name)
        return
    if (
        isinstance(annotation, type)
        and issubclass(annotation, BaseModel)
        and isinstance(value, Mapping)
    ):
        _validate_json_scalar_coercions(annotation, cast(Mapping[str, object], value))


def _annotation_accepts_non_int_value(annotation: object, value: object) -> bool:
    if annotation is type(None):
        return value is None
    if _annotation_contains_int(annotation):
        return type(value) is int
    return not isinstance(value, (bool, int, float))


type FrozenJson = JsonValue | Mapping[str, FrozenJson] | tuple[FrozenJson, ...]


def _freeze_json(value: JsonValue) -> FrozenJson:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def freeze_json_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return cast(
        Mapping[str, JsonValue],
        MappingProxyType({key: _freeze_json(item) for key, item in value.items()}),
    )


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


ROLE_COUNTS: Mapping[Role, int] = MappingProxyType(
    {
        Role.WEREWOLF: 2,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.VILLAGER: 2,
    }
)


class Player(StrictModel):
    seat_id: int = Field(ge=1, le=6)
    display_name: str = Field(min_length=1, max_length=24)
    alive: bool
    connected: bool
    ready: bool
    role: Role | None
    role_confirmed: bool


class RoleAssignment(StrictModel):
    seat_id: int = Field(ge=1, le=6)
    role: Role


class PotionState(StrictModel):
    antidote_available: bool
    poison_available: bool


class WolfNomination(StrictModel):
    seat_id: int = Field(ge=1, le=6)
    target_seat_id: int = Field(ge=1, le=6)


class WolfDecision(StrictModel):
    nominations: tuple[WolfNomination, ...] = ()
    target_seat_id: int | None = Field(default=None, ge=1, le=6)
    locked: bool = False

    @field_validator("nominations")
    @classmethod
    def reject_duplicate_nominations(
        cls, value: tuple[WolfNomination, ...]
    ) -> tuple[WolfNomination, ...]:
        seat_ids = [nomination.seat_id for nomination in value]
        if len(set(seat_ids)) != len(seat_ids):
            raise ValueError("wolf nomination seats must be unique")
        return value


class SeerCheckRecord(StrictModel):
    day: int = Field(ge=1)
    target_seat_id: int = Field(ge=1, le=6)
    faction: Faction


class Vote(StrictModel):
    voter_seat_id: int = Field(ge=1, le=6)
    target_seat_id: int | None = Field(default=None, ge=1, le=6)


class VoteRound(StrictModel):
    round_id: UUID
    round_index: int = Field(ge=1)
    phase: Phase
    candidate_seat_ids: tuple[int, ...] = ()
    eligible_voter_ids: tuple[int, ...]
    votes: tuple[Vote, ...] = ()
    closed: bool = False
    opened_at: datetime | None = None
    deadline_at: datetime | None = None

    @field_validator("candidate_seat_ids", "eligible_voter_ids")
    @classmethod
    def validate_unique_seats(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(seat_id < 1 or seat_id > 6 for seat_id in value):
            raise ValueError("seat ids must be between 1 and 6")
        if len(set(value)) != len(value):
            raise ValueError("seat ids must be unique")
        return value


class SeatTally(StrictModel):
    seat_id: int = Field(ge=1, le=6)
    votes: int = Field(ge=0)


class VoteSummary(StrictModel):
    round_id: UUID
    tallies: tuple[SeatTally, ...]
    abstention_count: int = Field(ge=0)
    closed: bool


class DiscussionState(StrictModel):
    participant_seat_ids: tuple[int, ...]
    completed_seat_ids: tuple[int, ...] = ()
    skipped_seat_ids: tuple[int, ...] = ()
    current_seat_id: int | None = Field(default=None, ge=1, le=6)
    deadline_at: datetime | None = None


class NightState(StrictModel):
    action: Literal["NONE", "ANTIDOTE", "POISON", "SKIP"] = "NONE"
    poison_target_seat_id: int | None = Field(default=None, ge=1, le=6)
    deaths: tuple[int, ...] = ()

    @field_validator("deaths")
    @classmethod
    def validate_deaths(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(seat_id < 1 or seat_id > 6 for seat_id in value):
            raise ValueError("death seat ids must be between 1 and 6")
        if len(set(value)) != len(value):
            raise ValueError("death seat ids must be unique")
        return value


class PrivateFact(StrictModel):
    fact_id: UUID
    event_id: UUID
    recipient_seat_id: int = Field(ge=1, le=6)
    revision: int = Field(ge=0)
    fact_type: str
    payload: Mapping[str, JsonValue]

    @model_validator(mode="after")
    def freeze_payload(self) -> Self:
        object.__setattr__(self, "payload", freeze_json_mapping(self.payload))
        return self

    @field_serializer("payload")
    def serialize_payload(self, payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], _thaw_json(payload))


class PublicTimelineItem(StrictModel):
    event_id: UUID
    revision: int = Field(ge=0)
    event_type: EventType
    statement: str = Field(min_length=1, max_length=240)


class OutboxItem(StrictModel):
    seq: int = Field(ge=1)
    kind: Literal["dm.message", "view.updated", "game.ended"]
    event_id: UUID
    revision: int = Field(ge=0)


class GameState(StrictModel):
    schema_version: Literal["game-state.v1"] = "game-state.v1"
    room_id: UUID
    seed: int = Field(ge=0)
    rulepack_version: Literal["1.1.0"] = "1.1.0"
    revision: int = Field(ge=0)
    event_count: int = Field(default=0, ge=0)
    event_log_digest: str = Field(
        default="0" * 64,
        pattern=r"^[0-9a-f]{64}$",
    )
    phase: Phase
    day: int = Field(ge=0)
    players: tuple[Player, ...] = ()
    potions: PotionState = PotionState(antidote_available=True, poison_available=True)
    wolf_decision: WolfDecision = WolfDecision()
    seer_checks: tuple[SeerCheckRecord, ...] = ()
    night: NightState = NightState()
    discussion: DiscussionState | None = None
    vote_round: VoteRound | None = None
    winner: Faction | None = None
    paused: bool = False
    paused_at: datetime | None = None
    deadline_at: datetime | None = None
    next_discussion_cursor: int | None = Field(default=None, ge=1, le=6)
    public_timeline: tuple[PublicTimelineItem, ...] = ()
    private_facts: tuple[PrivateFact, ...] = ()
    outbox: tuple[OutboxItem, ...] = ()
