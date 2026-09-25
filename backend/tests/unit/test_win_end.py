from tests.factories import submit_votes
from werewolf_dm.domain.contracts import (
    CommandErrorCode,
    HostPauseCommand,
    HostResumeCommand,
    SetReadyCommand,
)
from werewolf_dm.domain.enums import EventType, Faction, Phase
from werewolf_dm.domain.model import NightState, WolfDecision


def test_win_001_last_wolf_death_gives_good_win(core_last_wolf_dies):
    assert core_last_wolf_dies.state.phase is Phase.GAME_END
    assert core_last_wolf_dies.state.winner is Faction.GOOD


def test_win_002_wolf_parity_gives_wolf_win(core_at_parity_after_night):
    assert core_at_parity_after_night.state.phase is Phase.GAME_END
    assert core_at_parity_after_night.state.winner is Faction.WEREWOLF


def test_win_003_wolf_majority_gives_wolf_win(core_after_wolf_majority):
    assert core_after_wolf_majority.state.phase is Phase.GAME_END
    assert core_after_wolf_majority.state.winner is Faction.WEREWOLF


def test_win_004_night_death_announcement_precedes_game_end(core_night_win):
    outbox = core_night_win.state.outbox
    assert [item.kind for item in outbox[-2:]] == ["dm.message", "game.ended"]
    assert outbox[-2].seq < outbox[-1].seq
    announcement_index = max(
        index
        for index, event in enumerate(core_night_win.events)
        if event.event_type is EventType.PLAYERS_DIED
    )
    game_end_index = next(
        index
        for index, event in enumerate(core_night_win.events)
        if event.event_type is EventType.GAME_ENDED
    )
    assert announcement_index < game_end_index


def test_win_005_terminal_rejects_state_change(core_game_end, actor, host, envelope):
    revision = core_game_end.state.revision
    outbox = core_game_end.state.outbox
    result = core_game_end.submit(
        envelope(
            actor(1),
            SetReadyCommand(ready=True),
            expected_revision=revision,
        ),
        actor(1),
    )
    assert result.error_code is CommandErrorCode.GAME_ENDED

    for payload in (
        HostPauseCommand(reason="terminal test"),
        HostResumeCommand(),
    ):
        result = core_game_end.submit(
            envelope(
                host(),
                payload,
                expected_revision=revision,
            ),
            host(),
        )
        assert result.error_code is CommandErrorCode.GAME_ENDED

    assert core_game_end.state.revision == revision
    assert core_game_end.state.outbox == outbox
    assert core_game_end.reconnect(actor(1)).revision == revision


def test_win_006_exile_announcement_precedes_game_end(core_exile_last_wolf):
    outbox = core_exile_last_wolf.state.outbox
    assert [item.kind for item in outbox[-2:]] == ["dm.message", "game.ended"]
    assert outbox[-2].seq < outbox[-1].seq
    announcement_index = max(
        index
        for index, event in enumerate(core_exile_last_wolf.events)
        if event.event_type is EventType.PLAYER_EXILED
    )
    game_end_index = next(
        index
        for index, event in enumerate(core_exile_last_wolf.events)
        if event.event_type is EventType.GAME_ENDED
    )
    assert announcement_index < game_end_index


def test_win_check_runs_exactly_once_after_night(core_at_day_discussion):
    assert core_at_day_discussion.state.day == 1
    win_checks = [
        event
        for event in core_at_day_discussion.events
        if event.event_type is EventType.PHASE_CHANGED
        and event.fact_payload["next_phase"] == Phase.WIN_CHECK.value
    ]
    assert len(win_checks) == 1


def test_no_winner_day_advances_to_fresh_night(core_at_day_vote):
    previous_day = core_at_day_vote.state.day
    submit_votes(core_at_day_vote, {seat_id: None for seat_id in range(1, 7)})

    assert core_at_day_vote.state.phase is Phase.NIGHT_WOLF
    assert core_at_day_vote.state.day == previous_day + 1
    vote_resolved_index = max(
        index
        for index, event in enumerate(core_at_day_vote.events)
        if event.event_type is EventType.VOTE_ROUND_RESOLVED
    )
    win_checks = [
        event
        for event in core_at_day_vote.events[vote_resolved_index:]
        if event.event_type is EventType.PHASE_CHANGED
        and event.fact_payload["next_phase"] == Phase.WIN_CHECK.value
    ]
    assert len(win_checks) == 1
    assert core_at_day_vote.state.discussion is None
    assert core_at_day_vote.state.wolf_decision == WolfDecision()
    assert core_at_day_vote.state.night == NightState()
    assert core_at_day_vote.state.deadline_at is not None
