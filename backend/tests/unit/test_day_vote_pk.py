from tests.factories import (
    envelope_for,
    other_candidate,
    submit_pk_votes,
    submit_votes,
)
from werewolf_dm.domain.contracts import (
    CommandErrorCode,
    PassSpeechCommand,
    VoteCommand,
)
from werewolf_dm.domain.enums import EventType, Phase


def _latest_event(core, event_type):
    return next(event for event in reversed(core.events) if event.event_type is event_type)


def _tick_to_deadline(core) -> None:
    assert core.state.deadline_at is not None
    core.clock.set(core.state.deadline_at)
    core.tick()


def test_vote_001_unique_highest_is_exiled(core_at_day_vote):
    core = core_at_day_vote
    submit_votes(core, {1: 3, 2: 3, 3: 1, 4: 3, 5: 1, 6: None})

    assert core.state.players[2].alive is False
    assert core.state.next_discussion_cursor == 3
    assert core.state.phase is Phase.NIGHT_WOLF
    assert core.state.vote_round is not None
    assert core.state.vote_round.closed is True
    assert _latest_event(core, EventType.PLAYER_EXILED).fact_payload["seat_id"] == 3


def test_vote_002_all_abstain_no_exile(core_at_day_vote):
    core = core_at_day_vote
    submit_votes(core, {1: None, 2: None, 3: None, 4: None, 5: None, 6: None})

    assert core.state.phase is Phase.NIGHT_WOLF
    assert _latest_event(core, EventType.NO_EXILE).fact_payload["reason"] == "NO_VOTES"


def test_vote_003_two_way_tie_enters_pk(core_at_day_vote):
    core = core_at_day_vote
    submit_votes(core, {1: 3, 2: 3, 3: 2, 4: 2, 5: None, 6: None})

    assert core.state.phase is Phase.DAY_PK_DISCUSSION
    assert core.state.vote_round is not None
    assert core.state.vote_round.candidate_seat_ids == (2, 3)
    assert core.state.discussion is not None
    assert core.state.discussion.participant_seat_ids == (2, 3)
    assert core.state.discussion.current_seat_id == 2


def test_vote_004_three_way_tie_enters_pk(core_at_day_vote):
    core = core_at_day_vote
    submit_votes(core, {1: 2, 2: 3, 3: 4, 4: 2, 5: 3, 6: 4})

    assert core.state.phase is Phase.DAY_PK_DISCUSSION
    assert core.state.vote_round is not None
    assert core.state.vote_round.candidate_seat_ids == (2, 3, 4)


def test_vote_005_pk_candidate_cannot_vote(core_at_pk_vote, actor):
    core = core_at_pk_vote
    vote_round = core.state.vote_round
    assert vote_round is not None
    candidate = vote_round.candidate_seat_ids[0]
    revision = core.state.revision

    result = core.submit(
        envelope_for(
            core,
            candidate,
            VoteCommand(target_seat_id=other_candidate(core, candidate)),
        ),
        actor(candidate),
    )

    assert result.error_code is CommandErrorCode.ACTOR_NOT_AUTHORIZED
    assert core.state.revision == revision
    assert core.state.vote_round == vote_round


def test_vote_006_pk_unique_highest_exiled(core_at_pk_vote):
    core = core_at_pk_vote
    submit_pk_votes(core, {1: 2, 4: 2, 5: 3, 6: None})

    assert core.state.players[1].alive is False
    assert core.state.next_discussion_cursor == 2
    assert _latest_event(core, EventType.PLAYER_EXILED).fact_payload["seat_id"] == 2


def test_vote_007_second_tie_has_no_exile(core_at_pk_vote):
    submit_pk_votes(core_at_pk_vote, {1: 2, 4: 3, 5: None, 6: None})

    assert core_at_pk_vote.state.phase is Phase.NIGHT_WOLF
    assert _latest_event(core_at_pk_vote, EventType.NO_EXILE).fact_payload["reason"] == "PK_TIE"


def test_vote_008_pk_no_valid_vote_no_exile(core_at_pk_vote):
    submit_pk_votes(core_at_pk_vote, {1: None, 4: None, 5: None, 6: None})

    assert core_at_pk_vote.state.phase is Phase.NIGHT_WOLF
    assert core_at_pk_vote.state.next_discussion_cursor == 1
    assert (
        _latest_event(core_at_pk_vote, EventType.NO_EXILE).fact_payload["reason"]
        == "NO_VALID_PK_VOTES"
    )


