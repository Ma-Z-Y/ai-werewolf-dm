from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, Self, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, model_validator

from werewolf_dm.application.core import FrozenClock, GameCore
from werewolf_dm.application.replay import ReplayStep, replay
from werewolf_dm.domain.contracts import (
    AbstainCommand,
    AuthenticatedActor,
    CommandEnvelope,
    CommandPayload,
    ConfirmRoleCommand,
    DomainEvent,
    JoinRoomCommand,
    PassSpeechCommand,
    SeerInspectCommand,
    SetReadyCommand,
    SpeakCommand,
    SystemTimeout,
    VoteCommand,
    WitchSkipCommand,
    WitchUseAntidoteCommand,
    WitchUsePoisonCommand,
    WolfNominateKillCommand,
)
from werewolf_dm.domain.enums import CommandType, EventType, Faction, Phase, Role
from werewolf_dm.domain.model import GameState, StrictModel
from werewolf_dm.domain.replay import state_hash
from werewolf_dm.domain.state_machine import ALLOWED_TRANSITIONS, validate_invariants
from werewolf_dm.domain.visibility import (
    LegalAction,
    SeatView,
    project_public_view,
    project_seat_view,
)

PolicyKind = Literal["random", "conservative", "aggressive"]
OutboxKind = Literal["dm.message", "view.updated", "game.ended"]
GoldenAssertion = Literal[
    "PEACEFUL_FIRST_NIGHT",
    "WOLF_CONSENSUS_EMPTY_KNIFE",
    "ANTIDOTE_SAVE",
    "PK_TWO_TIE_NO_EXILE",
    "PK_THREE_TIE_NO_EXILE",
    "WOLF_KILL_DEATH",
    "POISON_INDEPENDENT_DEATH",
    "EXILE_LAST_WOLF",
    "WOLVES_REACH_PARITY",
]
GOLDEN_REQUIRED_ASSERTIONS: dict[str, tuple[GoldenAssertion, ...]] = {
    "first_night_peaceful": ("PEACEFUL_FIRST_NIGHT",),
    "first_night_one_death": ("WOLF_KILL_DEATH",),
    "antidote_saves_kill": ("ANTIDOTE_SAVE",),
    "poison_independent_death": ("POISON_INDEPENDENT_DEATH",),
    "wolf_consensus_and_empty_knife": ("WOLF_CONSENSUS_EMPTY_KNIFE",),
    "pk_two_tie": ("PK_TWO_TIE_NO_EXILE",),
    "pk_three_tie": ("PK_THREE_TIE_NO_EXILE",),
    "exile_last_wolf": ("EXILE_LAST_WOLF",),
    "wolves_reach_parity": ("WOLVES_REACH_PARITY",),
}


class PlayerPolicy(Protocol):
    kind: PolicyKind

    def choose(self, view: SeatView, rng: random.Random) -> PlayerCommand:
        raise NotImplementedError


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
)


def _first_action(view: SeatView, preferred: tuple[CommandType, ...]) -> LegalAction:
    for action_type in preferred:
        for action in view.legal_actions:
            if action.action is action_type:
                return action
    if view.legal_actions:
        return view.legal_actions[0]
    raise AssertionError(f"seat {view.seat_id} has no legal action")


def _first_target(action: LegalAction) -> int:
    if not action.target_seat_ids:
        raise AssertionError(f"{action.action.value} requires a target")
    return action.target_seat_ids[0]


