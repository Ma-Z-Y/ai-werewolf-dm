import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.rooms import (
    RoomActor,
    RoomRegistry,
    SequenceTokenSource,
    TokenService,
)
from werewolf_dm.infrastructure.persistence import SQLiteRoomStore


class CountingClock:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.now

    def set(self, now: datetime) -> None:
        self.now = now


def test_token_issue_samples_clock_once() -> None:
    now = datetime(2026, 9, 24, tzinfo=UTC)
    clock = CountingClock(now)
    service = TokenService(
        SequenceTokenSource(
            tokens=("host-token", "seat-token"),
            room_codes=("ROOM01",),
        ),
        clock,
    )

    host_token, host_expires_at = service.issue_host(UUID(int=1), timedelta(hours=1))
    assert clock.calls == 1
    assert service.resolve(host_token).issued_at == now
    assert host_expires_at == now + timedelta(hours=1)

    seat_token, seat_expires_at = service.issue_seat(UUID(int=1), 1, timedelta(hours=1))
    assert clock.calls == 3
    assert service.resolve(seat_token).issued_at == now
    assert seat_expires_at == now + timedelta(hours=1)


def test_create_room_returns_unique_code_and_host_token() -> None:
    now = datetime(2026, 9, 24, tzinfo=UTC)
    registry = RoomRegistry(
        clock=FrozenClock(now),
        token_source=SequenceTokenSource(
            tokens=("host-token",),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
    )

    created = registry.create_room(ttl=timedelta(hours=6))

    assert created.room_code == "ROOM01"
    assert created.host_token == "host-token"
    assert isinstance(created.room_id, UUID)
    assert created.expires_at == datetime(2026, 9, 24, 6, tzinfo=UTC)
    record = registry.tokens.resolve(created.host_token)
    assert record.issued_at == now
    assert record.expires_at == created.expires_at


def test_created_room_and_host_token_survive_registry_reopen(tmp_path: Path) -> None:
    now = datetime(2026, 9, 24, tzinfo=UTC)
    database_path = tmp_path / "rooms.sqlite3"
    store = SQLiteRoomStore(database_path)
    store.migrate()
    registry = RoomRegistry(
        clock=FrozenClock(now),
        token_source=SequenceTokenSource(
            tokens=("host-token",),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
        store=store,
    )
    created = registry.create_room(timedelta(hours=6))
    store.close()

    reopened_store = SQLiteRoomStore(database_path)
    reopened_store.migrate()
    reopened = RoomRegistry(
        clock=FrozenClock(now),
        token_source=SequenceTokenSource(tokens=(), room_codes=()),
        seed_source=lambda: 999,
        store=reopened_store,
    )

    actor = reopened.get_by_code(created.room_code)
    record = reopened.tokens.resolve(created.host_token)
    assert actor.room_id == created.room_id
    assert actor.core.state.seed == 101
    assert record.actor_type == "host"
    assert record.expires_at == created.expires_at
    assert created.host_token.encode() not in database_path.read_bytes()
    assert hashlib.sha256(created.host_token.encode()).hexdigest().encode() in (
        database_path.read_bytes()
    )
    reopened_store.close()


def test_issued_seat_token_digest_survives_registry_reopen(tmp_path: Path) -> None:
    now = datetime(2026, 9, 24, tzinfo=UTC)
    database_path = tmp_path / "rooms.sqlite3"
    store = SQLiteRoomStore(database_path)
    store.migrate()
    registry = RoomRegistry(
        clock=FrozenClock(now),
        token_source=SequenceTokenSource(
            tokens=("host-token", "seat-token"),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
        store=store,
    )
    created = registry.create_room(timedelta(hours=6))
    joined = registry.join_room(created.room_code, display_name="Alpha")
    store.close()

    reopened_store = SQLiteRoomStore(database_path)
    reopened_store.migrate()
    reopened = RoomRegistry(
        clock=FrozenClock(now),
        token_source=SequenceTokenSource(tokens=(), room_codes=()),
        seed_source=lambda: 999,
        store=reopened_store,
    )

    record = reopened.tokens.resolve(joined.seat_token)
    assert record.actor_type == "seat"
    assert record.room_id == created.room_id
    assert record.seat_id == 1
    assert reopened.tokens.active_seat_ids(created.room_id) == {1}
    reopened_store.close()


def test_expired_room_is_not_loaded_after_restart(tmp_path: Path) -> None:
    now = datetime(2026, 9, 24, tzinfo=UTC)
    database_path = tmp_path / "rooms.sqlite3"
    store = SQLiteRoomStore(database_path)
    store.migrate()
    clock = FrozenClock(now)
    registry = RoomRegistry(
        clock=clock,
        token_source=SequenceTokenSource(tokens=("host-token",), room_codes=("ROOM01",)),
        seed_source=lambda: 101,
        store=store,
    )
    created = registry.create_room(timedelta(hours=1))
    clock.set(now + timedelta(hours=2))
    store.close()

    reopened_store = SQLiteRoomStore(database_path)
    reopened_store.migrate()
    reopened = RoomRegistry(
        clock=clock,
        token_source=SequenceTokenSource(tokens=(), room_codes=()),
        seed_source=lambda: 999,
        store=reopened_store,
    )

    assert reopened.rooms == {}
    with pytest.raises(ValueError, match="ROOM_NOT_FOUND"):
        reopened.get_by_code(created.room_code)
    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        reopened.tokens.resolve(created.host_token)
    reopened_store.close()


def test_expired_token_is_not_loaded_after_restart(tmp_path: Path) -> None:
    now = datetime(2026, 9, 24, tzinfo=UTC)
    database_path = tmp_path / "rooms.sqlite3"
    store = SQLiteRoomStore(database_path)
    store.migrate()
    clock = FrozenClock(now)
    registry = RoomRegistry(
        clock=clock,
        token_source=SequenceTokenSource(
            tokens=("host-token", "seat-token"),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
        store=store,
    )
    created = registry.create_room(timedelta(hours=6))
    joined = registry.join_room(created.room_code, display_name="Alpha")
    clock.set(now + timedelta(hours=5))
    store.close()

    reopened_store = SQLiteRoomStore(database_path)
    reopened_store.migrate()
    reopened = RoomRegistry(
        clock=clock,
        token_source=SequenceTokenSource(tokens=(), room_codes=()),
        seed_source=lambda: 999,
        store=reopened_store,
    )

    assert reopened.get_by_code(created.room_code).room_id == created.room_id
    assert reopened.tokens.resolve(created.host_token).actor_type == "host"
    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        reopened.tokens.resolve(joined.seat_token)
    assert reopened.tokens.active_seat_ids(created.room_id) == set()
    reopened_store.close()


def test_room_activity_refresh_survives_registry_reopen(tmp_path: Path) -> None:
    now = datetime(2026, 9, 24, tzinfo=UTC)
    database_path = tmp_path / "rooms.sqlite3"
    store = SQLiteRoomStore(database_path)
    store.migrate()
    clock = FrozenClock(now)
    registry = RoomRegistry(
        clock=clock,
        token_source=SequenceTokenSource(tokens=("host-token",), room_codes=("ROOM01",)),
        seed_source=lambda: 101,
        store=store,
    )
    created = registry.create_room(timedelta(hours=6))
    refreshed_at = now + timedelta(hours=1)
    clock.set(refreshed_at)
    registry.get_by_code(created.room_code).touch()
    store.close()

    reopened_store = SQLiteRoomStore(database_path)
    reopened_store.migrate()
    reopened = RoomRegistry(
        clock=clock,
        token_source=SequenceTokenSource(tokens=(), room_codes=()),
        seed_source=lambda: 999,
        store=reopened_store,
    )

    assert reopened.get_by_code(created.room_code).last_activity_at == refreshed_at
    reopened_store.close()


def test_display_token_metadata_and_revoke_survive_registry_reopen(tmp_path: Path) -> None:
    now = datetime(2026, 9, 24, tzinfo=UTC)
    database_path = tmp_path / "rooms.sqlite3"
    store = SQLiteRoomStore(database_path)
    store.migrate()
    registry = RoomRegistry(
        clock=FrozenClock(now),
        token_source=SequenceTokenSource(
            tokens=("host-token", "display-token"),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
        store=store,
    )
    created = registry.create_room(timedelta(hours=6))
    pairing_code, _, _ = registry.create_display_pairing(created.room_code)
    _, display_token, _ = registry.exchange_display_pairing(
        created.room_code,
        pairing_code,
        source="127.0.0.1",
    )
    display_record = registry.tokens.resolve(display_token)
    store.close()

    reopened_store = SQLiteRoomStore(database_path)
    reopened_store.migrate()
    reopened = RoomRegistry(
        clock=FrozenClock(now),
        token_source=SequenceTokenSource(tokens=(), room_codes=()),
        seed_source=lambda: 999,
        store=reopened_store,
    )
    restored_actor = reopened.get_by_code(created.room_code)

    assert reopened.tokens.resolve(display_token).session_id == display_record.session_id
    assert restored_actor.display_token_digest == TokenService.digest(display_token)
    assert restored_actor.display_session_id == display_record.session_id

    reopened.revoke_display(created.room_code)
    reopened_store.close()

    revoked_store = SQLiteRoomStore(database_path)
    revoked_store.migrate()
    revoked = RoomRegistry(
        clock=FrozenClock(now),
        token_source=SequenceTokenSource(tokens=(), room_codes=()),
        seed_source=lambda: 999,
        store=revoked_store,
    )
    revoked_actor = revoked.get_by_code(created.room_code)

    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        revoked.tokens.resolve(display_token)
    assert revoked_actor.display_token_digest is None
    assert revoked_actor.display_session_id is None
    revoked_store.close()


@pytest.mark.asyncio
async def test_uuid_source_injection_controls_room_session_and_display_ids() -> None:
    ids = iter((UUID(int=101), UUID(int=102), UUID(int=103)))

    class Subscriber:
        def __init__(self) -> None:
            self.subscription_id = UUID(int=999)
            self.actor_type = "seat"
            self.seat_id = 1
            self.session_id = None
            self.channels = frozenset({"public", "seat"})
            self.close_codes: list[int] = []

        def offer(self, message: object) -> bool:
            del message
            return True

        def request_close(self, code: int) -> None:
            self.close_codes.append(code)

    registry = RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=("host-token", "display-token"),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
        uuid_source=lambda: next(ids),
    )

    created = registry.create_room(timedelta(hours=6))
    actor = registry.rooms[created.room_code]
    await actor.start()
    await actor.attach_subscriber(Subscriber())

    assert created.room_id == UUID(int=101)
    assert actor.seat_session_id(1) == UUID(int=102)

    display_token, _ = registry.tokens.issue_display(
        created.room_id,
        timedelta(hours=1),
    )
    assert registry.tokens.resolve(display_token).session_id == UUID(int=103)
    await actor.stop()


def test_create_room_limits_room_code_retries() -> None:
    class CountingRoomCodeSource:
        def __init__(self) -> None:
            self.calls = 0

        def token(self) -> str:
            return "host"

        def room_code(self) -> str:
            self.calls += 1
            if self.calls > 101:
                raise AssertionError("ROOM_CODE_RETRY_LIMIT_EXCEEDED")
            return "ROOM01"

    source = CountingRoomCodeSource()
    registry = RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=source,
        seed_source=lambda: 101,
    )
    registry.create_room(timedelta(hours=6))

    with pytest.raises(RuntimeError, match="ROOM_CODE_EXHAUSTED"):
        registry.create_room(timedelta(hours=6))
    assert source.calls == 101


def test_create_room_maps_exhausted_source_to_room_code_exhausted() -> None:
    registry = RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=("host",),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
    )
    registry.create_room(timedelta(hours=6))

    with pytest.raises(RuntimeError, match="ROOM_CODE_EXHAUSTED"):
        registry.create_room(timedelta(hours=6))


def test_join_room_assigns_next_seat_and_rejects_seventh() -> None:
    registry = RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=tuple(f"token-{seat}" for seat in range(1, 8)),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
    )
    created = registry.create_room(ttl=timedelta(hours=6))

    seats = [registry.join_room(created.room_code, display_name=f"P{seat}") for seat in range(1, 7)]

    assert [joined.seat_id for joined in seats] == [1, 2, 3, 4, 5, 6]
    with pytest.raises(ValueError, match="ROOM_FULL"):
        registry.join_room(created.room_code, display_name="P7")


def test_seat_occupancy_is_room_scoped_and_expired_seats_reopen() -> None:
    clock = FrozenClock(datetime(2026, 9, 24, tzinfo=UTC))
    registry = RoomRegistry(
        clock=clock,
        token_source=SequenceTokenSource(
            tokens=tuple(f"token-{index}" for index in range(1, 10)),
            room_codes=("ROOM01", "ROOM02"),
        ),
        seed_source=lambda: 101,
    )
    first = registry.create_room(timedelta(hours=6))
    second = registry.create_room(timedelta(hours=6))

    first_seat = registry.join_room(first.room_code, display_name="First")
    second_seat = registry.join_room(second.room_code, display_name="Second")

    assert (first_seat.seat_id, second_seat.seat_id) == (1, 1)
    first_token = registry.tokens.resolve(first_seat.seat_token)
    assert first_token.issued_at == datetime(2026, 9, 24, tzinfo=UTC)
    clock.set(first_token.expires_at)
    reused = registry.join_room(first.room_code, display_name="Reused")
    assert reused.seat_id == 1


async def test_reap_expired_removes_room_and_tokens() -> None:
    clock = FrozenClock(datetime(2026, 9, 24, tzinfo=UTC))
    registry = RoomRegistry(
        clock=clock,
        token_source=SequenceTokenSource(tokens=("host",), room_codes=("ROOM01",)),
        seed_source=lambda: 101,
    )
    created = registry.create_room(timedelta(hours=6))
    clock.set(created.expires_at)

    assert await registry.reap_expired() == 1
    with pytest.raises(ValueError, match="ROOM_NOT_FOUND"):
        registry.join_room(created.room_code, display_name="Late")
    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        registry.tokens.resolve(created.host_token)


async def test_room_activity_prevents_idle_reaping() -> None:
    clock = FrozenClock(datetime(2026, 9, 24, tzinfo=UTC))
    registry = RoomRegistry(
        clock=clock,
        token_source=SequenceTokenSource(
            tokens=("host", "seat"),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
    )
    created = registry.create_room(timedelta(hours=12))
    clock.set(datetime(2026, 9, 24, 5, tzinfo=UTC))
    registry.join_room(created.room_code, display_name="Active")
    clock.set(datetime(2026, 9, 24, 6, tzinfo=UTC))

    assert await registry.reap_expired() == 0
    assert registry.get_by_code(created.room_code).last_activity_at == datetime(
        2026,
        9,
        24,
        5,
        tzinfo=UTC,
    )


async def test_registry_helpers_are_frozen_in_s2_02() -> None:
    registry = RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=("host",),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
    )
    created = registry.create_room(timedelta(hours=6))
    actor = registry.actor_for(registry.tokens.resolve(created.host_token))

    assert registry.get_by_code(created.room_code).room_id == created.room_id
    assert registry.rooms_by_id()[created.room_id].room_id == created.room_id
    snapshot = registry.snapshot(actor, registry.tokens.resolve(created.host_token))
    assert snapshot.room_id == created.room_id
    assert snapshot.outbox_seq == 0
    assert snapshot.host_control is not None
    assert snapshot.host_control.revision == 0
    assert created.host_token not in registry.tokens._records
    assert registry.tokens.digest(created.host_token) in registry.tokens._records


def test_snapshot_rejects_actor_record_mismatch() -> None:
    registry = RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=("host-1", "host-2"),
            room_codes=("ROOM01", "ROOM02"),
        ),
        seed_source=lambda: 101,
    )
    first = registry.create_room(timedelta(hours=6))
    second = registry.create_room(timedelta(hours=6))
    first_actor = registry.actor_for(registry.tokens.resolve(first.host_token))
    second_record = registry.tokens.resolve(second.host_token)

    with pytest.raises(ValueError, match="ACTOR_NOT_AUTHORIZED"):
        registry.snapshot(first_actor, second_record)


