import asyncio
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from tests.factories import (
    ROOM_ID,
    START_TIME,
    core_at_day_vote,
    core_at_role_reveal,
    core_at_witch,
    core_at_wolf,
    host_actor,
    make_envelope,
    seat_actor,
)
from tests.factories import (
    core as make_core,
)
from werewolf_dm.application.core import (
    CoreMutation,
    DomainSeqAllocator,
    GameCore,
    PersistenceCoordinator,
)
from werewolf_dm.application.dm_contracts import DMAnnouncementSlot
from werewolf_dm.application.host_recovery import HostRecoveryService
from werewolf_dm.application.rooms import (
    HostControlUpdate,
    PublicViewUpdate,
    RoomActor,
    TimerTickEvent,
)
from werewolf_dm.domain import state_machine
from werewolf_dm.domain.contracts import (
    AuthenticatedActor,
    CommandErrorCode,
    CommandResult,
    ConfirmRoleCommand,
    HostForceTemplateCommand,
    HostPatch,
    HostPatchCommand,
    HostPauseCommand,
    HostRewindToSnapshotCommand,
    HostTemplateForcedAudit,
    SetAlivePatch,
    SetPhasePatch,
    SetPotionPatch,
    SetRolePatch,
    SetSeerChecksPatch,
    SetVotePatch,
    SnapshotReason,
    VoteCommand,
)
from werewolf_dm.domain.enums import EventType, Faction, Phase, Role
from werewolf_dm.domain.model import NightState, PrivateFact, SeerCheckRecord
from werewolf_dm.domain.replay import event_log_digest
from werewolf_dm.domain.state_machine import ApplyOutcome, apply_command, winner_for
from werewolf_dm.domain.visibility import project_public_view, project_seat_view
from werewolf_dm.infrastructure.persistence import (
    PersistedRoom,
    PersistedRoomRuntime,
    PersistedSnapshot,
    PersistenceConflictError,
    SQLiteRoomStore,
)

_SECOND_ROOM_ID = UUID("00000000-0000-0000-0000-000000000002")


def _runtime(
    *,
    outbox_seq: int = 0,
    next_domain_seq: int = 1,
    recovery_epoch: int = 0,
    domain_to_transport: dict[int, int] | None = None,
    completed_domain_seqs: tuple[int, ...] = (),
    processed_announcement_seq: int = 0,
    published_message_ids: tuple[UUID, ...] = (),
) -> PersistedRoomRuntime:
    return PersistedRoomRuntime(
        outbox_seq=outbox_seq,
        domain_to_transport={} if domain_to_transport is None else domain_to_transport,
        completed_domain_seqs=completed_domain_seqs,
        processed_announcement_seq=processed_announcement_seq,
        published_message_ids=published_message_ids,
        next_domain_seq=next_domain_seq,
        recovery_epoch=recovery_epoch,
        discarded_command_tombstones=(),
    )


def _persist_core(
    tmp_path: Path,
    core: GameCore,
    *,
    runtime: PersistedRoomRuntime | None = None,
) -> SQLiteRoomStore:
    store = SQLiteRoomStore(tmp_path / "rooms.sqlite3")
    store.migrate()
    store.save_room(
        PersistedRoom(
            room_code="REC001",
            room_id=ROOM_ID,
            seed=core.state.seed,
            rulepack_version="1.1.0",
            expires_at=START_TIME + timedelta(hours=1),
            last_activity_at=START_TIME,
        )
    )
    store.save_core(ROOM_ID, core.state, core.events)
    store.save_room_runtime(ROOM_ID, runtime or _runtime())
    return store


def _paused_store_and_service(
    tmp_path: Path,
    *,
    runtime: PersistedRoomRuntime | None = None,
) -> tuple[SQLiteRoomStore, HostRecoveryService, GameCore]:
    core = _pause(core_at_role_reveal())
    store = _persist_core(tmp_path, core, runtime=runtime)
    return store, HostRecoveryService(store), core


@dataclass(slots=True)
class _RecordingSubscriber:
    actor_type: str = "host"
    seat_id: int | None = None
    session_id: UUID | None = None
    channels: frozenset[str] = frozenset({"public", "host.control"})
    subscription_id: UUID = field(default_factory=uuid4)
    messages: list[object] = field(default_factory=list)
    close_code: int | None = None

    def offer(self, message: object) -> bool:
        self.messages.append(message)
        return True

    def request_close(self, code: int) -> None:
        self.close_code = code


def _actor(
    store: SQLiteRoomStore,
    core: GameCore,
    *,
    runtime: PersistedRoomRuntime | None = None,
) -> RoomActor:
    return RoomActor(
        room_id=ROOM_ID,
        room_code="REC001",
        seed=core.state.seed,
        clock=core.clock,
        expires_at=START_TIME + timedelta(hours=1),
        last_activity_at=START_TIME,
        core=core,
        coordinator=PersistenceCoordinator(store),
        runtime=runtime or _runtime(),
    )