def _command_for(
    view: SeatView,
    action: LegalAction,
    *,
    target_seat_id: int | None = None,
) -> PlayerCommand:
    command_type = action.action
    if command_type is CommandType.JOIN_ROOM:
        return JoinRoomCommand(seat_id=view.seat_id, display_name=f"P{view.seat_id}")
    if command_type is CommandType.SET_READY:
        return SetReadyCommand(ready=True)
    if command_type is CommandType.CONFIRM_ROLE:
        return ConfirmRoleCommand()
    if command_type is CommandType.WOLF_NOMINATE_KILL:
        target = _first_target(action) if target_seat_id is None else target_seat_id
        return WolfNominateKillCommand(target_seat_id=target)
    if command_type is CommandType.SEER_INSPECT:
        target = _first_target(action) if target_seat_id is None else target_seat_id
        return SeerInspectCommand(target_seat_id=target)
    if command_type is CommandType.WITCH_USE_ANTIDOTE:
        return WitchUseAntidoteCommand()
    if command_type is CommandType.WITCH_USE_POISON:
        target = _first_target(action) if target_seat_id is None else target_seat_id
        return WitchUsePoisonCommand(target_seat_id=target)
    if command_type is CommandType.WITCH_SKIP:
        return WitchSkipCommand()
    if command_type is CommandType.SPEAK:
        return SpeakCommand(text="Public claim.")
    if command_type is CommandType.PASS_SPEECH:
        return PassSpeechCommand()
    if command_type is CommandType.VOTE:
        target = _first_target(action) if target_seat_id is None else target_seat_id
        return VoteCommand(target_seat_id=target)
    if command_type is CommandType.ABSTAIN:
        return AbstainCommand()
    if command_type is CommandType.RECONNECT:
        raise AssertionError("simulation policies do not submit RECONNECT")
    raise AssertionError(f"unsupported policy action {command_type.value}")


class RandomPolicy:
    kind: PolicyKind = "random"

    def choose(self, view: SeatView, rng: random.Random) -> PlayerCommand:
        action = rng.choice(view.legal_actions)
        target = rng.choice(action.target_seat_ids) if action.target_seat_ids else None
        return _command_for(view, action, target_seat_id=target)


class ConservativePolicy:
    kind: PolicyKind = "conservative"

    def choose(self, view: SeatView, rng: random.Random) -> PlayerCommand:
        del rng
        action = _first_action(
            view,
            (
                CommandType.WITCH_SKIP,
                CommandType.ABSTAIN,
                CommandType.PASS_SPEECH,
            ),
        )
        target = _first_target(action) if action.target_seat_ids else None
        return _command_for(view, action, target_seat_id=target)


class AggressivePolicy:
    kind: PolicyKind = "aggressive"

    def choose(self, view: SeatView, rng: random.Random) -> PlayerCommand:
        del rng
        action = _first_action(
            view,
            (
                CommandType.WITCH_USE_POISON,
                CommandType.WITCH_USE_ANTIDOTE,
                CommandType.VOTE,
                CommandType.SPEAK,
                CommandType.WOLF_NOMINATE_KILL,
                CommandType.SEER_INSPECT,
                CommandType.CONFIRM_ROLE,
                CommandType.SET_READY,
                CommandType.JOIN_ROOM,
                CommandType.WITCH_SKIP,
                CommandType.ABSTAIN,
                CommandType.PASS_SPEECH,
            ),
        )
        target = _first_target(action) if action.target_seat_ids else None
        return _command_for(view, action, target_seat_id=target)


class GameRecord(StrictModel):
    room_id: UUID
    seed: int = Field(ge=0)
    final_state: GameState
    revision_count: int = Field(ge=0)
    state_hashes: tuple[str, ...]
    policy_kinds: frozenset[PolicyKind]
    violations: tuple[str, ...]
    projection_checks: int = Field(ge=0)

    @classmethod
    def from_core(
        cls,
        seed: int,
        policies: dict[int, PlayerPolicy],
        core: GameCore,
        hashes: list[str],
        violations: list[str],
        projection_checks: int,
    ) -> Self:
        return cls(
            room_id=core.state.room_id,
            seed=seed,
            final_state=core.state,
            revision_count=core.state.revision,
            state_hashes=tuple(hashes),
            policy_kinds=frozenset(policy.kind for policy in policies.values()),
            violations=tuple(violations),
            projection_checks=projection_checks,
        )


