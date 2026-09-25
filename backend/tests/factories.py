from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4

from werewolf_dm.application.core import FrozenClock, GameCore
from werewolf_dm.domain.contracts import (
    AbstainCommand,
    AuthenticatedActor,
    CommandEnvelope,
    CommandResult,
    ConfirmRoleCommand,
    HostCommand,
    JoinRoomCommand,
    PassSpeechCommand,
    PlayerCommand,
    SeerInspectCommand,
    SetReadyCommand,
    VoteCommand,
    WitchSkipCommand,
    WitchUseAntidoteCommand,
    WitchUsePoisonCommand,
    WolfNominateKillCommand,
)
from werewolf_dm.domain.enums import Faction, Phase, Role
from werewolf_dm.domain.model import StrictModel
from werewolf_dm.domain.state_machine import (
    assign_roles,
    deterministic_uuid,
)

START_TIME = datetime(2026, 1, 1, tzinfo=UTC)
ROOM_ID = UUID("00000000-0000-0000-0000-000000000001")


class Scenario(StrictModel):
    core: Any
    wolf_ids: tuple[int, ...]
    good_ids: tuple[int, ...]
    seer_id: int
    witch_id: int


def utc(seconds: int = 0) -> datetime:
    return START_TIME + timedelta(seconds=seconds)


def _replay_step(
    *,
    step_index: int,
    seat_id: int,
    payload: PlayerCommand,
    expected_revision: int,
) -> Any:
    from werewolf_dm.application.replay import ReplayStep

    actor = seat_actor(seat_id)
    now = START_TIME + timedelta(seconds=step_index)
    return ReplayStep(
        actor=actor,
        envelope=CommandEnvelope(
            command_id=deterministic_uuid(
                NAMESPACE_URL,
                ROOM_ID,
                step_index,
                payload.command_type.value,
            ),
            room_id=ROOM_ID,
            expected_revision=expected_revision,
            issued_at=now,
            payload=payload,
        ),
        now=now,
    )


def role_reveal_script() -> tuple[Any, ...]:
    steps: list[Any] = []
    for seat_id in range(1, 7):
        steps.append(
            _replay_step(
                step_index=len(steps),
                seat_id=seat_id,
                payload=JoinRoomCommand(seat_id=seat_id, display_name=f"P{seat_id}"),
                expected_revision=len(steps),
            )
        )
    for seat_id in range(1, 7):
        steps.append(
            _replay_step(
                step_index=len(steps),
                seat_id=seat_id,
                payload=SetReadyCommand(ready=True),
                expected_revision=len(steps),
            )
        )
    return tuple(steps)


def minimal_night_script(seed: int = 101) -> tuple[Any, ...]:
    steps = list(role_reveal_script())
    for seat_id in range(1, 7):
        steps.append(
            _replay_step(
                step_index=len(steps),
                seat_id=seat_id,
                payload=ConfirmRoleCommand(),
                expected_revision=len(steps),
            )
        )

    assignments = {assignment.role: assignment.seat_id for assignment in assign_roles(seed)}
    wolf_ids = tuple(
        assignment.seat_id for assignment in assign_roles(seed) if assignment.role is Role.WEREWOLF
    )
    target_seat_id = next(
        seat_id
        for seat_id in range(1, 7)
        if seat_id not in wolf_ids and seat_id != assignments[Role.SEER]
    )
    for wolf_id in wolf_ids:
        steps.append(
            _replay_step(
                step_index=len(steps),
                seat_id=wolf_id,
                payload=WolfNominateKillCommand(target_seat_id=target_seat_id),
                expected_revision=len(steps),
            )
        )
    steps.append(
        _replay_step(
            step_index=len(steps),
            seat_id=assignments[Role.SEER],
            payload=SeerInspectCommand(target_seat_id=wolf_ids[0]),
            expected_revision=len(steps),
        )
    )
    steps.append(
        _replay_step(
            step_index=len(steps),
            seat_id=assignments[Role.WITCH],
            payload=WitchSkipCommand(),
            expected_revision=len(steps),
        )
    )
    return tuple(steps)


def seat_actor(seat_id: int) -> AuthenticatedActor:
    return AuthenticatedActor(actor_type="seat", seat_id=seat_id, room_id=ROOM_ID)


def host_actor() -> AuthenticatedActor:
    return AuthenticatedActor(actor_type="host", seat_id=None, room_id=ROOM_ID)


