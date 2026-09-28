from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from tests.factories import Scenario, core_at_role_reveal, core_at_wolf
from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.dm_contracts import DMTraceRecord
from werewolf_dm.application.rooms import (
    RoomActor,
    RoomRegistry,
    SequenceTokenSource,
    SubmitCommandEvent,
)
from werewolf_dm.domain.contracts import (
    AuthenticatedActor,
    CommandEnvelope,
    HostPauseCommand,
)
from werewolf_dm.domain.replay import state_hash
from werewolf_dm.domain.visibility import project_public_view, project_seat_view
from werewolf_dm.infrastructure.persistence import (
    PersistedRoomRuntime,
    SQLiteRoomStore,
)
from werewolf_dm.interfaces.http_ws.app import create_app

_START = datetime(2026, 9, 25, tzinfo=UTC)
_TOKEN_TTL = timedelta(hours=6)
_ROOM_CODE = "ROOM01"
_SECOND_ROOM_CODE = "ROOM02"
_HOST_TOKEN = "host-token-SENTINEL"
_SEAT_TOKEN = "seat-token-SENTINEL"
_SECOND_HOST_TOKEN = "second-host-token-SENTINEL"


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _register_actor(
    registry: RoomRegistry,
    *,
    scenario: Scenario,
    room_code: str,
    room_id: UUID | None = None,
) -> RoomActor:
    actor = RoomActor(
        room_id=scenario.core.state.room_id if room_id is None else room_id,
        room_code=room_code,
        seed=scenario.core.state.seed,
        clock=registry.clock,
        expires_at=registry.clock() + _TOKEN_TTL,
        last_activity_at=registry.clock(),
        core=scenario.core,
    )
    registry.rooms[room_code] = actor
    return actor


def _open_registry(
    database_path: Path,
    *,
    now: datetime = _START,
    tokens: tuple[str, ...] = (),
    room_codes: tuple[str, ...] = (),
    uuid_source=None,
) -> tuple[SQLiteRoomStore, RoomRegistry]:
    store = SQLiteRoomStore(database_path)
    store.migrate()
    registry = RoomRegistry(
        clock=FrozenClock(now),
        token_source=SequenceTokenSource(tokens=tokens, room_codes=room_codes),
        seed_source=lambda: 101,
        uuid_source=uuid_source or uuid4,
        store=store,
    )
    return store, registry


def _host(room_id: UUID) -> AuthenticatedActor:
    return AuthenticatedActor(actor_type="host", seat_id=None, room_id=room_id)


async def _start_submit_stop(
    actor: RoomActor,
    envelope: CommandEnvelope,
    authenticated: AuthenticatedActor,
):
    await actor.start()
    try:
        return await actor.submit_command(envelope, authenticated)
    finally:
        await actor.stop()


class RecordingSubscriber:
    def __init__(self) -> None:
        self.subscription_id = uuid4()
        self.actor_type = "host"
        self.seat_id = None
        self.session_id = None
        self.channels = frozenset({"public", "host.control"})
        self.messages: list[object] = []

    def offer(self, message: object) -> bool:
        self.messages.append(message)
        return True

    def request_close(self, code: int) -> None:
        del code


@contextmanager
def _export_client() -> Iterator[tuple[TestClient, RoomRegistry, Scenario]]:
    scenario = core_at_wolf()
    registry = RoomRegistry(
        clock=FrozenClock(_START),
        token_source=SequenceTokenSource(
            tokens=(_HOST_TOKEN, _SEAT_TOKEN, _SECOND_HOST_TOKEN),
            room_codes=(_ROOM_CODE, _SECOND_ROOM_CODE),
        ),
        seed_source=lambda: 101,
    )
    actor = _register_actor(
        registry,
        scenario=scenario,
        room_code=_ROOM_CODE,
    )
    registry.tokens.issue_host(actor.room_id, _TOKEN_TTL)
    registry.tokens.issue_seat(actor.room_id, 1, _TOKEN_TTL)

    with TestClient(create_app(registry), raise_server_exceptions=False) as client:
        yield client, registry, scenario