def test_vote_009_self_vote_rejected(core_at_day_vote, actor):
    core = core_at_day_vote
    revision = core.state.revision

    result = core.submit(
        envelope_for(core, 1, VoteCommand(target_seat_id=1)),
        actor(1),
    )

    assert result.error_code is CommandErrorCode.INVALID_TARGET
    assert core.state.revision == revision
    assert core.state.vote_round is not None
    assert core.state.vote_round.votes == ()


def test_vote_010_revote_replaces_previous_vote(core_at_day_vote, actor):
    core = core_at_day_vote

    first = core.submit(envelope_for(core, 1, VoteCommand(target_seat_id=2)), actor(1))
    second = core.submit(envelope_for(core, 1, VoteCommand(target_seat_id=3)), actor(1))

    assert first.accepted is True
    assert second.accepted is True
    assert core.state.vote_round is not None
    assert [(vote.voter_seat_id, vote.target_seat_id) for vote in core.state.vote_round.votes] == [
        (1, 3)
    ]


def test_vote_011_post_settlement_vote_rejected(core_at_day_vote, actor):
    core = core_at_day_vote
    submit_votes(core, {1: 3, 2: 3, 3: 1, 4: 3, 5: 1, 6: None})
    settled_round = core.state.vote_round
    assert settled_round is not None

    result = core.submit(
        envelope_for(core, 1, VoteCommand(target_seat_id=2)),
        actor(1),
    )

    assert result.error_code is CommandErrorCode.VOTE_ROUND_CLOSED
    assert core.state.vote_round == settled_round


def test_vote_012_discussion_cursor_carry_forward(core_at_day_vote):
    core = core_at_day_vote
    assert core.state.discussion is not None
    first_speaker = core.state.discussion.participant_seat_ids[0]

    submit_votes(core, {seat_id: None for seat_id in range(1, 7)})

    assert core.state.phase is Phase.NIGHT_WOLF
    assert core.state.next_discussion_cursor == first_speaker


def test_st_020_normal_vote_timeout_inserts_abstentions(core_at_day_vote):
    core = core_at_day_vote
    submit_votes(core, {1: 2})
    eligible_voter_ids = core.state.vote_round.eligible_voter_ids

    _tick_to_deadline(core)

    assert _latest_event(core, EventType.TIMEOUT_APPLIED).fact_payload == {
        "phase": "DAY_VOTE",
        "timeout_reason": "VOTE",
    }
    assert core.state.vote_round is not None
    assert len(core.state.vote_round.votes) == len(eligible_voter_ids)
    assert {
        vote.voter_seat_id for vote in core.state.vote_round.votes if vote.target_seat_id is None
    } == {2, 3, 4, 5, 6}


def test_st_021_pk_vote_timeout_inserts_abstentions(core_at_pk_vote):
    core = core_at_pk_vote
    assert core.state.vote_round is not None
    eligible_voter_ids = core.state.vote_round.eligible_voter_ids
    candidate_seat_id = core.state.vote_round.candidate_seat_ids[0]
    first_voter = eligible_voter_ids[0]
    submit_pk_votes(core, {first_voter: candidate_seat_id})

    _tick_to_deadline(core)

    assert _latest_event(core, EventType.TIMEOUT_APPLIED).fact_payload == {
        "phase": "DAY_PK_VOTE",
        "timeout_reason": "PK_VOTE",
    }
    assert core.state.vote_round is not None
    assert len(core.state.vote_round.votes) == len(eligible_voter_ids)
    assert {
        vote.voter_seat_id for vote in core.state.vote_round.votes if vote.target_seat_id is None
    } == set(eligible_voter_ids[1:])


def test_pk_discussion_only_candidates_speak_and_timeout_advances(
    core_at_day_vote,
    actor,
):
    core = core_at_day_vote
    submit_votes(core, {1: 3, 2: 3, 3: 2, 4: 2, 5: None, 6: None})
    assert core.state.vote_round is not None
    candidates = core.state.vote_round.candidate_seat_ids
    non_candidate = next(seat_id for seat_id in range(1, 7) if seat_id not in candidates)

    rejected = core.submit(
        envelope_for(core, non_candidate, PassSpeechCommand()),
        actor(non_candidate),
    )
    assert rejected.error_code is CommandErrorCode.ACTOR_NOT_AUTHORIZED

    first_candidate = candidates[0]
    accepted = core.submit(
        envelope_for(core, first_candidate, PassSpeechCommand()),
        actor(first_candidate),
    )
    assert accepted.accepted is True
    assert core.state.discussion is not None
    assert core.state.discussion.current_seat_id == candidates[1]

    _tick_to_deadline(core)

    assert core.state.phase is Phase.DAY_PK_VOTE
    assert _latest_event(core, EventType.SPEECH_PASSED).fact_payload == {
        "seat_id": candidates[1],
        "timed_out": True,
    }