def make_envelope(
    actor: AuthenticatedActor,
    payload: PlayerCommand | HostCommand,
    *,
    expected_revision: int | None = None,
    command_id: UUID | None = None,
    now: datetime | None = None,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command_id or uuid4(),
        room_id=actor.room_id,
        expected_revision=0 if expected_revision is None else expected_revision,
        issued_at=now or START_TIME,
        payload=payload,
    )


def envelope_for(
    core: Any,
    seat_id: int,
    payload: PlayerCommand,
) -> CommandEnvelope:
    return make_envelope(
        seat_actor(seat_id),
        payload,
        expected_revision=core.state.revision,
        now=utc(core.state.revision),
    )


def submit_command(
    core: Any,
    actor: AuthenticatedActor,
    command: CommandEnvelope,
) -> CommandResult:
    return core.submit(command, actor)


def core() -> GameCore:
    return GameCore.new_room(
        room_id=ROOM_ID,
        seed=101,
        clock=FrozenClock(START_TIME),
    )


def _submit_player_command(
    game_core: GameCore,
    seat_id: int,
    payload: PlayerCommand,
    seconds: int,
) -> CommandResult:
    seat = seat_actor(seat_id)
    return game_core.submit(
        make_envelope(
            seat,
            payload,
            expected_revision=game_core.state.revision,
            now=utc(seconds),
        ),
        seat,
    )


def core_with_two_joined_players() -> GameCore:
    game_core = core()
    for second, seat_id in enumerate((1, 2)):
        _submit_player_command(
            game_core,
            seat_id,
            JoinRoomCommand(seat_id=seat_id, display_name=f"P{seat_id}"),
            second,
        )
    return game_core


def core_at_role_reveal() -> GameCore:
    game_core = core()
    for second, seat_id in enumerate(range(1, 7)):
        _submit_player_command(
            game_core,
            seat_id,
            JoinRoomCommand(seat_id=seat_id, display_name=f"P{seat_id}"),
            second,
        )
    for second, seat_id in enumerate(range(1, 7), start=6):
        _submit_player_command(
            game_core,
            seat_id,
            SetReadyCommand(ready=True),
            second,
        )
    return game_core


def tick_to_role_reveal_deadline(game_core: GameCore) -> None:
    assert game_core.state.deadline_at is not None
    game_core.clock.set(game_core.state.deadline_at)
    game_core.tick()


def _seat_for_role(game_core: GameCore, role: Role) -> int:
    matches = tuple(player.seat_id for player in game_core.state.players if player.role is role)
    assert len(matches) == 1
    return matches[0]


def _scenario(game_core: GameCore) -> Scenario:
    wolf_ids = tuple(
        player.seat_id for player in game_core.state.players if player.role is Role.WEREWOLF
    )
    good_ids = tuple(
        player.seat_id for player in game_core.state.players if player.role is not Role.WEREWOLF
    )
    return Scenario(
        core=game_core,
        wolf_ids=wolf_ids,
        good_ids=good_ids,
        seer_id=_seat_for_role(game_core, Role.SEER),
        witch_id=_seat_for_role(game_core, Role.WITCH),
    )


def core_at_wolf() -> Scenario:
    game_core = core_at_role_reveal()
    for second, seat_id in enumerate(range(1, 7), start=12):
        _submit_player_command(game_core, seat_id, ConfirmRoleCommand(), second)
    assert game_core.state.phase is Phase.NIGHT_WOLF
    return _scenario(game_core)


def core_at_seer() -> tuple[Scenario, int]:
    scenario = core_at_wolf()
    target = scenario.good_ids[0]
    for wolf_id in scenario.wolf_ids:
        result = _submit_player_command(
            scenario.core,
            wolf_id,
            WolfNominateKillCommand(target_seat_id=target),
            scenario.core.state.revision,
        )
        assert result.accepted
    assert scenario.core.state.phase is Phase.NIGHT_SEER
    return scenario, scenario.wolf_ids[0]


def _finish_seer_phase(scenario: Scenario, target_seat_id: int) -> None:
    result = _submit_player_command(
        scenario.core,
        scenario.seer_id,
        SeerInspectCommand(target_seat_id=target_seat_id),
        scenario.core.state.revision,
    )
    assert result.accepted
    assert scenario.core.state.phase is Phase.NIGHT_WITCH


def core_at_witch() -> tuple[Scenario, int]:
    scenario = core_at_wolf()
    target = next(seat_id for seat_id in scenario.good_ids if seat_id != scenario.witch_id)
    for wolf_id in scenario.wolf_ids:
        result = _submit_player_command(
            scenario.core,
            wolf_id,
            WolfNominateKillCommand(target_seat_id=target),
            scenario.core.state.revision,
        )
        assert result.accepted
    _finish_seer_phase(scenario, scenario.wolf_ids[0])
    return scenario, target