def test_player_replay_contains_only_public_and_own_private_facts() -> None:
    with _export_client() as (client, _, scenario):
        response = client.get(
            f"/rooms/{_ROOM_CODE}/replay",
            headers=_authorization(_SEAT_TOKEN),
        )

    assert response.status_code == 200
    replay = response.json()
    assert set(replay) == {
        "room_id",
        "revision",
        "public_timeline",
        "private_facts",
        "events",
    }
    assert replay["room_id"] == str(scenario.core.state.room_id)
    assert replay["revision"] == scenario.core.state.revision

    seat_one_facts = tuple(
        fact for fact in scenario.core.state.private_facts if fact.recipient_seat_id == 1
    )
    assert [fact["fact_type"] for fact in replay["private_facts"]] == [
        fact.fact_type for fact in seat_one_facts
    ]
    assert [fact["fact_type"] for fact in replay["private_facts"]] == ["WITCH_POTIONS"]

    for event in replay["events"]:
        visibility = event["visibility"]
        assert visibility["scope"] == "public" or (
            visibility["scope"] == "seat" and visibility["seat_id"] == 1
        )

    role_events = [event for event in replay["events"] if event["event_type"] == "ROLE_ASSIGNED"]
    assert [event["fact_payload"]["role"] for event in role_events] == ["WITCH"]
    serialized = json.dumps(replay, ensure_ascii=False)
    assert "WOLF_TEAM" not in serialized
    assert "WOLF_DECISION" not in serialized
    assert "WEREWOLF" not in serialized
    assert "SEER" not in serialized


def test_player_replay_redacts_host_pause_reason() -> None:
    secret = "SECRET_REASON_SENTINEL"
    with _export_client() as (client, _, scenario):
        host = AuthenticatedActor(
            actor_type="host",
            seat_id=None,
            room_id=scenario.core.state.room_id,
        )
        result = scenario.core.submit(
            CommandEnvelope(
                command_id=uuid4(),
                room_id=scenario.core.state.room_id,
                expected_revision=scenario.core.state.revision,
                issued_at=_START,
                payload=HostPauseCommand(reason=secret),
            ),
            host,
        )
        assert result.accepted is True

        response = client.get(
            f"/rooms/{_ROOM_CODE}/replay",
            headers=_authorization(_SEAT_TOKEN),
        )

    assert response.status_code == 200
    replay = response.json()
    pause_events = [event for event in replay["events"] if event["event_type"] == "HOST_PAUSED"]
    assert pause_events
    assert all(event["fact_payload"]["reason"] == "主持人操作" for event in pause_events)
    assert secret not in response.text


def test_player_replay_cannot_request_dm_trace_or_snapshots() -> None:
    with _export_client() as (client, _, _):
        response = client.request(
            "GET",
            f"/rooms/{_ROOM_CODE}/replay?include=dm_trace,snapshots",
            headers=_authorization(_SEAT_TOKEN),
            json={"include": ["dm_trace", "snapshots"]},
        )

    assert response.status_code == 200
    replay = response.json()
    assert replay.keys().isdisjoint({"dm_trace", "snapshots", "state", "raw_events"})


def test_host_audit_returns_complete_state_and_events() -> None:
    with _export_client() as (client, _, scenario):
        response = client.get(
            f"/rooms/{_ROOM_CODE}/audit",
            headers=_authorization(_HOST_TOKEN),
        )

    assert response.status_code == 200
    audit = response.json()
    assert set(audit) == {
        "room_id",
        "revision",
        "state",
        "raw_events",
        "dm_trace",
        "snapshots",
    }
    assert audit["room_id"] == str(scenario.core.state.room_id)
    assert audit["revision"] == scenario.core.state.revision
    assert audit["dm_trace"] == []
    assert audit["snapshots"] == []
    assert len(audit["raw_events"]) == len(scenario.core.events)

    roles = {player["seat_id"]: player["role"] for player in audit["state"]["players"]}
    assert roles == {
        1: "WITCH",
        2: "WEREWOLF",
        3: "VILLAGER",
        4: "SEER",
        5: "WEREWOLF",
        6: "VILLAGER",
    }
    hidden_role_events = [
        event for event in audit["raw_events"] if event["event_type"] == "ROLE_ASSIGNED"
    ]
    assert {event["visibility"]["seat_id"] for event in hidden_role_events} == {
        1,
        2,
        3,
        4,
        5,
        6,
    }