def _pause(core: GameCore) -> GameCore:
    actor = host_actor()
    result = core.submit(
        make_envelope(
            actor,
            HostPauseCommand(reason="host recovery test"),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        actor,
    )
    assert result.accepted is True
    assert core.state.paused is True
    return core


def _patch_outcome(
    core: GameCore,
    patch: HostPatch,
    *,
    actor: AuthenticatedActor | None = None,
) -> ApplyOutcome:
    authenticated = host_actor() if actor is None else actor
    envelope = make_envelope(
        authenticated,
        HostPatchCommand(patch=patch),
        expected_revision=core.state.revision,
        now=core.clock(),
    )
    return apply_command(core.state, envelope, authenticated, core.clock())


def _living_other(core: GameCore, seat_id: int) -> int:
    return next(
        player.seat_id
        for player in core.state.players
        if player.alive and player.seat_id != seat_id
    )


def fact_payload(fact: PrivateFact) -> dict[str, object]:
    return {key: value for key, value in fact.payload.items()}


def test_apply_host_patch_entrypoint_exists() -> None:
    assert callable(getattr(state_machine, "apply_host_patch", None))


def test_host_patch_requires_paused_state() -> None:
    core = core_at_role_reveal()
    before = core.state

    outcome = _patch_outcome(
        core,
        SetPotionPatch(antidote_available=False, poison_available=False),
    )

    assert outcome.error_code is CommandErrorCode.ILLEGAL_PHASE
    assert outcome.next_state == before
    assert outcome.events == ()


def test_host_patch_requires_host_actor() -> None:
    core = _pause(core_at_role_reveal())
    before = core.state

    outcome = _patch_outcome(
        core,
        SetPotionPatch(antidote_available=False, poison_available=False),
        actor=seat_actor(1),
    )

    assert outcome.error_code is CommandErrorCode.ACTOR_NOT_AUTHORIZED
    assert outcome.next_state == before
    assert outcome.events == ()


def test_set_alive_patch_changes_only_target_and_increments_revision() -> None:
    core = _pause(core_at_role_reveal())
    before = core.state
    target = before.players[0]

    outcome = _patch_outcome(
        core,
        SetAlivePatch(seat_id=target.seat_id, alive=not target.alive),
    )

    assert outcome.error_code is None
    assert outcome.next_state.revision == before.revision + 1
    assert tuple(event.event_type for event in outcome.events) == (
        EventType.HOST_CORRECTION_APPLIED,
        EventType.HOST_COMPENSATION_APPLIED,
    )
    expected_players = tuple(
        player.model_copy(update={"alive": not player.alive})
        if player.seat_id == target.seat_id
        else player
        for player in before.players
    )
    assert outcome.next_state.players == expected_players
    assert outcome.next_state.outbox == before.outbox
    assert (
        outcome.next_state.model_copy(
            update={
                "revision": before.revision,
                "players": before.players,
                "event_count": before.event_count,
                "event_log_digest": before.event_log_digest,
            }
        )
        == before
    )


def test_invalid_patch_is_rejected_without_state_mutation() -> None:
    core = _pause(make_core())
    before = core.state

    outcome = _patch_outcome(
        core,
        SetRolePatch(seat_id=1, role=Role.WEREWOLF),
    )

    assert outcome.error_code is CommandErrorCode.INVALID_TARGET
    assert outcome.next_state == before
    assert outcome.events == ()


def test_set_role_patch_swaps_roles_without_changing_rulepack_counts() -> None:
    core = _pause(core_at_role_reveal())
    before = core.state
    target = before.players[0]
    assert target.role is not None
    desired_role = next(role for role in Role if role is not target.role)
    other = next(
        player
        for player in before.players
        if player.seat_id != target.seat_id and player.role is desired_role
    )

    outcome = _patch_outcome(
        core,
        SetRolePatch(seat_id=target.seat_id, role=desired_role),
    )

    assert outcome.error_code is None
    updated_target = next(
        player for player in outcome.next_state.players if player.seat_id == target.seat_id
    )
    updated_other = next(
        player for player in outcome.next_state.players if player.seat_id == other.seat_id
    )
    assert updated_target.role is desired_role
    assert updated_other.role is target.role
    assert sorted(player.role for player in outcome.next_state.players) == sorted(
        player.role for player in before.players
    )
    assert outcome.next_state.revision == before.revision + 1


def test_set_phase_patch_cannot_jump_to_future_phase() -> None:
    core = _pause(core_at_role_reveal())
    before = core.state

    outcome = _patch_outcome(core, SetPhasePatch(phase=Phase.NIGHT_WOLF))

    assert outcome.error_code is CommandErrorCode.ILLEGAL_PHASE
    assert outcome.next_state == before
    assert outcome.events == ()


def test_set_role_rejects_terminal_game() -> None:
    core = core_at_role_reveal()
    terminal = core.state.model_copy(
        update={
            "phase": Phase.GAME_END,
            "winner": Faction.GOOD,
            "paused": True,
            "paused_at": core.clock(),
        }
    )
    target = terminal.players[0]
    assert target.role is not None

    actor = host_actor()
    envelope = make_envelope(
        actor,
        HostPatchCommand(patch=SetRolePatch(seat_id=target.seat_id, role=target.role)),
        expected_revision=terminal.revision,
        now=core.clock(),
    )
    outcome = apply_command(terminal, envelope, actor, core.clock())

    assert outcome.error_code is CommandErrorCode.GAME_ENDED
    assert outcome.next_state == terminal
    assert outcome.events == ()


def test_set_potion_updates_only_potion_state() -> None:
    core = _pause(core_at_role_reveal())
    before = core.state
    history_before = core.events
    actor = host_actor()

    result = core.submit(
        make_envelope(
            actor,
            HostPatchCommand(
                patch=SetPotionPatch(
                    antidote_available=False,
                    poison_available=False,
                )
            ),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        actor,
    )

    assert result.accepted is True
    assert core.state.revision == before.revision + 1
    assert core.state.potions.antidote_available is False
    assert core.state.potions.poison_available is False
    assert (
        core.state.model_copy(
            update={
                "revision": before.revision,
                "potions": before.potions,
                "event_count": before.event_count,
                "event_log_digest": before.event_log_digest,
            }
        )
        == before
    )
    assert core.events[: len(history_before)] == history_before


def test_set_vote_requires_matching_open_round_and_legal_target() -> None:
    core = _pause(core_at_day_vote())
    assert core.state.vote_round is not None
    round_ = core.state.vote_round
    voter_seat_id = round_.eligible_voter_ids[0]
    target_seat_id = _living_other(core, voter_seat_id)

    wrong_round = _patch_outcome(
        core,
        SetVotePatch(
            voter_seat_id=voter_seat_id,
            round_id=uuid4(),
            target_seat_id=target_seat_id,
        ),
    )
    assert wrong_round.error_code is not None
    assert wrong_round.next_state == core.state

    actor = host_actor()
    result = core.submit(
        make_envelope(
            actor,
            HostPatchCommand(
                patch=SetVotePatch(
                    voter_seat_id=voter_seat_id,
                    round_id=round_.round_id,
                    target_seat_id=target_seat_id,
                )
            ),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        actor,
    )

    assert result.accepted is True
    assert core.state.vote_round is not None
    vote = next(vote for vote in core.state.vote_round.votes if vote.voter_seat_id == voter_seat_id)
    assert vote.target_seat_id == target_seat_id


def test_set_vote_rejects_self_vote_unconditionally() -> None:
    core = _pause(core_at_day_vote())
    assert core.state.vote_round is not None
    round_ = core.state.vote_round
    voter_seat_id = round_.eligible_voter_ids[0]
    before = core.state

    outcome = _patch_outcome(
        core,
        SetVotePatch(
            voter_seat_id=voter_seat_id,
            round_id=round_.round_id,
            target_seat_id=voter_seat_id,
        ),
    )

    assert outcome.error_code is CommandErrorCode.INVALID_TARGET
    assert outcome.next_state == before
    assert outcome.events == ()


def test_set_seer_checks_accepts_canonical_history() -> None:
    core = _pause(core_at_day_vote())
    before = core.state
    seer = next(player for player in before.players if player.role is Role.SEER)

    outcome = _patch_outcome(
        core,
        SetSeerChecksPatch(
            seer_seat_id=seer.seat_id,
            checks=list(before.seer_checks),
        ),
    )

    assert outcome.error_code is None
    assert outcome.next_state.seer_checks == before.seer_checks
    assert outcome.next_state.revision == before.revision + 1


def test_set_seer_checks_rejects_non_seer_duplicate_and_future_history() -> None:
    core = _pause(core_at_day_vote())
    before = core.state
    seer = next(player for player in before.players if player.role is Role.SEER)
    non_seer = next(player for player in before.players if player.role is not Role.SEER)
    assert before.seer_checks
    existing = before.seer_checks[0]

    wrong_seat = _patch_outcome(
        core,
        SetSeerChecksPatch(
            seer_seat_id=non_seer.seat_id,
            checks=list(before.seer_checks),
        ),
    )
    duplicate = _patch_outcome(
        core,
        SetSeerChecksPatch(
            seer_seat_id=seer.seat_id,
            checks=[existing, existing],
        ),
    )
    future = _patch_outcome(
        core,
        SetSeerChecksPatch(
            seer_seat_id=seer.seat_id,
            checks=[
                SeerCheckRecord(
                    day=before.day + 1,
                    target_seat_id=existing.target_seat_id,
                    faction=existing.faction,
                )
            ],
        ),
    )

    for outcome in (wrong_seat, duplicate, future):
        assert outcome.error_code is not None
        assert outcome.next_state == before
        assert outcome.events == ()


def test_set_seer_checks_rebuilds_seat_projection_history() -> None:
    core = _pause(core_at_day_vote())
    before = core.state
    seer = next(player for player in before.players if player.role is Role.SEER)
    previous = before.seer_checks[0]
    target = next(
        player
        for player in before.players
        if player.alive and player.role is not None and player.seat_id != seer.seat_id
    )
    corrected = SeerCheckRecord(
        day=before.day,
        target_seat_id=target.seat_id,
        faction=Faction.WEREWOLF if target.role is Role.WEREWOLF else Faction.GOOD,
    )
    actor = host_actor()

    result = core.submit(
        make_envelope(
            actor,
            HostPatchCommand(
                patch=SetSeerChecksPatch(
                    seer_seat_id=seer.seat_id,
                    checks=[corrected],
                )
            ),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        actor,
    )

    assert result.accepted is True
    view = project_seat_view(core.state, seer.seat_id, seat_actor(seer.seat_id))
    facts = tuple(fact for fact in view.private_facts if fact.fact_type == "SEER_CHECK")
    assert len(facts) == 1
    assert fact_payload(facts[0]) == {
        "day": corrected.day,
        "target_seat_id": corrected.target_seat_id,
        "faction": corrected.faction.value,
    }
    assert fact_payload(facts[0])["target_seat_id"] != previous.target_seat_id
    assert facts[0].revision == core.state.revision


def test_set_phase_night_wolf_rebuilds_current_wolf_decision_facts() -> None:
    core = _pause(core_at_wolf().core)
    actor = host_actor()

    result = core.submit(
        make_envelope(
            actor,
            HostPatchCommand(patch=SetPhasePatch(phase=Phase.NIGHT_WOLF)),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        actor,
    )

    assert result.accepted is True
    wolf_ids = tuple(
        player.seat_id
        for player in core.state.players
        if player.alive and player.role is Role.WEREWOLF
    )
    for wolf_id in wolf_ids:
        view = project_seat_view(core.state, wolf_id, seat_actor(wolf_id))
        facts = tuple(fact for fact in view.private_facts if fact.fact_type == "WOLF_DECISION")
        assert len(facts) == 1
        assert fact_payload(facts[0]) == {
            "nominations": (),
            "target_seat_id": None,
            "locked": False,
        }
        assert facts[0].revision == core.state.revision


def test_set_phase_night_witch_rebuilds_current_kill_target_fact() -> None:
    scenario, previous_target = core_at_witch()
    core = _pause(scenario.core)
    current_target = next(
        player.seat_id
        for player in core.state.players
        if player.alive
        and player.seat_id != scenario.witch_id
        and player.seat_id != previous_target
    )
    state = core.state.model_copy(
        update={
            "wolf_decision": core.state.wolf_decision.model_copy(
                update={"target_seat_id": current_target}
            )
        }
    )
    actor = host_actor()
    envelope = make_envelope(
        actor,
        HostPatchCommand(patch=SetPhasePatch(phase=Phase.NIGHT_WITCH)),
        expected_revision=state.revision,
        now=core.clock(),
    )

    outcome = apply_command(state, envelope, actor, core.clock())

    assert outcome.error_code is None
    view = project_seat_view(
        outcome.next_state,
        scenario.witch_id,
        seat_actor(scenario.witch_id),
    )
    facts = tuple(fact for fact in view.private_facts if fact.fact_type == "WITCH_KILL_TARGET")
    assert len(facts) == 1
    assert fact_payload(facts[0]) == {"target_seat_id": current_target}
    assert facts[0].revision == outcome.next_state.revision


def test_set_alive_rejects_candidate_that_creates_winner_condition() -> None:
    core = _pause(core_at_day_vote())
    good_seen = 0
    players = []
    for player in core.state.players:
        if player.role is Role.WEREWOLF:
            players.append(player.model_copy(update={"alive": True}))
            continue
        good_seen += 1
        players.append(player.model_copy(update={"alive": good_seen <= 3}))
    state = core.state.model_copy(
        update={
            "phase": Phase.DAY_DISCUSSION,
            "players": tuple(players),
            "discussion": None,
            "vote_round": None,
            "deadline_at": None,
        }
    )
    target = next(
        player for player in state.players if player.alive and player.role is not Role.WEREWOLF
    )
    assert winner_for(state) is None
    actor = host_actor()
    envelope = make_envelope(
        actor,
        HostPatchCommand(patch=SetAlivePatch(seat_id=target.seat_id, alive=False)),
        expected_revision=state.revision,
        now=core.clock(),
    )

    outcome = apply_command(state, envelope, actor, core.clock())

    assert outcome.error_code is CommandErrorCode.INVALID_TARGET
    assert outcome.next_state == state
    assert outcome.events == ()


def test_host_patch_authorization_rejection_is_not_dedupe_cached() -> None:
    core = _pause(core_at_role_reveal())
    actor = seat_actor(1)
    before_cache = dict(core.command_dedupe_cache)
    envelope = make_envelope(
        actor,
        HostPatchCommand(
            patch=SetPotionPatch(
                antidote_available=False,
                poison_available=False,
            )
        ),
        expected_revision=core.state.revision,
        now=core.clock(),
    )

    mutation = core.stage_submit(
        envelope,
        actor,
        next_domain_seq=DomainSeqAllocator(1),
    )

    assert mutation.command_result.error_code is CommandErrorCode.ACTOR_NOT_AUTHORIZED
    assert mutation.dedupe_key is not None
    assert mutation.cache_result is False
    core.commit(mutation)
    assert core.command_dedupe_cache == before_cache


def test_set_phase_resets_discussion_vote_and_night_substate_for_target_phase() -> None:
    core = core_at_day_vote()
    assert core.state.vote_round is not None
    round_ = core.state.vote_round
    voter_seat_id = round_.eligible_voter_ids[0]
    target_seat_id = _living_other(core, voter_seat_id)
    voter = seat_actor(voter_seat_id)
    vote_result = core.submit(
        make_envelope(
            voter,
            VoteCommand(target_seat_id=target_seat_id),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        voter,
    )
    assert vote_result.accepted is True
    assert core.state.vote_round is not None
    assert core.state.vote_round.votes
    _pause(core)
    before_round_id = core.state.vote_round.round_id
    now = core.clock()

    outcome = _patch_outcome(core, SetPhasePatch(phase=Phase.DAY_VOTE))

    assert outcome.error_code is None
    assert outcome.next_state.phase is Phase.DAY_VOTE
    assert outcome.next_state.discussion is None
    assert outcome.next_state.night == NightState()
    assert outcome.next_state.vote_round is not None
    assert outcome.next_state.vote_round.round_id != before_round_id
    assert outcome.next_state.vote_round.votes == ()
    assert outcome.next_state.vote_round.opened_at == now
    assert outcome.next_state.vote_round.deadline_at == now + timedelta(seconds=30)
    assert outcome.next_state.deadline_at == now + timedelta(seconds=30)


def test_host_patch_events_never_enter_public_or_seat_projections() -> None:
    core = _pause(core_at_role_reveal())
    actor = host_actor()

    result = core.submit(
        make_envelope(
            actor,
            HostPatchCommand(
                patch=SetPotionPatch(
                    antidote_available=False,
                    poison_available=True,
                )
            ),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        actor,
    )

    assert result.accepted is True
    assert all(event.visibility.scope == "host" for event in core.events[-2:])
    public = project_public_view(core.state).model_dump_json()
    seat = project_seat_view(core.state, 1, seat_actor(1)).model_dump_json()
    for serialized in (public, seat):
        for forbidden in (
            "HOST_COMPENSATION_APPLIED",
            "snapshot_id",
            "diff",
            "fact_payload",
            "token",
        ):
            assert forbidden not in serialized


def test_domain_seq_allocator_reserves_contiguous_ranges_and_rolls_back() -> None:
    allocator = DomainSeqAllocator(7)

    first = allocator.reserve(2)
    second = allocator.reserve(3)

    assert (first.start, first.count) == (7, 2)
    assert (second.start, second.count) == (9, 3)
    assert allocator.current() == 12

    allocator.rollback(second)
    assert allocator.current() == 9
    allocator.rollback(first)
    assert allocator.current() == 7


def test_domain_seq_allocator_commit_closes_reservation() -> None:
    allocator = DomainSeqAllocator(4)
    reservation = allocator.reserve(2)

    allocator.commit(reservation)

    assert allocator.current() == 6
    with pytest.raises(ValueError, match="not open"):
        allocator.rollback(reservation)


async def test_pause_creates_recovery_snapshot_before_correction(tmp_path: Path) -> None:
    core = core_at_role_reveal()
    store = SQLiteRoomStore(tmp_path / "rooms.sqlite3")
    store.migrate()
    store.save_room(
        PersistedRoom(
            room_code="REC001",
            room_id=ROOM_ID,
            seed=core.state.seed,
            rulepack_version="1.1.0",
            expires_at=START_TIME + timedelta(hours=1),
            last_activity_at=START_TIME,
        )
    )
    store.save_core(ROOM_ID, core.state, core.events)
    store.save_room_runtime(ROOM_ID, _runtime())
    actor = _actor(store, core)
    await actor.start()
    actor_ = host_actor()

    pause_result = await actor.submit_command(
        make_envelope(
            actor_,
            HostPauseCommand(reason="transaction snapshot"),
            expected_revision=core.state.revision,
            now=core.clock(),
        ),
        actor_,
    )
    assert pause_result.accepted is True
    assert [snapshot.reason.value for snapshot in store.load_snapshots(ROOM_ID)] == ["PAUSED"]

    patch_result = await actor.submit_command(
        make_envelope(
            actor_,
            HostPatchCommand(
                patch=SetPotionPatch(antidote_available=False, poison_available=False)
            ),
            expected_revision=actor.core.state.revision,
            now=core.clock(),
        ),
        actor_,
    )
    assert patch_result.accepted is True
    assert {snapshot.reason.value for snapshot in store.load_snapshots(ROOM_ID)} == {
        "PAUSED",
        "PRE_CORRECTION",
    }
    await actor.stop()


def test_recovery_transaction_rolls_back_when_store_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, service, core = _paused_store_and_service(tmp_path)
    before_state, before_events = store.load_core(ROOM_ID)
    before_revision = before_state.revision

    def fail_write(*args: object, **kwargs: object) -> None:
        raise RuntimeError("save core failed")

    monkeypatch.setattr(store, "save_core", fail_write)

    with pytest.raises(RuntimeError, match="save core failed"):
        service.correct(
            ROOM_ID,
            HostPatchCommand(
                patch=SetPotionPatch(antidote_available=False, poison_available=False)
            ),
            host_actor(),
            core.clock(),
        )

    assert store.load_core(ROOM_ID) == (before_state, before_events)
    assert store.load_core(ROOM_ID)[0].revision == before_revision
    assert store.load_snapshots(ROOM_ID) == ()
    assert store.load_recovery_audit(ROOM_ID) == ()


def test_recovery_commit_failure_rolls_back_all_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, service, core = _paused_store_and_service(tmp_path)
    before_state, before_events = store.load_core(ROOM_ID)

    def fail_write(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(store, "append_recovery_audit", fail_write)

    with pytest.raises(RuntimeError, match="audit write failed"):
        service.correct(
            ROOM_ID,
            HostPatchCommand(
                patch=SetPotionPatch(antidote_available=False, poison_available=False)
            ),
            host_actor(),
            core.clock(),
        )

    assert store.load_core(ROOM_ID) == (before_state, before_events)
    assert store.load_snapshots(ROOM_ID) == ()
    assert store.load_recovery_audit(ROOM_ID) == ()


def _patch_command(*, antidote: bool = False, poison: bool = False) -> HostPatchCommand:
    return HostPatchCommand(
        patch=SetPotionPatch(
            antidote_available=antidote,
            poison_available=poison,
        )
    )


def test_rewind_requires_exact_latest_pre_correction_snapshot_id(tmp_path: Path) -> None:
    store, service, core = _paused_store_and_service(tmp_path)
    first = service.correct(ROOM_ID, _patch_command(), host_actor(), core.clock())
    assert first.accepted is True
    first_snapshot = store.load_latest_pre_correction_snapshot(ROOM_ID)
    second_snapshot_id = service.snapshot(ROOM_ID, "PRE_CORRECTION")
    assert second_snapshot_id != first_snapshot.snapshot_id
    before_revision = store.load_core(ROOM_ID)[0].revision

    result = service.rewind(
        ROOM_ID,
        HostRewindToSnapshotCommand(snapshot_id=first_snapshot.snapshot_id),
        host_actor(),
        core.clock(),
    )

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.INVALID_TARGET
    assert store.load_core(ROOM_ID)[0].revision == before_revision


def test_rewind_rejects_missing_cross_room_and_older_snapshot_id(tmp_path: Path) -> None:
    store, service, core = _paused_store_and_service(tmp_path)
    missing = service.rewind(
        ROOM_ID,
        HostRewindToSnapshotCommand(snapshot_id=uuid4()),
        host_actor(),
        core.clock(),
    )
    assert missing.error_code is CommandErrorCode.INVALID_TARGET

    store.save_room(
        PersistedRoom(
            room_code="REC002",
            room_id=_SECOND_ROOM_ID,
            seed=core.state.seed,
            rulepack_version="1.1.0",
            expires_at=START_TIME + timedelta(hours=1),
            last_activity_at=START_TIME,
        )
    )
    cross_state = core.state.model_copy(update={"room_id": _SECOND_ROOM_ID})
    cross_snapshot = PersistedSnapshot(
        snapshot_id=uuid4(),
        room_id=_SECOND_ROOM_ID,
        revision=cross_state.revision,
        reason=SnapshotReason.PRE_CORRECTION,
        state=cross_state,
        event_count=cross_state.event_count,
        created_at=core.clock(),
    )
    store.save_snapshot(cross_snapshot)
    cross = service.rewind(
        ROOM_ID,
        HostRewindToSnapshotCommand(snapshot_id=cross_snapshot.snapshot_id),
        host_actor(),
        core.clock(),
    )
    assert cross.error_code is CommandErrorCode.INVALID_TARGET

    first_patch = service.correct(ROOM_ID, _patch_command(), host_actor(), core.clock())
    assert first_patch.accepted is True
    older = store.load_latest_pre_correction_snapshot(ROOM_ID)
    service.snapshot(ROOM_ID, "PRE_CORRECTION")
    older_result = service.rewind(
        ROOM_ID,
        HostRewindToSnapshotCommand(snapshot_id=older.snapshot_id),
        host_actor(),
        core.clock(),
    )
    assert older_result.error_code is CommandErrorCode.INVALID_TARGET

    paused_snapshot_id = service.snapshot(ROOM_ID, "PAUSED")
    wrong_reason = service.rewind(
        ROOM_ID,
        HostRewindToSnapshotCommand(snapshot_id=paused_snapshot_id),
        host_actor(),
        core.clock(),
    )
    assert wrong_reason.error_code is CommandErrorCode.INVALID_TARGET


def test_rewind_keeps_original_events_and_advances_revision(tmp_path: Path) -> None:
    store, service, core = _paused_store_and_service(tmp_path)
    patch = service.correct(ROOM_ID, _patch_command(), host_actor(), core.clock())
    assert patch.accepted is True
    target = store.load_latest_pre_correction_snapshot(ROOM_ID)
    before_state, before_events = store.load_core(ROOM_ID)

    result = service.rewind(
        ROOM_ID,
        HostRewindToSnapshotCommand(snapshot_id=target.snapshot_id),
        host_actor(),
        core.clock(),
    )

    assert result.accepted is True
    state, events = store.load_core(ROOM_ID)
    assert events[:-1] == before_events
    assert events[-1].event_type is EventType.HOST_REWIND_APPLIED
    assert len(events) == len(before_events) + 1
    assert state.revision == before_state.revision + 1
    assert state.event_count == len(events)


def test_rewind_increments_epoch_and_clears_discarded_runtime_state(
    tmp_path: Path,
) -> None:
    published_one = uuid4()
    published_two = uuid4()
    runtime = _runtime(
        outbox_seq=12,
        next_domain_seq=20,
        domain_to_transport={1: 1, 2: 3},
        completed_domain_seqs=(1, 2),
        processed_announcement_seq=2,
        published_message_ids=(published_one, published_two),
    )
    store, service, core = _paused_store_and_service(tmp_path, runtime=runtime)
    patch = service.correct(ROOM_ID, _patch_command(), host_actor(), core.clock())
    assert patch.accepted is True
    target = store.load_latest_pre_correction_snapshot(ROOM_ID)

    result = service.rewind(
        ROOM_ID,
        HostRewindToSnapshotCommand(snapshot_id=target.snapshot_id),
        host_actor(),
        core.clock(),
    )

    assert result.accepted is True
    rewind_runtime = store.load_room_runtime(ROOM_ID)
    assert rewind_runtime.recovery_epoch == 1
    assert rewind_runtime.outbox_seq == 12
    assert rewind_runtime.domain_to_transport == {}
    assert rewind_runtime.completed_domain_seqs == ()
    assert rewind_runtime.processed_announcement_seq == 0
    assert rewind_runtime.published_message_ids == ()
    assert rewind_runtime.next_domain_seq == 21


def test_rewind_tombstones_discarded_command_ids(tmp_path: Path) -> None:
    store, service, core = _paused_store_and_service(tmp_path)
    patch = service.correct(ROOM_ID, _patch_command(), host_actor(), core.clock())
    assert patch.accepted is True
    target = store.load_latest_pre_correction_snapshot(ROOM_ID)

    result = service.rewind(
        ROOM_ID,
        HostRewindToSnapshotCommand(snapshot_id=target.snapshot_id),
        host_actor(),
        core.clock(),
    )

    assert result.accepted is True
    rewind_runtime = store.load_room_runtime(ROOM_ID)
    assert any(
        key.command_id == patch.command_id and key.actor_type == "host"
        for key in rewind_runtime.discarded_command_tombstones
    )
    state, events = store.load_core(ROOM_ID)
    restored = GameCore.restore(
        room_id=ROOM_ID,
        seed=state.seed,
        clock=core.clock,
        state=state,
        events=events,
        command_results=store.load_command_results(ROOM_ID),
    )
    retry = restored.stage_submit(
        make_envelope(
            host_actor(),
            _patch_command(),
            expected_revision=state.revision,
            command_id=patch.command_id,
            now=core.clock(),
        ),
        host_actor(),
        next_domain_seq=DomainSeqAllocator(rewind_runtime.next_domain_seq),
        discarded_command_tombstones=rewind_runtime.discarded_command_tombstones,
    )
    assert retry.command_result.error_code is CommandErrorCode.COMMAND_VOIDED_BY_REWIND


async def test_rewind_rejects_old_epoch_tick_and_admission(tmp_path: Path) -> None:
    core = _pause(core_at_role_reveal())
    store = _persist_core(tmp_path, core)
    actor = _actor(store, core)
    await actor.start()
    host = host_actor()

    patch = await actor.submit_command(
        make_envelope(
            host,
            _patch_command(),
            expected_revision=actor.core.state.revision,
            now=core.clock(),
        ),
        host,
    )
    assert patch.accepted is True
    target = store.load_latest_pre_correction_snapshot(ROOM_ID)
    old_revision = actor.core.state.revision
    rewind = await actor.submit_command(
        make_envelope(
            host,
            HostRewindToSnapshotCommand(snapshot_id=target.snapshot_id),
            expected_revision=actor.core.state.revision,
            now=core.clock(),
        ),
        host,
    )
    assert rewind.accepted is True
    revision_after_rewind = actor.core.state.revision
    outbox_after_rewind = actor.outbox_seq
    runtime_after_rewind = store.load_room_runtime(ROOM_ID)

    await actor.events.put(
        TimerTickEvent(
            revision=old_revision,
            deadline_at=actor.current_deadline() or core.clock(),
            now=core.clock(),
            recovery_epoch=0,
        )
    )
    for _ in range(5):
        await asyncio.sleep(0)
        if actor.events.empty():
            break
    assert actor.core.state.revision == revision_after_rewind
    assert actor.outbox_seq == outbox_after_rewind

    stale_slot = DMAnnouncementSlot(
        domain_seq=1,
        room_id=ROOM_ID,
        revision=old_revision,
        trigger_at_monotonic_ms=0,
        admission_deadline_monotonic_ms=2000,
    )
    admission = await actor.admit(stale_slot)
    assert admission.admitted is False
    assert store.load_room_runtime(ROOM_ID) == runtime_after_rewind
    await actor.stop()


def test_rewind_allocates_new_domain_seq_from_global_watermark(tmp_path: Path) -> None:
    store, service, core = _paused_store_and_service(
        tmp_path,
        runtime=_runtime(next_domain_seq=40),
    )
    patch = service.correct(ROOM_ID, _patch_command(), host_actor(), core.clock())
    assert patch.accepted is True
    target = store.load_latest_pre_correction_snapshot(ROOM_ID)

    result = service.rewind(
        ROOM_ID,
        HostRewindToSnapshotCommand(snapshot_id=target.snapshot_id),
        host_actor(),
        core.clock(),
    )

    assert result.accepted is True
    assert store.load_room_runtime(ROOM_ID).next_domain_seq == 41


def test_force_template_records_audit_without_enabling_provider(tmp_path: Path) -> None:
    core = core_at_role_reveal()
    store = _persist_core(tmp_path, core)
    service = HostRecoveryService(store)
    _, events_before = store.load_core(ROOM_ID)
    runtime_before = store.load_room_runtime(ROOM_ID)

    result = service.force_template(
        ROOM_ID,
        HostForceTemplateCommand(),
        host_actor(),
        core.clock(),
    )

    assert result.accepted is True
    assert store.load_core(ROOM_ID)[0].revision == core.state.revision
    assert store.load_core(ROOM_ID)[1] == events_before
    assert store.load_room_runtime(ROOM_ID) == runtime_before
    audits = store.load_recovery_audit(ROOM_ID)
    assert len(audits) == 1
    assert audits[0].patch_type == "HOST_FORCE_TEMPLATE"
    assert audits[0].after_revision == core.state.revision


def test_force_template_is_idempotent_and_does_not_change_revision_or_outbox(
    tmp_path: Path,
) -> None:
    core = core_at_role_reveal()
    store = _persist_core(tmp_path, core, runtime=_runtime(outbox_seq=9, next_domain_seq=10))
    service = HostRecoveryService(store)

    first = service.force_template(
        ROOM_ID,
        HostForceTemplateCommand(),
        host_actor(),
        core.clock(),
    )
    second = service.force_template(
        ROOM_ID,
        HostForceTemplateCommand(),
        host_actor(),
        core.clock(),
    )

    assert first.accepted is True
    assert second.accepted is True
    assert store.load_core(ROOM_ID)[0].revision == core.state.revision
    assert store.load_room_runtime(ROOM_ID).outbox_seq == 9
    assert len(store.load_recovery_audit(ROOM_ID)) == 1


async def test_successful_recovery_publishes_rebuilt_views_once(tmp_path: Path) -> None:
    core = core_at_role_reveal()
    store = _persist_core(tmp_path, core)
    actor = _actor(store, core)
    subscriber = _RecordingSubscriber()
    await actor.start()
    await actor.attach_subscriber(subscriber)
    host = host_actor()
    pause = await actor.submit_command(
        make_envelope(
            host,
            HostPauseCommand(reason="publish recovery"),
            expected_revision=actor.core.state.revision,
            now=core.clock(),
        ),
        host,
    )
    assert pause.accepted is True
    subscriber.messages.clear()
    outbox_before = actor.outbox_seq

    result = await actor.submit_command(
        make_envelope(
            host,
            _patch_command(poison=True),
            expected_revision=actor.core.state.revision,
            now=core.clock(),
        ),
        host,
    )

    assert result.accepted is True
    assert actor.outbox_seq == outbox_before + 1
    assert sum(isinstance(message, PublicViewUpdate) for message in subscriber.messages) == 1
    assert sum(isinstance(message, HostControlUpdate) for message in subscriber.messages) == 1
    await actor.stop()


async def test_failed_recovery_does_not_increment_outbox_seq(tmp_path: Path) -> None:
    core = core_at_role_reveal()
    store = _persist_core(tmp_path, core)
    actor = _actor(store, core)
    subscriber = _RecordingSubscriber()
    await actor.start()
    await actor.attach_subscriber(subscriber)
    subscriber.messages.clear()
    outbox_before = actor.outbox_seq

    result = await actor.submit_command(
        make_envelope(
            host_actor(),
            _patch_command(),
            expected_revision=actor.core.state.revision,
            now=core.clock(),
        ),
        host_actor(),
    )

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.ILLEGAL_PHASE
    assert actor.outbox_seq == outbox_before
    assert subscriber.messages == []
    await actor.stop()


def test_event_count_and_digest_include_rewind_record_and_ordered_events(
    tmp_path: Path,
) -> None:
    store, service, core = _paused_store_and_service(tmp_path)
    patch = service.correct(ROOM_ID, _patch_command(), host_actor(), core.clock())
    assert patch.accepted is True
    target = store.load_latest_pre_correction_snapshot(ROOM_ID)

    result = service.rewind(
        ROOM_ID,
        HostRewindToSnapshotCommand(snapshot_id=target.snapshot_id),
        host_actor(),
        core.clock(),
    )

    assert result.accepted is True
    state, events = store.load_core(ROOM_ID)
    assert state.event_count == len(events)
    assert state.event_log_digest == event_log_digest(events)
    assert events[-1].event_type is EventType.HOST_REWIND_APPLIED


async def test_recovery_audit_rollback_is_atomic_for_late_conflicts(tmp_path: Path) -> None:
    core = _pause(core_at_role_reveal())
    store = _persist_core(tmp_path, core)
    actor = _actor(store, core)
    await actor.start()
    actor_ = host_actor()
    envelope = make_envelope(
        actor_,
        _patch_command(),
        expected_revision=core.state.revision,
        command_id=uuid4(),
        now=core.clock(),
    )

    first = await actor.submit_command(envelope, actor_)
    before_state, before_events = store.load_core(ROOM_ID)
    before_snapshots = store.load_snapshots(ROOM_ID)
    before_audit = store.load_recovery_audit(ROOM_ID)
    duplicate = await actor.submit_command(envelope, actor_)

    assert duplicate == first
    assert store.load_core(ROOM_ID) == (before_state, before_events)
    assert store.load_snapshots(ROOM_ID) == before_snapshots
    assert store.load_recovery_audit(ROOM_ID) == before_audit
    await actor.stop()


def test_persistence_conflict_rolls_back_entire_recovery_transaction(tmp_path: Path) -> None:
    store, service, core = _paused_store_and_service(tmp_path)
    command = _patch_command()
    first = service.correct(ROOM_ID, command, host_actor(), core.clock())
    assert first.accepted is True
    before_state, before_events = store.load_core(ROOM_ID)
    before_snapshots = store.load_snapshots(ROOM_ID)
    before_audit = store.load_recovery_audit(ROOM_ID)

    with pytest.raises(PersistenceConflictError), store.transaction():
        store.save_core(
            ROOM_ID,
            before_state.model_copy(update={"revision": before_state.revision + 1}),
            before_events,
        )

    assert store.load_core(ROOM_ID) == (before_state, before_events)
    assert store.load_snapshots(ROOM_ID) == before_snapshots
    assert store.load_recovery_audit(ROOM_ID) == before_audit


async def test_host_patch_authorization_precedes_seat_dedupe_cache(tmp_path: Path) -> None:
    core = core_at_day_vote()
    round_ = core.state.vote_round
    assert round_ is not None
    voter_seat_id = round_.eligible_voter_ids[0]
    voter = seat_actor(voter_seat_id)
    command_id = uuid4()
    vote = core.submit(
        make_envelope(
            voter,
            VoteCommand(target_seat_id=_living_other(core, voter_seat_id)),
            expected_revision=core.state.revision,
            command_id=command_id,
            now=core.clock(),
        ),
        voter,
    )
    assert vote.accepted is True
    _pause(core)
    store = _persist_core(tmp_path, core)
    actor = _actor(store, core)
    await actor.start()
    before_state = actor.core.state
    before_events = actor.core.events
    before_outbox = actor.outbox_seq
    before_runtime = store.load_room_runtime(ROOM_ID)

    result = await actor.submit_command(
        make_envelope(
            voter,
            _patch_command(poison=True),
            expected_revision=actor.core.state.revision,
            command_id=command_id,
            now=core.clock(),
        ),
        voter,
    )

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.ACTOR_NOT_AUTHORIZED
    assert actor.core.state == before_state
    assert actor.core.events == before_events
    assert actor.outbox_seq == before_outbox
    assert store.load_room_runtime(ROOM_ID) == before_runtime
    await actor.stop()


async def test_recovery_command_rejects_cross_room_envelope_before_staging(
    tmp_path: Path,
) -> None:
    core = _pause(core_at_role_reveal())
    store = _persist_core(tmp_path, core)
    actor = _actor(store, core)
    await actor.start()
    before_state = actor.core.state
    before_events = actor.core.events
    before_outbox = actor.outbox_seq
    before_snapshots = store.load_snapshots(ROOM_ID)
    before_audit = store.load_recovery_audit(ROOM_ID)
    envelope = make_envelope(
        host_actor(),
        _patch_command(poison=True),
        expected_revision=actor.core.state.revision,
        command_id=uuid4(),
        now=core.clock(),
    ).model_copy(update={"room_id": _SECOND_ROOM_ID})

    result = await actor.submit_command(envelope, host_actor())

    assert result.accepted is False
    assert result.error_code is CommandErrorCode.ACTOR_NOT_AUTHORIZED
    assert actor.core.state == before_state
    assert actor.core.events == before_events
    assert actor.outbox_seq == before_outbox
    assert store.load_snapshots(ROOM_ID) == before_snapshots
    assert store.load_recovery_audit(ROOM_ID) == before_audit
    await actor.stop()


async def test_force_template_retry_survives_later_revision_change(tmp_path: Path) -> None:
    core = core_at_role_reveal()
    store = _persist_core(tmp_path, core)
    actor = _actor(store, core)
    await actor.start()
    host = host_actor()
    dm_service = actor.dm_service
    force_envelope = make_envelope(
        host,
        HostForceTemplateCommand(),
        expected_revision=actor.core.state.revision,
        command_id=uuid4(),
        now=core.clock(),
    )

    first = await actor.submit_command(force_envelope, host)
    assert first.accepted is True
    pause = await actor.submit_command(
        make_envelope(
            host,
            HostPauseCommand(reason="advance force revision"),
            expected_revision=actor.core.state.revision,
            now=core.clock(),
        ),
        host,
    )
    assert pause.accepted is True
    before_revision = actor.core.state.revision
    before_outbox = actor.outbox_seq
    before_audit = store.load_recovery_audit(ROOM_ID)

    retry = await actor.submit_command(force_envelope, host)

    assert retry.accepted is True
    assert retry.error_code is None
    assert retry.revision == first.revision
    assert actor.core.state.revision == before_revision
    assert actor.outbox_seq == before_outbox
    assert actor.dm_service is dm_service
    assert store.load_recovery_audit(ROOM_ID) == before_audit
    await actor.stop()


def test_force_template_audit_uses_canonical_payload_without_extra_semantics(
    tmp_path: Path,
) -> None:
    core = core_at_role_reveal()
    store = _persist_core(tmp_path, core)
    service = HostRecoveryService(store)

    result = service.force_template(
        ROOM_ID,
        HostForceTemplateCommand(),
        host_actor(),
        core.clock(),
    )

    audit = store.load_recovery_audit(ROOM_ID)[0]
    canonical = HostTemplateForcedAudit(
        command_id=audit.command_id,
        revision=audit.after_revision,
    )
    assert result.command_id == canonical.command_id
    assert audit.before_revision == canonical.revision
    assert audit.after_revision == canonical.revision
    assert audit.diff == {}


def test_phase_changing_command_creates_phase_start_snapshot(tmp_path: Path) -> None:
    core = core_at_role_reveal()
    store = _persist_core(tmp_path, core)
    actor = _actor(store, core)

    for seat_id in range(1, 7):
        actor_ = seat_actor(seat_id)
        mutation = actor.core.stage_submit(
            make_envelope(
                actor_,
                ConfirmRoleCommand(),
                expected_revision=actor.core.state.revision,
                now=core.clock(),
            ),
            actor_,
            next_domain_seq=actor.domain_seq_allocator,
            discarded_command_tombstones=actor._discarded_command_tombstones,
        )
        actor._commit_core_mutation(mutation)

    assert SnapshotReason.PHASE_START in {
        snapshot.reason for snapshot in store.load_snapshots(ROOM_ID)
    }


def test_timeout_phase_change_creates_phase_start_snapshot(tmp_path: Path) -> None:
    core = core_at_role_reveal()
    assert core.state.deadline_at is not None
    core.clock.set(core.state.deadline_at)
    store = _persist_core(tmp_path, core)
    actor = _actor(store, core)

    mutation = actor.core.stage_tick(next_domain_seq=actor.domain_seq_allocator)
    actor._commit_core_mutation(mutation)

    assert mutation.next_state.phase is Phase.NIGHT_WOLF
    assert SnapshotReason.PHASE_START in {
        snapshot.reason for snapshot in store.load_snapshots(ROOM_ID)
    }


def test_first_winner_mutation_selects_game_end_snapshot(tmp_path: Path) -> None:
    core = core_at_role_reveal()
    store = _persist_core(tmp_path, core)
    actor = _actor(store, core)
    winner_state = core.state.model_copy(update={"phase": Phase.GAME_END, "winner": Faction.GOOD})
    mutation = CoreMutation(
        next_state=winner_state,
        appended_events=(),
        command_result=CommandResult(
            command_id=uuid4(),
            accepted=True,
            revision=winner_state.revision,
            event_ids=(),
            error_code=None,
        ),
        dedupe_key=None,
        cache_result=False,
    )

    snapshot = actor._snapshot_for_mutation(mutation)

    assert snapshot is not None
    assert snapshot.reason is SnapshotReason.GAME_END


async def test_actor_rewind_failure_keeps_all_memory_and_durable_state_unchanged(
    tmp_path: Path,
) -> None:
    core = _pause(core_at_role_reveal())
    store = _persist_core(tmp_path, core)
    actor = _actor(store, core)
    subscriber = _RecordingSubscriber()
    await actor.start()
    await actor.attach_subscriber(subscriber)
    host = host_actor()
    patch = await actor.submit_command(
        make_envelope(
            host,
            _patch_command(poison=True),
            expected_revision=actor.core.state.revision,
            now=core.clock(),
        ),
        host,
    )
    assert patch.accepted is True
    target = store.load_latest_pre_correction_snapshot(ROOM_ID)
    subscriber.messages.clear()
    before_state, before_events = store.load_core(ROOM_ID)
    before_runtime = store.load_room_runtime(ROOM_ID)
    before_snapshots = store.load_snapshots(ROOM_ID)
    before_audit = store.load_recovery_audit(ROOM_ID)
    before_results = store.load_command_results(ROOM_ID)
    before_actor_state = actor.core.state
    before_actor_events = actor.core.events
    before_outbox = actor.outbox_seq
    before_tombstones = actor._discarded_command_tombstones
    before_messages = list(subscriber.messages)

    with store.transaction() as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_recovery_delete
            BEFORE DELETE ON command_results
            BEGIN
                SELECT RAISE(ABORT, 'blocked recovery delete');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="blocked recovery delete"):
        await actor.submit_command(
            make_envelope(
                host,
                HostRewindToSnapshotCommand(snapshot_id=target.snapshot_id),
                expected_revision=actor.core.state.revision,
                now=core.clock(),
            ),
            host,
        )

    assert store.load_core(ROOM_ID) == (before_state, before_events)
    assert store.load_room_runtime(ROOM_ID) == before_runtime
    assert store.load_snapshots(ROOM_ID) == before_snapshots
    assert store.load_recovery_audit(ROOM_ID) == before_audit
    assert store.load_command_results(ROOM_ID) == before_results
    assert actor.core.state == before_actor_state
    assert actor.core.events == before_actor_events
    assert actor.outbox_seq == before_outbox
    assert actor._discarded_command_tombstones == before_tombstones
    assert subscriber.messages == before_messages