def deterministic_room_id(seed: int) -> UUID:
    return uuid5(NAMESPACE_URL, f"werewolf-dm:seeded-game:{seed}")


def seat_actor(room_id: UUID, seat_id: int) -> AuthenticatedActor:
    return AuthenticatedActor(actor_type="seat", seat_id=seat_id, room_id=room_id)


def pending_actor_ids(state: GameState) -> tuple[int, ...]:
    if state.phase is Phase.LOBBY:
        joined = {player.seat_id for player in state.players}
        missing = next((seat_id for seat_id in range(1, 7) if seat_id not in joined), None)
        if missing is not None:
            return (missing,)
        return tuple(player.seat_id for player in state.players if not player.ready)
    if state.phase is Phase.ROLE_REVEAL:
        return tuple(player.seat_id for player in state.players if not player.role_confirmed)
    if state.phase is Phase.NIGHT_WOLF:
        nominated = {nomination.seat_id for nomination in state.wolf_decision.nominations}
        return tuple(
            player.seat_id
            for player in state.players
            if player.alive and player.role is Role.WEREWOLF and player.seat_id not in nominated
        )
    if state.phase in {Phase.DAY_VOTE, Phase.DAY_PK_VOTE}:
        round_ = state.vote_round
        if round_ is None or round_.closed:
            return ()
        voted = {vote.voter_seat_id for vote in round_.votes}
        return tuple(seat_id for seat_id in round_.eligible_voter_ids if seat_id not in voted)
    return tuple(
        seat_id
        for seat_id in range(1, 7)
        if project_seat_view(
            state,
            seat_id,
            seat_actor(state.room_id, seat_id),
        ).legal_actions
    )


def make_envelope(
    core: GameCore,
    seat_id: int,
    command: PlayerCommand,
    rng: random.Random,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=UUID(int=rng.getrandbits(128), version=4),
        room_id=core.state.room_id,
        expected_revision=core.state.revision,
        issued_at=core.clock(),
        payload=command,
    )


def advance_to_next_decision(core: GameCore) -> None:
    if core.state.deadline_at is None:
        raise AssertionError("no pending actor and no deadline")
    core.clock.set(core.state.deadline_at)
    core.tick()


def _policy_rng(seed: int, phase: Phase, revision: int, kind: PolicyKind) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{phase.value}:{revision}:{kind}".encode("ascii")).digest()
    return random.Random(int.from_bytes(digest, "big"))


def _event_actor_seat_id(
    event_type: EventType,
    payload: Mapping[str, object],
) -> int | None:
    if event_type in {
        EventType.WOLF_NOMINATION_RECORDED,
        EventType.SPEECH_RECORDED,
        EventType.SPEECH_PASSED,
    }:
        return cast(int, payload["seat_id"])
    if event_type in {EventType.VOTE_RECORDED, EventType.ABSTAIN_RECORDED}:
        return cast(int, payload["voter_seat_id"])
    if event_type is EventType.SEER_CHECKED:
        return cast(int, payload["seer_seat_id"])
    return None