def test_seat_and_host_tokens_cannot_cross_export_scopes() -> None:
    with _export_client() as (client, _, _):
        seat_audit = client.get(
            f"/rooms/{_ROOM_CODE}/audit",
            headers=_authorization(_SEAT_TOKEN),
        )
        host_replay = client.get(
            f"/rooms/{_ROOM_CODE}/replay",
            headers=_authorization(_HOST_TOKEN),
        )

    for response in (seat_audit, host_replay):
        assert response.status_code == 403
        assert response.json()["code"] == "ACTOR_NOT_AUTHORIZED"
        assert set(response.json()) == {"code", "message", "request_id"}


def test_export_requires_bearer_header_not_raw_dict_body() -> None:
    with _export_client() as (client, _, _):
        missing = client.get(f"/rooms/{_ROOM_CODE}/replay")
        raw_dict = client.request(
            "GET",
            f"/rooms/{_ROOM_CODE}/replay",
            json={"token": _SEAT_TOKEN},
        )

    for response in (missing, raw_dict):
        assert response.status_code == 401
        assert response.json()["code"] == "TOKEN_INVALID"
        assert set(response.json()) == {"code", "message", "request_id"}
        assert _SEAT_TOKEN not in response.text


def test_export_authorization_order_and_room_binding() -> None:
    with _export_client() as (client, registry, scenario):
        invalid_token = client.get(
            "/rooms/NO-SUCH/replay",
            headers=_authorization("invalid-token"),
        )
        missing_room = client.get(
            "/rooms/NO-SUCH/replay",
            headers=_authorization(_HOST_TOKEN),
        )

        second_room_id = uuid4()
        _register_actor(
            registry,
            scenario=scenario,
            room_code=_SECOND_ROOM_CODE,
            room_id=second_room_id,
        )
        registry.tokens.issue_host(second_room_id, _TOKEN_TTL)
        cross_room = client.get(
            f"/rooms/{_SECOND_ROOM_CODE}/replay",
            headers=_authorization(_SEAT_TOKEN),
        )

    assert invalid_token.status_code == 401
    assert invalid_token.json()["code"] == "TOKEN_INVALID"
    assert missing_room.status_code == 404
    assert missing_room.json()["code"] == "ROOM_NOT_FOUND"
    assert cross_room.status_code == 403
    assert cross_room.json()["code"] == "ACTOR_NOT_AUTHORIZED"


def test_exports_omit_tokens_digests_and_raw_exception_material() -> None:
    with _export_client() as (client, _, _):
        replay = client.get(
            f"/rooms/{_ROOM_CODE}/replay",
            headers=_authorization(_SEAT_TOKEN),
        )
        audit = client.get(
            f"/rooms/{_ROOM_CODE}/audit",
            headers=_authorization(_HOST_TOKEN),
        )
        invalid_token = client.get(
            f"/rooms/{_ROOM_CODE}/replay",
            headers={"Authorization": "Bearer RAW-EXCEPTION-SENTINEL Traceback RuntimeError"},
        )

    assert replay.status_code == 200
    assert audit.status_code == 200
    exported = json.dumps(
        {"replay": replay.json(), "audit": audit.json()},
        ensure_ascii=False,
    )
    for forbidden in (_HOST_TOKEN, _SEAT_TOKEN, _digest(_HOST_TOKEN), _digest(_SEAT_TOKEN)):
        assert forbidden not in exported
    assert "RAW-EXCEPTION-SENTINEL" not in invalid_token.text
    assert "Traceback" not in invalid_token.text
    assert "RuntimeError" not in invalid_token.text


