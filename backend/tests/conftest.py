from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

import pytest

from tests.factories import (
    Scenario,
    host_actor,
    make_envelope,
    seat_actor,
    submit_command,
)
from tests.factories import (
    core as make_core,
)
from tests.factories import (
    core_after_antidote_used as make_core_after_antidote_used,
)
from tests.factories import (
    core_after_poison_used as make_core_after_poison_used,
)
from tests.factories import (
    core_at_day_vote as make_core_at_day_vote,
)
from tests.factories import (
    core_at_pk_vote as make_core_at_pk_vote,
)
from tests.factories import (
    core_at_role_reveal as make_core_at_role_reveal,
)
from tests.factories import (
    core_at_seer as make_core_at_seer,
)
from tests.factories import (
    core_at_witch as make_core_at_witch,
)
from tests.factories import (
    core_at_wolf as make_core_at_wolf,
)
from tests.factories import (
    core_with_one_living_wolf_and_dead_good as make_core_with_one_living_wolf_and_dead_good,
)
from tests.factories import (
    core_with_one_living_wolf_and_dead_seer as make_core_with_one_living_wolf_and_dead_seer,
)
from tests.factories import (
    core_with_two_joined_players as make_core_with_two_joined_players,
)
from tests.factories import (
    core_with_witch_as_target as make_core_with_witch_as_target,
)
from tests.factories import (
    utc as make_utc,
)
from werewolf_dm.application.core import GameCore
from werewolf_dm.domain.contracts import (
    AbstainCommand,
    AuthenticatedActor,
    CommandEnvelope,
    CommandResult,
    HostCommand,
    PassSpeechCommand,
    PlayerCommand,
    SeerInspectCommand,
    VoteCommand,
    WitchSkipCommand,
    WitchUsePoisonCommand,
    WolfNominateKillCommand,
)
from werewolf_dm.domain.enums import Phase, Role


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.option.markexpr:
        return
    skip_latency = pytest.mark.skip(reason="latency tests run explicitly with -m latency")
    for item in items:
        if item.get_closest_marker("latency") is not None:
            item.add_marker(skip_latency)


@pytest.fixture
def room_id() -> UUID:
    return seat_actor(1).room_id


@pytest.fixture
def utc() -> Callable[[int], datetime]:
    return make_utc


@pytest.fixture
def actor() -> Callable[[int], AuthenticatedActor]:
    return seat_actor


@pytest.fixture
def host() -> Callable[[], AuthenticatedActor]:
    return host_actor


@pytest.fixture
def envelope() -> Callable[..., CommandEnvelope]:
    def build(
        actor: AuthenticatedActor,
        payload: PlayerCommand | HostCommand,
        *,
        expected_revision: int | None = None,
        command_id: UUID | None = None,
        now: datetime | None = None,
    ) -> CommandEnvelope:
        return make_envelope(
            actor,
            payload,
            expected_revision=expected_revision,
            command_id=command_id,
            now=now,
        )

    return build


@pytest.fixture
def submit() -> Callable[[Any, AuthenticatedActor, CommandEnvelope], CommandResult]:
    return submit_command


@pytest.fixture
def core() -> GameCore:
    return make_core()


@pytest.fixture
def core_with_two_joined_players() -> GameCore:
    return make_core_with_two_joined_players()


@pytest.fixture
def core_at_role_reveal() -> GameCore:
    return make_core_at_role_reveal()


@pytest.fixture
def core_at_wolf() -> Scenario:
    return make_core_at_wolf()


@pytest.fixture
def core_at_night() -> GameCore:
    return make_core_at_wolf().core


@pytest.fixture
def core_at_seer() -> tuple[Scenario, int]:
    return make_core_at_seer()


@pytest.fixture
def core_at_witch() -> tuple[Scenario, int]:
    return make_core_at_witch()


@pytest.fixture
def core_with_witch_as_target() -> Scenario:
    return make_core_with_witch_as_target()


@pytest.fixture
def core_after_antidote_used() -> Scenario:
    return make_core_after_antidote_used()


@pytest.fixture
def core_after_poison_used() -> Scenario:
    return make_core_after_poison_used()