def record_invariants(
    core: GameCore,
    hashes: list[str],
    violations: list[str],
    *,
    events: tuple[DomainEvent, ...] | None = None,
) -> int:
    event_log = core.events if events is None else events
    try:
        validate_invariants(core.state)
    except (AssertionError, ValueError) as exc:
        violations.append(f"seed={core.seed} revision={core.state.revision}: {exc}")

    if len(set(core.state.night.deaths)) != len(core.state.night.deaths):
        violations.append(f"seed={core.seed}: duplicate death ids")

    dead_seat_ids: set[int] = set()
    for event in event_log:
        if event.event_type is EventType.PLAYERS_DIED and event.visibility.scope == "public":
            raw_seat_ids = event.fact_payload.get("seat_ids")
            if isinstance(raw_seat_ids, (list, tuple)):
                for raw_seat_id in cast(Sequence[object], raw_seat_ids):
                    if not isinstance(raw_seat_id, int):
                        continue
                    if raw_seat_id in dead_seat_ids:
                        violations.append(
                            f"seed={core.seed} revision={event.revision}: "
                            f"duplicate death for seat {raw_seat_id}"
                        )
                    else:
                        dead_seat_ids.add(raw_seat_id)
        elif event.event_type is EventType.PLAYER_EXILED:
            raw_seat_id = event.fact_payload.get("seat_id")
            if isinstance(raw_seat_id, int):
                if raw_seat_id in dead_seat_ids:
                    violations.append(
                        f"seed={core.seed} revision={event.revision}: "
                        f"duplicate death or exile for seat {raw_seat_id}"
                    )
                else:
                    dead_seat_ids.add(raw_seat_id)

        event_actor = _event_actor_seat_id(event.event_type, event.fact_payload)
        if event.event_type is EventType.WITCH_ACTION_RECORDED and event.visibility.scope == "seat":
            event_actor = event.visibility.seat_id
        if event_actor is not None and event_actor in dead_seat_ids:
            violations.append(
                f"seed={core.seed} revision={event.revision}: dead seat {event_actor} acted"
            )

        if event.event_type is EventType.VOTE_ROUND_RESOLVED:
            raw_tallies = event.fact_payload.get("tallies")
            if isinstance(raw_tallies, (list, tuple)):
                for raw_tally in cast(Sequence[object], raw_tallies):
                    if not isinstance(raw_tally, Mapping):
                        continue
                    votes = cast(Mapping[str, object], raw_tally).get("votes")
                    if isinstance(votes, int) and votes < 0:
                        violations.append(
                            f"seed={core.seed} revision={event.revision}: negative vote tally"
                        )
        if event.event_type is EventType.PHASE_CHANGED:
            previous = cast(str | None, event.fact_payload["previous_phase"])
            next_phase = Phase(cast(str, event.fact_payload["next_phase"]))
            if previous is not None and next_phase not in ALLOWED_TRANSITIONS[Phase(previous)]:
                violations.append(
                    f"seed={core.seed} revision={event.revision}: illegal phase transition"
                )

    checks = 0
    try:
        project_public_view(core.state)
    except (AssertionError, ValueError) as exc:
        violations.append(f"seed={core.seed}: public projection failed: {exc}")
    else:
        checks += 1
    for seat_id in range(1, 7):
        try:
            project_seat_view(
                core.state,
                seat_id,
                seat_actor(core.state.room_id, seat_id),
            )
        except (AssertionError, ValueError) as exc:
            violations.append(f"seed={core.seed}: seat {seat_id} projection failed: {exc}")
        else:
            checks += 1
    return checks