def test_registry_restart_restores_views_and_command_dedupe(tmp_path: Path) -> None:
    database_path = tmp_path / "restore.sqlite3"
    store, registry = _open_registry(
        database_path,
        tokens=(_HOST_TOKEN, _SEAT_TOKEN),
        room_codes=(_ROOM_CODE,),
    )
    created = registry.create_room(_TOKEN_TTL)
    joined = registry.join_room(created.room_code, display_name="Alice")
    actor = registry.get_by_code(created.room_code)
    envelope = CommandEnvelope(
        command_id=uuid4(),
        room_id=created.room_id,
        expected_revision=actor.core.state.revision,
        issued_at=_START,
        payload=HostPauseCommand(reason="restart"),
    )
    result = asyncio.run(_start_submit_stop(actor, envelope, _host(created.room_id)))
    state_before = actor.core.state
    events_before = actor.core.events
    store.close()

    reopened_store, reopened = _open_registry(database_path)
    restored = reopened.get_by_code(created.room_code)

    assert restored.core.state == state_before
    assert restored.core.events == events_before
    assert state_hash(restored.core.state) == state_hash(state_before)
    assert project_public_view(restored.core.state).paused is True
    seat_view = project_seat_view(
        restored.core.state,
        1,
        AuthenticatedActor(actor_type="seat", seat_id=1, room_id=created.room_id),
    )
    assert seat_view.room_id == created.room_id
    assert reopened.tokens.resolve(joined.seat_token).seat_id == 1

    deduped = asyncio.run(
        _start_submit_stop(
            restored,
            envelope,
            _host(created.room_id),
        )
    )

    assert deduped.command_id == result.command_id
    assert deduped.accepted is result.accepted
    assert deduped.revision == result.revision
    assert deduped == result
    assert restored.core.state == state_before
    assert restored.core.events == events_before
    reopened_store.close()


def test_restart_preserves_outbox_admission_and_audit_trace(tmp_path: Path) -> None:
    database_path = tmp_path / "admission.sqlite3"
    scenario = core_at_wolf()
    room_id = scenario.core.state.room_id
    store, registry = _open_registry(
        database_path,
        tokens=(_HOST_TOKEN,),
        room_codes=(_ROOM_CODE,),
        uuid_source=lambda: room_id,
    )
    created = registry.create_room(_TOKEN_TTL)
    actor = registry.get_by_code(created.room_code)
    actor.core = scenario.core
    store.save_core(room_id, actor.core.state, actor.core.events)
    outbox_seqs = tuple(item.seq for item in actor.core.state.outbox)
    assert outbox_seqs
    trace = DMTraceRecord(
        trace_id=uuid4(),
        intent_id=uuid4(),
        template_variant_id="public.phase.notice.neutral",
        catalog_version="s4-template-v1",
        source_event_ids=(actor.core.events[-1].event_id,),
        channel="public",
        audience_seat_ids=(),
        admission_status="admitted",
        elapsed_ms=3,
    )
    transport_trace = trace.model_copy(
        update={
            "trace_id": uuid4(),
            "admission_status": "suppressed",
            "suppress_reason": "transport_failed",
        }
    )
    persisted_outbox_seq = 7
    restored_runtime = PersistedRoomRuntime(
        outbox_seq=persisted_outbox_seq,
        domain_to_transport={seq: index for index, seq in enumerate(outbox_seqs, start=1)},
        completed_domain_seqs=outbox_seqs,
        processed_announcement_seq=max(outbox_seqs),
        published_message_ids=(uuid4(),),
        next_domain_seq=max(outbox_seqs) + 1,
        recovery_epoch=9,
        discarded_command_tombstones=(),
    )
    store.save_room_runtime(room_id, restored_runtime)
    store.save_dm_trace(room_id, "DM_TRACE", trace)
    store.save_dm_trace(room_id, "DM_TRANSPORT_TRACE", transport_trace)
    store.close()

    reopened_store, reopened = _open_registry(database_path)
    restored = reopened.get_by_code(created.room_code)
    before_seq = restored.outbox_seq
    before_processed = restored.processed_announcement_seq
    before_mapping = dict(restored.domain_to_transport)

    asyncio.run(restored.consume_announcements())

    assert restored.outbox_seq == before_seq == persisted_outbox_seq
    assert restored.processed_announcement_seq == before_processed == max(outbox_seqs)
    assert dict(restored.domain_to_transport) == before_mapping
    assert restored.dm_trace == [trace]
    assert restored.dm_transport_trace == [transport_trace]
    assert restored.recovery_epoch == 9

    pause = CommandEnvelope(
        command_id=uuid4(),
        room_id=room_id,
        expected_revision=restored.core.state.revision,
        issued_at=_START,
        payload=HostPauseCommand(reason="after restart"),
    )
    ack = asyncio.run(_start_submit_stop(restored, pause, _host(room_id)))

    assert ack.accepted is True
    assert restored.outbox_seq > persisted_outbox_seq

    with TestClient(create_app(reopened), raise_server_exceptions=False) as client:
        response = client.get(
            f"/rooms/{_ROOM_CODE}/audit?include=dm_trace",
            headers=_authorization(_HOST_TOKEN),
        )

    assert response.status_code == 200
    assert response.json()["dm_trace"][0]["trace_id"] == str(trace.trace_id)
    reopened_store.close()


