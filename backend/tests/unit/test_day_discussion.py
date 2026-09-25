from datetime import timedelta

import pytest

from werewolf_dm.domain.contracts import (
    CommandErrorCode,
    PassSpeechCommand,
    SpeakCommand,
    WitchSkipCommand,
)
from werewolf_dm.domain.enums import EventType, Phase
from werewolf_dm.domain.state_machine import apply_command


def _tick_to_discussion_deadline(core):
    assert core.state.deadline_at is not None
    core.clock.set(core.state.deadline_at)
    core.tick()


def test_day_discussion_starts_at_seat_one_on_day_one(core_at_day_discussion):
    assert core_at_day_discussion.state.phase is Phase.DAY_DISCUSSION
    assert core_at_day_discussion.state.day == 1
    assert core_at_day_discussion.state.discussion is not None
    assert core_at_day_discussion.state.discussion.current_seat_id == 1


def test_speak_requires_current_speaker(core_at_day_discussion, actor, envelope):
    result = core_at_day_discussion.submit(
        envelope(
            actor(2),
            SpeakCommand(text="I speak first"),
            expected_revision=core_at_day_discussion.state.revision,
        ),
        actor(2),
    )

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.NOT_CURRENT_SPEAKER


def test_speak_records_public_event_and_advances(core_at_day_discussion, actor, envelope):
    result = core_at_day_discussion.submit(
        envelope(
            actor(1),
            SpeakCommand(text="I speak first"),
            expected_revision=core_at_day_discussion.state.revision,
        ),
        actor(1),
    )

    assert result.accepted is True
    event = next(
        event
        for event in reversed(core_at_day_discussion.events)
        if event.event_type is EventType.SPEECH_RECORDED
    )
    assert event.visibility.scope == "public"
    assert event.fact_payload == {"seat_id": 1, "text": "I speak first"}
    assert core_at_day_discussion.state.discussion is not None
    assert core_at_day_discussion.state.discussion.completed_seat_ids == (1,)
    assert core_at_day_discussion.state.discussion.current_seat_id == 2


def test_pass_speech_is_public_and_advances(core_at_day_discussion, actor, envelope):
    result = core_at_day_discussion.submit(
        envelope(
            actor(1),
            PassSpeechCommand(),
            expected_revision=core_at_day_discussion.state.revision,
        ),
        actor(1),
    )

    assert result.accepted is True
    event = next(
        event
        for event in reversed(core_at_day_discussion.events)
        if event.event_type is EventType.SPEECH_PASSED
    )
    assert event.visibility.scope == "public"
    assert event.fact_payload == {"seat_id": 1, "timed_out": False}
    assert core_at_day_discussion.state.discussion is not None
    assert core_at_day_discussion.state.discussion.completed_seat_ids == (1,)
    assert core_at_day_discussion.state.discussion.skipped_seat_ids == (1,)
    assert core_at_day_discussion.state.discussion.current_seat_id == 2


def test_st_019_speaker_timeout_is_public_skip(core_at_day_discussion):
    _tick_to_discussion_deadline(core_at_day_discussion)

    passed = next(
        event
        for event in reversed(core_at_day_discussion.events)
        if event.event_type is EventType.SPEECH_PASSED
    )
    assert passed.visibility.scope == "public"
    assert passed.fact_payload["seat_id"] == 1
    assert passed.fact_payload["timed_out"] is True
    assert core_at_day_discussion.state.discussion is not None
    assert core_at_day_discussion.state.discussion.skipped_seat_ids == (1,)
    assert core_at_day_discussion.state.discussion.current_seat_id == 2


def test_day_discussion_skips_dead_cursor_seat(
    core_at_day_discussion_with_dead_seat_one,
):
    state = core_at_day_discussion_with_dead_seat_one.state

    assert state.players[0].alive is False
    assert state.discussion is not None
    assert state.discussion.participant_seat_ids == (2, 3, 4, 5, 6)
    assert state.discussion.current_seat_id == 2


def test_all_living_players_complete_enters_day_vote(
    core_at_day_discussion,
    actor,
    envelope,
):
    discussion = core_at_day_discussion.state.discussion
    assert discussion is not None

    for seat_id in discussion.participant_seat_ids:
        assert core_at_day_discussion.state.discussion is not None
        assert core_at_day_discussion.state.discussion.current_seat_id == seat_id
        result = core_at_day_discussion.submit(
            envelope(
                actor(seat_id),
                PassSpeechCommand(),
                expected_revision=core_at_day_discussion.state.revision,
            ),
            actor(seat_id),
        )
        assert result.accepted is True

    assert core_at_day_discussion.state.phase is Phase.DAY_VOTE
    assert core_at_day_discussion.state.discussion is not None
    assert core_at_day_discussion.state.discussion.current_seat_id is None
    assert core_at_day_discussion.state.vote_round is not None
    assert core_at_day_discussion.state.vote_round.round_index == 1
    assert core_at_day_discussion.state.vote_round.phase is Phase.DAY_VOTE
    assert (
        core_at_day_discussion.state.vote_round.eligible_voter_ids
        == discussion.participant_seat_ids
    )
    assert core_at_day_discussion.state.vote_round.closed is False
    assert core_at_day_discussion.state.vote_round.deadline_at == (
        core_at_day_discussion.clock() + timedelta(seconds=30)
    )
    assert (
        core_at_day_discussion.state.deadline_at
        == core_at_day_discussion.state.vote_round.deadline_at
    )


@pytest.mark.parametrize(
    ("cursor", "next_speaker"),
    (
        (5, 6),
        (6, 1),
    ),
)
def test_later_day_discussion_starts_at_next_cursor(
    core_at_witch,
    actor,
    envelope,
    cursor,
    next_speaker,
):
    scenario, _ = core_at_witch
    state = scenario.core.state.model_copy(update={"day": 2, "next_discussion_cursor": cursor})
    witch = actor(scenario.witch_id)

    outcome = apply_command(
        state,
        envelope(
            witch,
            WitchSkipCommand(),
            expected_revision=state.revision,
        ),
        witch,
        scenario.core.clock(),
    )

    assert outcome.error_code is None
    assert outcome.next_state.discussion is not None
    assert outcome.next_state.discussion.current_seat_id == cursor

    speaker = actor(cursor)
    next_outcome = apply_command(
        outcome.next_state,
        envelope(
            speaker,
            PassSpeechCommand(),
            expected_revision=outcome.next_state.revision,
        ),
        speaker,
        scenario.core.clock(),
    )

    assert next_outcome.error_code is None
    assert next_outcome.next_state.discussion is not None
    assert next_outcome.next_state.discussion.current_seat_id == next_speaker


def test_dead_player_cannot_speak(
    core_at_day_discussion_with_dead_seat_one,
    actor,
    envelope,
):
    core = core_at_day_discussion_with_dead_seat_one
    revision = core.state.revision

    result = core.submit(
        envelope(
            actor(1),
            SpeakCommand(text="dead player speech"),
            expected_revision=revision,
        ),
        actor(1),
    )

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.PLAYER_DEAD
    assert core.state.revision == revision