def run_seeded_game(seed: int) -> GameRecord:
    base = random.Random(seed)
    policies: dict[int, PlayerPolicy] = {
        1: AggressivePolicy(),
        2: ConservativePolicy(),
        3: RandomPolicy(),
        4: RandomPolicy(),
        5: ConservativePolicy(),
        6: AggressivePolicy(),
    }
    core = GameCore.new_room(
        room_id=deterministic_room_id(seed),
        seed=seed,
        clock=FrozenClock(datetime(2026, 1, 1)),
    )
    hashes = [state_hash(core.state)]
    violations: list[str] = []
    projection_checks = 0
    iterations = 0
    no_progress = 0

    while core.state.phase is not Phase.GAME_END:
        iterations += 1
        if iterations > 5000:
            raise AssertionError(f"seed={seed}: iteration cap reached")
        if core.state.revision > 500:
            raise AssertionError(f"seed={seed}: revision cap reached")
        if core.state.deadline_at is not None and core.clock() >= core.state.deadline_at:
            core.tick()
            hashes.append(state_hash(core.state))
            projection_checks += record_invariants(core, hashes, violations)
            continue

        actor_ids = pending_actor_ids(core.state)
        if not actor_ids:
            advance_to_next_decision(core)
            continue

        seat_id = actor_ids[0]
        policy = policies[seat_id]
        view = project_seat_view(
            core.state,
            seat_id,
            seat_actor(core.state.room_id, seat_id),
        )
        command = policy.choose(
            view,
            _policy_rng(seed, core.state.phase, core.state.revision, policy.kind),
        )
        revision_before = core.state.revision
        result = core.submit(
            make_envelope(core, seat_id, command, base),
            seat_actor(core.state.room_id, seat_id),
        )
        if result.accepted and core.state.revision > revision_before:
            no_progress = 0
        else:
            no_progress += 1
        if no_progress > 200:
            raise AssertionError(f"seed={seed}: no accepted progress for 200 iterations")
        hashes.append(state_hash(core.state))
        projection_checks += record_invariants(core, hashes, violations)

    if core.state.revision > 500:
        raise AssertionError(f"seed={seed}: revision cap reached")
    return GameRecord.from_core(
        seed,
        policies,
        core,
        hashes,
        violations,
        projection_checks,
    )


class GoldenStep(StrictModel):
    actor: AuthenticatedActor | None = None
    command_id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    issued_at: datetime | None = None
    payload: CommandPayload | None = None
    timeout: SystemTimeout | None = None
    now: datetime

    @model_validator(mode="after")
    def check_exactly_one_trigger(self) -> Self:
        command_fields = (
            self.actor,
            self.command_id,
            self.expected_revision,
            self.issued_at,
            self.payload,
        )
        if self.timeout is None and any(value is None for value in command_fields):
            raise ValueError("command step requires all command fields")
        if self.timeout is not None and any(value is not None for value in command_fields):
            raise ValueError("timeout step cannot contain command fields")
        return self


class GoldenExpected(StrictModel):
    phase: Phase
    winner: Faction | None
    deaths: tuple[int, ...]
    outbox_kinds: tuple[OutboxKind, ...]
    semantic_assertions: tuple[GoldenAssertion, ...] = Field(min_length=1)


class GoldenHashes(StrictModel):
    replay_1: tuple[str, ...]
    replay_2: tuple[str, ...]


class GoldenDocument(StrictModel):
    scenario: str
    room_id: UUID
    seed: int = Field(ge=0)
    steps: tuple[GoldenStep, ...]
    expected: GoldenExpected
    state_hashes: GoldenHashes

    @model_validator(mode="after")
    def check_scenario_semantics(self) -> Self:
        required = GOLDEN_REQUIRED_ASSERTIONS.get(self.scenario)
        if required is None:
            raise ValueError(f"unknown golden scenario: {self.scenario}")
        if self.expected.semantic_assertions != required:
            raise ValueError(f"golden scenario {self.scenario} requires assertions {required!r}")
        return self


def _golden_path(name: str) -> Path:
    if Path(name).name != name or name.endswith(".json"):
        raise ValueError(f"invalid golden scenario name: {name}")
    return Path(__file__).resolve().parents[3] / "tests" / "headless" / "golden" / f"{name}.json"


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _actual_death_or_exile_seats(events: tuple[DomainEvent, ...]) -> tuple[int, ...]:
    deaths: list[int] = []
    for event in events:
        if event.event_type is EventType.PLAYERS_DIED and event.visibility.scope == "public":
            raw_seat_ids = event.fact_payload.get("seat_ids")
            if isinstance(raw_seat_ids, (list, tuple)):
                deaths.extend(
                    item for item in cast(Sequence[object], raw_seat_ids) if isinstance(item, int)
                )
        elif event.event_type is EventType.PLAYER_EXILED:
            raw_seat_id = event.fact_payload.get("seat_id")
            if isinstance(raw_seat_id, int):
                deaths.append(raw_seat_id)
    return tuple(deaths)