@pytest.fixture
def core_with_one_living_wolf_and_dead_good() -> Scenario:
    return make_core_with_one_living_wolf_and_dead_good()


@pytest.fixture
def core_with_one_living_wolf_and_dead_seer() -> Scenario:
    return make_core_with_one_living_wolf_and_dead_seer()


def _finish_witch_night(scenario: Scenario) -> GameCore:
    seat = seat_actor(scenario.witch_id)
    result = scenario.core.submit(
        make_envelope(
            seat,
            WitchSkipCommand(),
            expected_revision=scenario.core.state.revision,
        ),
        seat,
    )
    assert result.accepted is True
    assert scenario.core.state.phase is Phase.DAY_DISCUSSION
    return scenario.core


@pytest.fixture
def core_at_day_discussion() -> GameCore:
    scenario, _ = make_core_at_witch()
    return _finish_witch_night(scenario)


@pytest.fixture
def core_at_day_discussion_with_dead_seat_one() -> GameCore:
    return _finish_witch_night(make_core_with_witch_as_target())


@pytest.fixture
def core_at_day_vote() -> GameCore:
    return make_core_at_day_vote()


@pytest.fixture
def core_at_pk_vote() -> GameCore:
    return make_core_at_pk_vote()


def _submit_waiting_player(
    core: GameCore,
    seat_id: int,
    payload: PlayerCommand,
) -> None:
    player = seat_actor(seat_id)
    result = core.submit(
        make_envelope(
            player,
            payload,
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        player,
    )
    assert result.accepted is True, result.error_code


def _living_seat(core: GameCore, role: Role) -> int:
    return next(
        player.seat_id for player in core.state.players if player.alive and player.role is role
    )


def _living_good_excluding(core: GameCore, *excluded_seat_ids: int) -> int:
    return next(
        player.seat_id
        for player in core.state.players
        if player.alive
        and player.role is not Role.WEREWOLF
        and player.seat_id not in excluded_seat_ids
    )


def _exile_player(core: GameCore, target_seat_id: int) -> None:
    vote_round = core.state.vote_round
    assert core.state.phase is Phase.DAY_VOTE
    assert vote_round is not None
    for voter_seat_id in vote_round.eligible_voter_ids:
        payload = (
            AbstainCommand()
            if voter_seat_id == target_seat_id
            else VoteCommand(target_seat_id=target_seat_id)
        )
        _submit_waiting_player(core, voter_seat_id, payload)


def _pass_remaining_discussion(core: GameCore) -> None:
    while core.state.phase is Phase.DAY_DISCUSSION:
        discussion = core.state.discussion
        assert discussion is not None
        assert discussion.current_seat_id is not None
        _submit_waiting_player(
            core,
            discussion.current_seat_id,
            PassSpeechCommand(),
        )


@pytest.fixture
def core_with_two_seer_checks() -> Scenario:
    scenario, _ = make_core_at_seer()
    core = scenario.core
    first_target = next(
        player.seat_id
        for player in core.state.players
        if player.alive and player.seat_id != scenario.seer_id
    )
    _submit_waiting_player(
        core,
        scenario.seer_id,
        SeerInspectCommand(target_seat_id=first_target),
    )
    _submit_waiting_player(core, scenario.witch_id, WitchSkipCommand())
    assert core.state.phase is Phase.DAY_DISCUSSION

    _pass_remaining_discussion(core)
    assert core.state.phase is Phase.DAY_VOTE
    assert core.state.vote_round is not None
    for voter_seat_id in core.state.vote_round.eligible_voter_ids:
        _submit_waiting_player(core, voter_seat_id, AbstainCommand())
    assert core.state.phase is Phase.NIGHT_WOLF

    second_kill_target = _living_good_excluding(core)
    for wolf in tuple(
        player.seat_id
        for player in core.state.players
        if player.alive and player.role is Role.WEREWOLF
    ):
        _submit_waiting_player(
            core,
            wolf,
            WolfNominateKillCommand(target_seat_id=second_kill_target),
        )
    assert core.state.phase is Phase.NIGHT_SEER

    second_seer_target = next(
        player.seat_id
        for player in core.state.players
        if player.alive and player.seat_id != scenario.seer_id
    )
    _submit_waiting_player(
        core,
        scenario.seer_id,
        SeerInspectCommand(target_seat_id=second_seer_target),
    )
    assert len(core.state.seer_checks) == 2
    return scenario


@pytest.fixture
def core_dead_witch() -> Scenario:
    scenario = make_core_with_witch_as_target()
    _submit_waiting_player(scenario.core, scenario.witch_id, WitchSkipCommand())
    assert scenario.core.state.phase is Phase.DAY_DISCUSSION
    assert scenario.core.state.players[scenario.witch_id - 1].alive is False
    return scenario


def _finish_night(
    core: GameCore,
    *,
    kill_target_seat_id: int,
    poison_target_seat_id: int | None = None,
) -> None:
    assert core.state.phase is Phase.NIGHT_WOLF
    for player in core.state.players:
        if player.alive and player.role is Role.WEREWOLF:
            _submit_waiting_player(
                core,
                player.seat_id,
                WolfNominateKillCommand(target_seat_id=kill_target_seat_id),
            )

    assert core.state.phase is Phase.NIGHT_SEER
    seer_seat_id = _living_seat(core, Role.SEER)
    seer_target = _living_good_excluding(core, seer_seat_id)
    _submit_waiting_player(
        core,
        seer_seat_id,
        SeerInspectCommand(target_seat_id=seer_target),
    )

    assert core.state.phase is Phase.NIGHT_WITCH
    witch_seat_id = _living_seat(core, Role.WITCH)
    payload = (
        WitchSkipCommand()
        if poison_target_seat_id is None
        else WitchUsePoisonCommand(target_seat_id=poison_target_seat_id)
    )
    _submit_waiting_player(core, witch_seat_id, payload)


def _core_after_day_one_good_exile() -> GameCore:
    core = make_core_at_day_vote()
    target = next(
        player.seat_id
        for player in core.state.players
        if player.alive and player.role is Role.VILLAGER
    )
    _exile_player(core, target)
    return core


def _core_last_wolf_dies() -> GameCore:
    core = make_core_at_day_vote()
    wolf_ids = tuple(
        player.seat_id
        for player in core.state.players
        if player.alive and player.role is Role.WEREWOLF
    )
    _exile_player(core, wolf_ids[-1])
    remaining_wolf = _living_seat(core, Role.WEREWOLF)
    kill_target = _living_good_excluding(core)
    _finish_night(
        core,
        kill_target_seat_id=kill_target,
        poison_target_seat_id=remaining_wolf,
    )
    return core


def _core_with_wolf_win(*, poison_good: bool) -> GameCore:
    core = _core_after_day_one_good_exile()
    kill_target = _living_good_excluding(core)
    poison_target = _living_good_excluding(core, kill_target) if poison_good else None
    _finish_night(
        core,
        kill_target_seat_id=kill_target,
        poison_target_seat_id=poison_target,
    )
    return core


def _core_exile_last_wolf() -> GameCore:
    core = make_core_at_day_vote()
    wolf_ids = tuple(
        player.seat_id
        for player in core.state.players
        if player.alive and player.role is Role.WEREWOLF
    )
    _exile_player(core, wolf_ids[-1])
    kill_target = _living_good_excluding(core)
    _finish_night(
        core,
        kill_target_seat_id=kill_target,
    )
    _pass_remaining_discussion(core)
    remaining_wolf = _living_seat(core, Role.WEREWOLF)
    _exile_player(core, remaining_wolf)
    return core


@pytest.fixture
def core_last_wolf_dies() -> GameCore:
    return _core_last_wolf_dies()


@pytest.fixture
def core_at_parity_after_night() -> GameCore:
    return _core_with_wolf_win(poison_good=False)


@pytest.fixture
def core_after_wolf_majority() -> GameCore:
    return _core_with_wolf_win(poison_good=True)


@pytest.fixture
def core_night_win() -> GameCore:
    return _core_with_wolf_win(poison_good=False)


@pytest.fixture
def core_game_end() -> GameCore:
    return _core_last_wolf_dies()


@pytest.fixture
def core_exile_last_wolf() -> GameCore:
    return _core_exile_last_wolf()