async def test_room_actor_run_failure_fails_pending_futures() -> None:
    now = datetime(2026, 9, 24, tzinfo=UTC)
    actor = RoomActor(
        room_id=UUID(int=1),
        room_code="ROOM01",
        seed=101,
        clock=FrozenClock(now),
        expires_at=now + timedelta(hours=6),
        last_activity_at=now,
    )
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    actor._pending_futures.add(future)
    await actor.start()
    await actor.events.put(object())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="UNHANDLED_ROOM_EVENT"):
        await actor._task
    with pytest.raises(RuntimeError, match="UNHANDLED_ROOM_EVENT"):
        await future


async def test_remove_room_cleans_tokens_after_actor_failure() -> None:
    registry = RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(tokens=("host",), room_codes=("ROOM01",)),
        seed_source=lambda: 101,
    )
    created = registry.create_room(timedelta(hours=6))
    actor = registry.get_by_code(created.room_code)
    await actor.start()
    await actor.events.put(object())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="UNHANDLED_ROOM_EVENT"):
        await actor._task

    await registry.remove_room(created.room_code)

    with pytest.raises(ValueError, match="ROOM_NOT_FOUND"):
        registry.get_by_code(created.room_code)
    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        registry.tokens.resolve(created.host_token)


async def test_stop_propagates_live_handler_failure() -> None:
    class LiveFailingRoomActor(RoomActor):
        def __init__(self) -> None:
            now = datetime(2026, 9, 24, tzinfo=UTC)
            super().__init__(
                room_id=UUID(int=1),
                room_code="ROOM01",
                seed=101,
                clock=FrozenClock(now),
                expires_at=now + timedelta(hours=6),
                last_activity_at=now,
            )
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.stop_entered = asyncio.Event()

        async def _run_loop(self) -> None:
            self.started.set()
            await self.release.wait()
            raise RuntimeError("LIVE_HANDLER_FAILURE")

        async def stop(self) -> None:
            self.stop_entered.set()
            await super().stop()

    actor = LiveFailingRoomActor()
    await actor.start()
    await actor.started.wait()
    stop_task = asyncio.create_task(actor.stop())
    await actor.stop_entered.wait()
    actor.release.set()

    with pytest.raises(RuntimeError, match="LIVE_HANDLER_FAILURE"):
        await stop_task


async def test_remove_room_cleans_tokens_after_cancel() -> None:
    registry = RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(tokens=("host",), room_codes=("ROOM01",)),
        seed_source=lambda: 101,
    )
    created = registry.create_room(timedelta(hours=6))
    actor = registry.get_by_code(created.room_code)
    await actor.start()
    actor._task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await actor._task

    await registry.remove_room(created.room_code)

    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        registry.tokens.resolve(created.host_token)


@pytest.mark.asyncio
async def test_remove_room_deletes_room_state_and_tokens(tmp_path: Path) -> None:
    store = SQLiteRoomStore(tmp_path / "rooms.sqlite3")
    store.migrate()
    registry = RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=("host", "seat"),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
        store=store,
    )
    created = registry.create_room(timedelta(hours=6))
    registry.join_room(created.room_code, display_name="Alpha")

    await registry.remove_room(created.room_code)

    assert store.load_rooms() == ()
    assert store.load_tokens() == ()
    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        registry.tokens.resolve(created.host_token)
    store.close()