def _event_seat_ids(event: DomainEvent, key: str) -> tuple[int, ...]:
    raw = event.fact_payload.get(key)
    if type(raw) is int:
        return (raw,)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(item for item in cast(Sequence[object], raw) if type(item) is int)


def _remaining_wolves(
    state: GameState | None,
    living_seat_ids: set[int],
) -> int:
    if state is None:
        return 0
    roles = {player.seat_id: player.role for player in state.players}
    return sum(1 for seat_id in living_seat_ids if roles.get(seat_id) is Role.WEREWOLF)


def _exile_last_wolf(
    state: GameState | None,
    events: tuple[DomainEvent, ...],
) -> bool:
    if state is None:
        return False

    living_seat_ids = {player.seat_id for player in state.players}
    exiled_last_wolf = False
    for event in events:
        if event.event_type is EventType.PLAYERS_DIED:
            living_seat_ids.difference_update(_event_seat_ids(event, "seat_ids"))
        elif event.event_type is EventType.PLAYER_EXILED:
            seat_ids = _event_seat_ids(event, "seat_id")
            if not seat_ids:
                continue
            exiled_seat_id = seat_ids[0]
            exiled_role = next(
                (player.role for player in state.players if player.seat_id == exiled_seat_id),
                None,
            )
            if (
                exiled_seat_id in living_seat_ids
                and _remaining_wolves(state, living_seat_ids) == 1
                and exiled_role is Role.WEREWOLF
            ):
                exiled_last_wolf = True
            living_seat_ids.discard(exiled_seat_id)

    good_win = any(
        event.event_type is EventType.GAME_ENDED
        and event.fact_payload.get("winner") == Faction.GOOD.value
        for event in events
    )
    return exiled_last_wolf and good_win and _remaining_wolves(state, living_seat_ids) == 0


def _wolves_reach_parity(
    state: GameState | None,
    events: tuple[DomainEvent, ...],
) -> bool:
    if state is None:
        return False
    wolf_win = any(
        event.event_type is EventType.GAME_ENDED
        and event.fact_payload.get("winner") == Faction.WEREWOLF.value
        for event in events
    )
    living_players = tuple(player for player in state.players if player.alive)
    living_wolves = sum(player.role is Role.WEREWOLF for player in living_players)
    living_good = sum(player.role is not Role.WEREWOLF for player in living_players)
    return wolf_win and living_wolves > 0 and living_wolves == living_good


def _wolf_consensus_then_empty_knife(events: tuple[DomainEvent, ...]) -> bool:
    nominations: dict[int, int] = {}
    saw_consensus_lock = False
    saw_later_empty_knife = False
    for event in events:
        if event.event_type is EventType.WOLF_NOMINATION_RECORDED:
            seat_id = event.fact_payload.get("seat_id")
            target_seat_id = event.fact_payload.get("target_seat_id")
            if type(seat_id) is int and type(target_seat_id) is int:
                nominations[seat_id] = target_seat_id
        elif event.event_type is EventType.WOLF_TARGET_LOCKED:
            target_seat_id = event.fact_payload.get("target_seat_id")
            if type(target_seat_id) is int:
                supporting_wolves = {
                    seat_id
                    for seat_id, nomination in nominations.items()
                    if nomination == target_seat_id
                }
                if len(supporting_wolves) >= 2:
                    saw_consensus_lock = True
            elif (
                saw_consensus_lock and len(nominations) >= 2 and len(set(nominations.values())) >= 2
            ):
                saw_later_empty_knife = True
            nominations.clear()
    return saw_consensus_lock and saw_later_empty_knife