def core_at_day_vote() -> GameCore:
    scenario, _ = core_at_witch()
    game_core = scenario.core
    witch = seat_actor(scenario.witch_id)
    result = game_core.submit(
        make_envelope(
            witch,
            WitchUseAntidoteCommand(),
            expected_revision=game_core.state.revision,
        ),
        witch,
    )
    assert result.accepted is True
    assert game_core.state.phase is Phase.DAY_DISCUSSION

    while game_core.state.phase is Phase.DAY_DISCUSSION:
        discussion = game_core.state.discussion
        assert discussion is not None
        assert discussion.current_seat_id is not None
        speaker = seat_actor(discussion.current_seat_id)
        result = game_core.submit(
            make_envelope(
                speaker,
                PassSpeechCommand(),
                expected_revision=game_core.state.revision,
            ),
            speaker,
        )
        assert result.accepted is True

    assert game_core.state.phase is Phase.DAY_VOTE
    return game_core


def core_at_pk_vote() -> GameCore:
    game_core = core_at_day_vote()
    vote_round = game_core.state.vote_round
    assert vote_round is not None
    votes = {
        voter_seat_id: (2, 3)[index % 2]
        for index, voter_seat_id in enumerate(vote_round.eligible_voter_ids)
    }
    submit_votes(game_core, votes)

    vote_round = game_core.state.vote_round
    assert game_core.state.phase is Phase.DAY_PK_DISCUSSION
    assert vote_round is not None
    for candidate_seat_id in vote_round.candidate_seat_ids:
        candidate = seat_actor(candidate_seat_id)
        result = game_core.submit(
            make_envelope(
                candidate,
                PassSpeechCommand(),
                expected_revision=game_core.state.revision,
            ),
            candidate,
        )
        assert result.accepted is True

    assert game_core.state.phase is Phase.DAY_PK_VOTE
    return game_core


def core_with_witch_as_target() -> Scenario:
    scenario, _ = core_at_seer()
    _finish_seer_phase(scenario, scenario.wolf_ids[0])
    return scenario


def tick_to_wolf_deadline(game_core: GameCore) -> None:
    assert game_core.state.deadline_at is not None
    game_core.clock.set(game_core.state.deadline_at)
    game_core.tick()


def tick_to_seer_deadline(game_core: GameCore) -> None:
    assert game_core.state.deadline_at is not None
    game_core.clock.set(game_core.state.deadline_at)
    game_core.tick()


def tick_to_witch_deadline(game_core: GameCore) -> None:
    assert game_core.state.deadline_at is not None
    game_core.clock.set(game_core.state.deadline_at)
    game_core.tick()


def living_ids(game_core: GameCore) -> tuple[int, ...]:
    return tuple(player.seat_id for player in game_core.state.players if player.alive)


def faction_of(scenario: Scenario, seat_id: int) -> Faction:
    player = next(player for player in scenario.core.state.players if player.seat_id == seat_id)
    assert player.role is not None
    return Faction.WEREWOLF if player.role is Role.WEREWOLF else Faction.GOOD


def submit_votes(core: GameCore, votes: dict[int, int | None]) -> None:
    assert core.state.phase is Phase.DAY_VOTE
    for voter_seat_id, target_seat_id in votes.items():
        voter = seat_actor(voter_seat_id)
        payload = (
            AbstainCommand()
            if target_seat_id is None
            else VoteCommand(target_seat_id=target_seat_id)
        )
        result = core.submit(
            make_envelope(
                voter,
                payload,
                expected_revision=core.state.revision,
            ),
            voter,
        )
        assert result.accepted is True


def submit_pk_votes(core: GameCore, votes: dict[int, int | None]) -> None:
    assert core.state.phase is Phase.DAY_PK_VOTE
    for voter_seat_id, target_seat_id in votes.items():
        voter = seat_actor(voter_seat_id)
        payload = (
            AbstainCommand()
            if target_seat_id is None
            else VoteCommand(target_seat_id=target_seat_id)
        )
        result = core.submit(
            make_envelope(
                voter,
                payload,
                expected_revision=core.state.revision,
            ),
            voter,
        )
        assert result.accepted is True


def other_candidate(core: GameCore, candidate: int) -> int:
    vote_round = core.state.vote_round
    assert vote_round is not None
    return next(seat_id for seat_id in vote_round.candidate_seat_ids if seat_id != candidate)