def test_stale_epoch_submit_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    actor = RoomActor(
        room_id=UUID(int=701),
        room_code="ROOM01",
        seed=101,
        clock=FrozenClock(_START),
        expires_at=_START + _TOKEN_TTL,
        last_activity_at=_START,
    )
    before_state = actor.core.state
    before_events = actor.core.events
    actor.outbox_seq = 5
    envelope = CommandEnvelope(
        command_id=uuid4(),
        room_id=actor.room_id,
        expected_revision=before_state.revision,
        issued_at=_START,
        payload=HostPauseCommand(reason="stale"),
    )

    async def scenario() -> None:
        await actor.start()
        actor.recovery_epoch = 2
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        await actor.events.put(
            SubmitCommandEvent(
                envelope=envelope,
                actor=_host(actor.room_id),
                result=future,
                recovery_epoch=1,
            )
        )
        ack = await future
        assert ack.accepted is False
        assert ack.error_code == "COMMAND_VOIDED_BY_REWIND"
        assert ack.outbox_seq == 5
        await actor.stop()

    asyncio.run(scenario())

    assert actor.core.state == before_state
    assert actor.core.events == before_events


def test_submit_rollback_keeps_memory_state_events_dedupe_and_allocator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, registry = _open_registry(
        tmp_path / "submit-rollback.sqlite3",
        tokens=(_HOST_TOKEN,),
        room_codes=(_ROOM_CODE,),
    )
    created = registry.create_room(_TOKEN_TTL)
    actor = registry.get_by_code(created.room_code)
    before_state = actor.core.state
    before_events = actor.core.events
    before_cache = dict(actor.core.command_dedupe_cache)
    before_outbox = actor.outbox_seq
    before_watermark = actor.domain_seq_allocator.current()
    envelope = CommandEnvelope(
        command_id=uuid4(),
        room_id=created.room_id,
        expected_revision=before_state.revision,
        issued_at=_START,
        payload=HostPauseCommand(reason="rollback"),
    )

    def fail_save_core(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("save core failed")

    monkeypatch.setattr(store, "save_core", fail_save_core)

    async def scenario() -> None:
        await actor.start()
        with pytest.raises(RuntimeError, match="save core failed"):
            await actor.submit_command(envelope, _host(created.room_id))

    asyncio.run(scenario())

    assert actor.core.state == before_state
    assert actor.core.events == before_events
    assert actor.core.command_dedupe_cache == before_cache
    assert actor.outbox_seq == before_outbox
    assert actor.domain_seq_allocator.current() == before_watermark
    store.close()


def test_tick_rollback_keeps_timer_state_events_and_outbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core = core_at_role_reveal()
    room_id = core.state.room_id
    store, registry = _open_registry(
        tmp_path / "tick-rollback.sqlite3",
        tokens=(_HOST_TOKEN,),
        room_codes=(_ROOM_CODE,),
        uuid_source=lambda: room_id,
    )
    created = registry.create_room(_TOKEN_TTL)
    actor = registry.get_by_code(created.room_code)
    actor.core = core
    store.save_core(room_id, actor.core.state, actor.core.events)
    before_state = actor.core.state
    before_events = actor.core.events
    before_outbox = actor.outbox_seq
    before_watermark = actor.domain_seq_allocator.current()
    assert before_state.deadline_at is not None

    def fail_save_core(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("save core failed")

    monkeypatch.setattr(store, "save_core", fail_save_core)

    async def scenario_run() -> None:
        await actor.start()
        clock = actor.clock
        assert before_state.deadline_at is not None
        clock.set(before_state.deadline_at)
        actor.core.clock.set(before_state.deadline_at)
        await actor.enqueue_timer_tick(
            revision=before_state.revision,
            deadline_at=before_state.deadline_at,
            now=before_state.deadline_at,
        )
        with pytest.raises(RuntimeError, match="save core failed"):
            await actor.attach_subscriber(RecordingSubscriber())

    asyncio.run(scenario_run())

    assert actor.core.state == before_state
    assert actor.core.events == before_events
    assert actor.outbox_seq == before_outbox
    assert actor.domain_seq_allocator.current() == before_watermark
    store.close()


def test_announcement_rollback_keeps_completed_processed_and_outbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = core_at_wolf()
    store, registry = _open_registry(
        tmp_path / "announcement-rollback.sqlite3",
        tokens=(_HOST_TOKEN,),
        room_codes=(_ROOM_CODE,),
        uuid_source=lambda: scenario.core.state.room_id,
    )
    created = registry.create_room(_TOKEN_TTL)
    actor = registry.get_by_code(created.room_code)
    actor.core = scenario.core
    before_completed = set(actor._completed_domain_seqs)
    before_processed = actor.processed_announcement_seq
    before_outbox = actor.outbox_seq
    before_traces = list(actor.dm_trace)

    def fail_save_runtime(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("save runtime failed")

    monkeypatch.setattr(store, "save_room_runtime", fail_save_runtime)

    async def scenario_run() -> None:
        with pytest.raises(RuntimeError, match="save runtime failed"):
            await actor.consume_announcements()

    asyncio.run(scenario_run())

    assert actor._completed_domain_seqs == before_completed
    assert actor.processed_announcement_seq == before_processed
    assert actor.outbox_seq == before_outbox
    assert actor.dm_trace == before_traces
    store.close()


def test_publish_rollback_keeps_transport_sequence_and_subscribers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, registry = _open_registry(
        tmp_path / "publish-rollback.sqlite3",
        tokens=(_HOST_TOKEN,),
        room_codes=(_ROOM_CODE,),
    )
    created = registry.create_room(_TOKEN_TTL)
    actor = registry.get_by_code(created.room_code)
    subscriber = RecordingSubscriber()
    actor.subscribers[subscriber.subscription_id] = subscriber

    def fail_save_runtime(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("save runtime failed")

    monkeypatch.setattr(store, "save_room_runtime", fail_save_runtime)

    with pytest.raises(RuntimeError, match="save runtime failed"):
        actor._publish_updates()

    assert actor.outbox_seq == 0
    assert subscriber.messages == []
    store.close()


def test_registry_restart_skips_corrupt_room_without_blocking_other_rooms(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "corrupt-room.sqlite3"
    room_ids = iter((UUID(int=501), UUID(int=502)))
    store, registry = _open_registry(
        database_path,
        tokens=("host-one", "host-two"),
        room_codes=("ROOM01", "ROOM02"),
        uuid_source=lambda: next(room_ids),
    )
    corrupt = registry.create_room(_TOKEN_TTL)
    healthy = registry.create_room(_TOKEN_TTL)
    store.close()

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE rooms SET state_json = ? WHERE room_code = ?",
            ("{", corrupt.room_code),
        )
        connection.commit()
    finally:
        connection.close()

    reopened_store, reopened = _open_registry(database_path)

    assert healthy.room_code in reopened.rooms
    assert corrupt.room_code not in reopened.rooms
    reopened_store.close()