def _second_pk_result(events: tuple[DomainEvent, ...], candidate_count: int) -> bool:
    no_exile_index = next(
        (
            index
            for index in range(len(events) - 1, -1, -1)
            if events[index].event_type is EventType.NO_EXILE
            and events[index].fact_payload.get("reason") == "PK_TIE"
        ),
        None,
    )
    if no_exile_index is None:
        return False
    preceding_resolution = next(
        (
            events[index]
            for index in range(no_exile_index - 1, -1, -1)
            if events[index].event_type is EventType.VOTE_ROUND_RESOLVED
        ),
        None,
    )
    if preceding_resolution is None:
        return False
    tallies = preceding_resolution.fact_payload.get("tallies")
    if not isinstance(tallies, (list, tuple)) or len(tallies) != candidate_count:
        return False
    vote_counts: list[int] = []
    candidate_seat_ids: list[int] = []
    for tally in cast(Sequence[object], tallies):
        seat_id = (
            tally.get("seat_id") if isinstance(tally, Mapping) else getattr(tally, "seat_id", None)
        )
        votes = tally.get("votes") if isinstance(tally, Mapping) else getattr(tally, "votes", None)
        if type(seat_id) is not int or type(votes) is not int or votes <= 0:
            return False
        candidate_seat_ids.append(seat_id)
        vote_counts.append(votes)
    return (
        preceding_resolution.fact_payload.get("tie") is True
        and len(set(vote_counts)) == 1
        and len(set(candidate_seat_ids)) == candidate_count
    )


def _semantic_assertions_pass(
    events: tuple[DomainEvent, ...],
    assertions: tuple[GoldenAssertion, ...],
    *,
    state: GameState | None = None,
) -> bool:
    if not assertions:
        return False
    for assertion in assertions:
        if assertion == "PEACEFUL_FIRST_NIGHT":
            has_empty_knife = any(
                event.event_type is EventType.WOLF_TARGET_LOCKED
                and event.fact_payload.get("target_seat_id") is None
                for event in events
            )
            no_deaths = all(
                not _event_seat_ids(event, "seat_ids")
                for event in events
                if event.event_type is EventType.PLAYERS_DIED and event.visibility.scope == "public"
            )
            witch_skipped = any(
                event.event_type is EventType.WITCH_ACTION_RECORDED
                and event.fact_payload.get("action") == "SKIP"
                for event in events
            )
            no_wolf_nominations = not any(
                event.event_type is EventType.WOLF_NOMINATION_RECORDED for event in events
            )
            if not (has_empty_knife and no_deaths and witch_skipped and no_wolf_nominations):
                return False
        elif assertion == "WOLF_CONSENSUS_EMPTY_KNIFE":
            if not _wolf_consensus_then_empty_knife(events):
                return False
        elif assertion == "ANTIDOTE_SAVE":
            antidote_used = any(
                event.event_type is EventType.WITCH_ACTION_RECORDED
                and event.fact_payload.get("action") == "ANTIDOTE"
                for event in events
            )
            public_deaths = tuple(
                event
                for event in events
                if event.event_type is EventType.PLAYERS_DIED and event.visibility.scope == "public"
            )
            no_deaths = bool(public_deaths) and all(
                tuple(cast(Sequence[object], event.fact_payload["seat_ids"])) == ()
                for event in public_deaths
            )
            if not (antidote_used and no_deaths):
                return False
        elif assertion in {"PK_TWO_TIE_NO_EXILE", "PK_THREE_TIE_NO_EXILE"}:
            candidate_count = 2 if assertion == "PK_TWO_TIE_NO_EXILE" else 3
            if not _second_pk_result(events, candidate_count):
                return False
        elif assertion == "WOLF_KILL_DEATH":
            public_death = next(
                (
                    event
                    for event in events
                    if event.event_type is EventType.PLAYERS_DIED
                    and event.visibility.scope == "public"
                    and event.fact_payload.get("seat_ids")
                ),
                None,
            )
            host_wolf_death = any(
                event.event_type is EventType.PLAYERS_DIED
                and event.visibility.scope == "host"
                and event.fact_payload.get("cause") == "WOLF"
                for event in events
            )
            if public_death is None or not host_wolf_death:
                return False
        elif assertion == "POISON_INDEPENDENT_DEATH":
            poison_death_event = next(
                (
                    event
                    for event in events
                    if event.event_type is EventType.PLAYERS_DIED
                    and event.visibility.scope == "public"
                    and len(cast(Sequence[object], event.fact_payload.get("seat_ids", ()))) >= 2
                ),
                None,
            )
            host_poison_death = any(
                event.event_type is EventType.PLAYERS_DIED
                and event.visibility.scope == "host"
                and event.fact_payload.get("cause") in {"POISON", "MIXED"}
                for event in events
            )
            if poison_death_event is None or not host_poison_death:
                return False
        elif assertion == "EXILE_LAST_WOLF":
            if not _exile_last_wolf(state, events):
                return False
        elif assertion == "WOLVES_REACH_PARITY":
            if not _wolves_reach_parity(state, events):
                return False
        else:
            return False
    return True