def _submit_waiting_command(
    core: GameCore,
    seat_id: int,
    payload: PlayerCommand,
) -> CommandResult:
    actor = seat_actor(seat_id)
    return core.submit(
        make_envelope(
            actor,
            payload,
            expected_revision=core.state.revision,
            now=utc(core.state.revision),
        ),
        actor,
    )


def _advance_past_day(core: GameCore) -> None:
    assert core.state.phase is Phase.DAY_DISCUSSION
    while core.state.phase is Phase.DAY_DISCUSSION:
        discussion = core.state.discussion
        assert discussion is not None
        assert discussion.current_seat_id is not None
        result = _submit_waiting_command(
            core,
            discussion.current_seat_id,
            PassSpeechCommand(),
        )
        assert result.accepted is True

    assert core.state.phase is Phase.DAY_VOTE
    vote_round = core.state.vote_round
    assert vote_round is not None
    submit_votes(
        core,
        {voter_seat_id: None for voter_seat_id in vote_round.eligible_voter_ids},
    )
    assert core.state.phase is Phase.NIGHT_WOLF


def _advance_to_next_witch(core: GameCore) -> None:
    assert core.state.phase is Phase.NIGHT_WOLF
    target_seat_id = next(
        player.seat_id
        for player in core.state.players
        if player.alive and player.role is not Role.WEREWOLF
    )
    living_wolf_ids = tuple(
        player.seat_id
        for player in core.state.players
        if player.alive and player.role is Role.WEREWOLF
    )
    for wolf_id in living_wolf_ids:
        result = _submit_waiting_command(
            core,
            wolf_id,
            WolfNominateKillCommand(target_seat_id=target_seat_id),
        )
        assert result.accepted is True

    if core.state.phase is Phase.NIGHT_SEER:
        seer_id = next(
            player.seat_id
            for player in core.state.players
            if player.alive and player.role is Role.SEER
        )
        seer_target_id = next(
            player.seat_id
            for player in core.state.players
            if player.alive and player.seat_id != seer_id
        )
        result = _submit_waiting_command(
            core,
            seer_id,
            SeerInspectCommand(target_seat_id=seer_target_id),
        )
        assert result.accepted is True

    assert core.state.phase is Phase.NIGHT_WITCH


def core_after_antidote_used() -> Scenario:
    scenario, _ = core_at_witch()
    result = _submit_waiting_command(
        scenario.core,
        scenario.witch_id,
        WitchUseAntidoteCommand(),
    )
    assert result.accepted is True
    _advance_past_day(scenario.core)
    _advance_to_next_witch(scenario.core)
    return scenario


def core_after_poison_used() -> Scenario:
    scenario, _ = core_at_witch()
    result = _submit_waiting_command(
        scenario.core,
        scenario.witch_id,
        WitchUsePoisonCommand(target_seat_id=scenario.wolf_ids[0]),
    )
    assert result.accepted is True
    _advance_past_day(scenario.core)
    _advance_to_next_witch(scenario.core)
    return scenario


def core_with_one_living_wolf_and_dead_good() -> Scenario:
    scenario, _ = core_at_witch()
    result = _submit_waiting_command(
        scenario.core,
        scenario.witch_id,
        WitchUsePoisonCommand(target_seat_id=scenario.wolf_ids[0]),
    )
    assert result.accepted is True
    _advance_past_day(scenario.core)
    assert scenario.core.state.phase is Phase.NIGHT_WOLF
    return scenario


def core_with_one_living_wolf_and_dead_seer() -> Scenario:
    scenario = core_at_wolf()
    for wolf_id in scenario.wolf_ids:
        result = _submit_waiting_command(
            scenario.core,
            wolf_id,
            WolfNominateKillCommand(target_seat_id=scenario.seer_id),
        )
        assert result.accepted is True

    assert scenario.core.state.phase is Phase.NIGHT_SEER
    seer_target_id = next(
        player.seat_id
        for player in scenario.core.state.players
        if player.alive and player.seat_id != scenario.seer_id
    )
    result = _submit_waiting_command(
        scenario.core,
        scenario.seer_id,
        SeerInspectCommand(target_seat_id=seer_target_id),
    )
    assert result.accepted is True
    assert scenario.core.state.phase is Phase.NIGHT_WITCH
    result = _submit_waiting_command(
        scenario.core,
        scenario.witch_id,
        WitchUsePoisonCommand(target_seat_id=scenario.wolf_ids[0]),
    )
    assert result.accepted is True
    _advance_past_day(scenario.core)
    assert scenario.core.state.phase is Phase.NIGHT_WOLF
    return scenario