def run_golden_replay(name: str, *, update_hashes: bool = False) -> bool:
    path = _golden_path(name)
    raw_text = path.read_text(encoding="utf-8")
    try:
        document = GoldenDocument.model_validate_json(raw_text)
    except ValueError:
        return False
    if document.scenario != name:
        return False
    steps: list[ReplayStep] = []
    for step in document.steps:
        if step.timeout is not None:
            steps.append(ReplayStep(timeout=step.timeout, now=step.now))
            continue
        assert step.actor is not None
        assert step.command_id is not None
        assert step.expected_revision is not None
        assert step.issued_at is not None
        assert step.payload is not None
        steps.append(
            ReplayStep(
                actor=step.actor,
                envelope=CommandEnvelope(
                    command_id=step.command_id,
                    room_id=document.room_id,
                    expected_revision=step.expected_revision,
                    issued_at=step.issued_at,
                    payload=step.payload,
                ),
                now=step.now,
            )
        )
    replay_steps = tuple(steps)
    first = replay(document.room_id, document.seed, replay_steps)
    second = replay(document.room_id, document.seed, replay_steps)
    first_hashes = first.state_hashes
    second_hashes = second.state_hashes
    expected_hash_count = len(document.steps) + 1
    hashes_are_well_formed = (
        len(first_hashes) == expected_hash_count
        and len(second_hashes) == expected_hash_count
        and all(_is_sha256(value) for value in (*first_hashes, *second_hashes))
    )
    hashes_match = first_hashes == second_hashes

    if update_hashes and hashes_are_well_formed and hashes_match:
        updated = document.model_copy(
            update={
                "state_hashes": GoldenHashes(
                    replay_1=first_hashes,
                    replay_2=second_hashes,
                )
            }
        )
        path.write_text(
            json.dumps(updated.model_dump(mode="json"), indent=2) + "\n",
            encoding="utf-8",
        )
        document = updated

    expected_hashes_match = (
        document.state_hashes.replay_1 == first_hashes
        and document.state_hashes.replay_2 == second_hashes
    )
    expected_deaths = _actual_death_or_exile_seats(first.events)
    return (
        all(result.accepted for result in first.command_results)
        and all(result.accepted for result in first.timeout_results)
        and first.command_results == second.command_results
        and tuple(event.event_type for event in first.events)
        == tuple(event.event_type for event in second.events)
        and first.final_state == second.final_state
        and hashes_are_well_formed
        and hashes_match
        and expected_hashes_match
        and first.final_state.phase is document.expected.phase
        and first.final_state.winner is document.expected.winner
        and expected_deaths == document.expected.deaths
        and tuple(item.kind for item in first.final_state.outbox) == document.expected.outbox_kinds
        and _semantic_assertions_pass(
            first.events,
            document.expected.semantic_assertions,
            state=first.final_state,
        )
    )
