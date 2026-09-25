from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

from tests.factories import (
    core_at_role_reveal,
    core_at_wolf,
    host_actor,
    make_envelope,
)
from werewolf_dm.application.core import FrozenClock, GameCore
from werewolf_dm.application.rooms import (
    RoomActor,
    RoomClosedError,
    RoomRegistry,
    SequenceTokenSource,
    TimerScheduler,
)
from werewolf_dm.domain.contracts import HostPauseCommand, HostResumeCommand
from werewolf_dm.domain.enums import Phase
from werewolf_dm.interfaces.http_ws.app import create_app
from werewolf_dm.interfaces.http_ws.runtime import RealClock


def make_registry() -> RoomRegistry:
    return RoomRegistry(
        clock=FrozenClock(datetime(2026, 9, 24, tzinfo=UTC)),
        token_source=SequenceTokenSource(
            tokens=("host", "seat-1"),
            room_codes=("ROOM01",),
        ),
        seed_source=lambda: 101,
    )


def make_actor(core: GameCore) -> RoomActor:
    clock = cast(FrozenClock, core.clock)
    return RoomActor(
        room_id=core.state.room_id,
        room_code="ROOM01",
        seed=core.seed,
        clock=clock,
        expires_at=clock() + timedelta(hours=1),
        last_activity_at=clock(),
        core=core,
    )


@asynccontextmanager
async def running_actor(core: GameCore) -> AsyncIterator[RoomActor]:
    actor = make_actor(core)
    await actor.start()
    try:
        yield actor
    finally:
        await actor.stop()


async def settle_actor_queue(actor: RoomActor) -> None:
    state = actor.core.state
    payload = HostPauseCommand(reason="barrier") if state.paused else HostResumeCommand()
    envelope = make_envelope(
        host_actor(),
        payload,
        expected_revision=state.revision,
        now=actor.clock(),
    )
    await actor.submit_command(envelope, host_actor())


def test_real_clock_is_utc_and_set_is_a_noop() -> None:
    clock = RealClock()

    before = clock()
    ignored = before + timedelta(days=1)
    clock.set(ignored)
    after = clock()

    assert before.tzinfo is UTC
    assert after.tzinfo is UTC
    assert before <= after < ignored


def test_create_app_lifespan_injects_production_registry_only_when_missing() -> None:
    production_app = create_app()
    assert production_app.state.room_registry is None

    with TestClient(production_app):
        production_registry = cast(RoomRegistry, production_app.state.room_registry)
        assert isinstance(production_registry, RoomRegistry)
        assert isinstance(production_registry.clock, RealClock)

    provided = make_registry()
    provided_app = create_app(provided)
    with TestClient(provided_app):
        assert provided_app.state.room_registry is provided


def test_create_app_lifespan_recreates_owned_registry_and_preserves_injected() -> None:
    owned_app = create_app()

    with TestClient(owned_app) as client:
        first_registry = cast(RoomRegistry, owned_app.state.room_registry)
        created = client.post("/rooms", json={"display_name": "Host"})
        assert created.status_code == 201
        assert first_registry.rooms

    assert first_registry.rooms == {}
    assert owned_app.state.room_registry is None

    with TestClient(owned_app) as client:
        second_registry = cast(RoomRegistry, owned_app.state.room_registry)
        assert second_registry is not first_registry
        assert second_registry.rooms == {}
        assert client.get("/healthz").status_code == 200

    provided = make_registry()
    provided_app = create_app(provided)
    with TestClient(provided_app):
        assert provided_app.state.room_registry is provided
    with TestClient(provided_app):
        assert provided_app.state.room_registry is provided


@pytest.mark.asyncio
async def test_actor_timer_task_and_explicit_tick_reach_game_core_timeout() -> None:
    core = core_at_wolf().core
    clock = cast(FrozenClock, core.clock)

    async with running_actor(core) as actor:
        assert actor._timer_task is not None
        assert not actor._timer_task.done()

        before = core.state
        assert before.deadline_at is not None
        clock.set(before.deadline_at)

        await actor.enqueue_timer_tick(
            revision=before.revision,
            deadline_at=before.deadline_at,
            now=before.deadline_at,
        )
        await settle_actor_queue(actor)

        after = core.state
        assert after.phase is Phase.NIGHT_SEER
        assert after.revision > before.revision


@pytest.mark.asyncio
async def test_pause_suppresses_explicit_timer_tick() -> None:
    core = core_at_role_reveal()
    clock = cast(FrozenClock, core.clock)

    async with running_actor(core) as actor:
        clock.set(clock() + timedelta(seconds=10))
        paused = await actor.submit_command(
            make_envelope(
                host_actor(),
                HostPauseCommand(reason="break"),
                expected_revision=core.state.revision,
                now=clock(),
            ),
            host_actor(),
        )
        assert paused.accepted is True
        paused_state = core.state
        assert paused_state.paused is True
        assert paused_state.deadline_at is not None

        clock.set(paused_state.deadline_at)
        await actor.enqueue_timer_tick(
            revision=paused_state.revision,
            deadline_at=paused_state.deadline_at,
            now=paused_state.deadline_at,
        )
        await settle_actor_queue(actor)

        assert core.state.revision == paused_state.revision
        assert core.state.deadline_at == paused_state.deadline_at
        assert core.state.phase is Phase.ROLE_REVEAL


@pytest.mark.asyncio
async def test_resume_shifts_deadline_and_shifted_tick_advances() -> None:
    core = core_at_role_reveal()
    clock = cast(FrozenClock, core.clock)

    async with running_actor(core) as actor:
        clock.set(clock() + timedelta(seconds=10))
        paused = await actor.submit_command(
            make_envelope(
                host_actor(),
                HostPauseCommand(reason="break"),
                expected_revision=core.state.revision,
                now=clock(),
            ),
            host_actor(),
        )
        assert paused.accepted is True
        old_deadline = core.state.deadline_at
        assert old_deadline is not None

        clock.set(clock() + timedelta(seconds=30))
        resumed = await actor.submit_command(
            make_envelope(
                host_actor(),
                HostResumeCommand(),
                expected_revision=core.state.revision,
                now=clock(),
            ),
            host_actor(),
        )
        assert resumed.accepted is True
        shifted_deadline = core.state.deadline_at
        assert shifted_deadline == old_deadline + timedelta(seconds=30)
        resumed_revision = core.state.revision

        await actor.enqueue_timer_tick(
            revision=resumed_revision,
            deadline_at=old_deadline,
            now=clock(),
        )
        await settle_actor_queue(actor)
        assert core.state.revision == resumed_revision
        assert core.state.phase is Phase.ROLE_REVEAL

        assert shifted_deadline is not None
        clock.set(shifted_deadline)
        await actor.enqueue_timer_tick(
            revision=resumed_revision,
            deadline_at=shifted_deadline,
            now=shifted_deadline,
        )
        await settle_actor_queue(actor)

        assert core.state.phase is Phase.NIGHT_WOLF
        assert core.state.revision > resumed_revision


@pytest.mark.asyncio
async def test_stale_revision_tick_is_discarded() -> None:
    core = core_at_role_reveal()
    clock = cast(FrozenClock, core.clock)

    async with running_actor(core) as actor:
        before = core.state
        assert before.deadline_at is not None
        clock.set(before.deadline_at)

        await actor.enqueue_timer_tick(
            revision=before.revision - 1,
            deadline_at=before.deadline_at,
            now=before.deadline_at,
        )
        await settle_actor_queue(actor)

        assert core.state.revision == before.revision
        assert core.state.phase is Phase.ROLE_REVEAL


class RaceEvent(asyncio.Event):
    def __init__(self, actor: RaceActor) -> None:
        super().__init__()
        self.actor = actor

    def clear(self) -> None:
        super().clear()
        if not self.actor.raced:
            self.actor.raced = True
            self.actor.deadline_at = self.actor.changed_deadline


class RaceActor:
    def __init__(self, clock: FrozenClock) -> None:
        self.clock = clock
        self.closed = False
        self.deadline_at: datetime | None = None
        self.changed_deadline = clock()
        self.raced = False
        self.ticks: list[tuple[int, datetime, datetime]] = []
        self.tick_seen = asyncio.Event()
        self.core = SimpleNamespace(state=SimpleNamespace(paused=False, revision=0))
        self.deadline_changed = RaceEvent(self)

    def current_deadline(self) -> datetime | None:
        return self.deadline_at

    async def enqueue_timer_tick(
        self,
        revision: int,
        deadline_at: datetime,
        now: datetime,
    ) -> None:
        self.ticks.append((revision, deadline_at, now))
        self.tick_seen.set()
        self.closed = True


@pytest.mark.asyncio
async def test_scheduler_rereads_deadline_changed_during_clear() -> None:
    clock = FrozenClock(datetime(2026, 9, 24, tzinfo=UTC))
    actor = RaceActor(clock)
    scheduler = TimerScheduler(cast(RoomActor, actor), clock)

    task = asyncio.create_task(scheduler.run())
    await asyncio.wait_for(actor.tick_seen.wait(), timeout=0.5)
    await task

    assert actor.raced is True
    assert actor.ticks == [(0, actor.changed_deadline, clock())]


@pytest.mark.asyncio
async def test_actor_cancelled_before_first_scheduling_closes_lifecycle() -> None:
    actor = make_actor(core_at_role_reveal())
    await actor.start()
    assert actor._task is not None
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    actor._pending_futures.add(future)

    actor._task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await actor._task

        assert actor.closed is True
        with pytest.raises(RoomClosedError):
            await asyncio.wait_for(future, timeout=0.5)
        assert actor._timer_task is not None
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(actor._timer_task, timeout=0.5)
        assert actor._timer_task.done()
    finally:
        timer_task = actor._timer_task
        if timer_task is not None and not timer_task.done():
            timer_task.cancel()
            with suppress(asyncio.CancelledError):
                await timer_task


class FailingTimerActor(RoomActor):
    def __init__(self) -> None:
        core = core_at_role_reveal()
        clock = cast(FrozenClock, core.clock)
        super().__init__(
            room_id=core.state.room_id,
            room_code="ROOM01",
            seed=core.seed,
            clock=clock,
            expires_at=clock() + timedelta(hours=1),
            last_activity_at=clock(),
            core=core,
        )
        self.release = asyncio.Event()

    async def _run_loop(self) -> None:
        await self.release.wait()
        raise RuntimeError("ACTOR_FAILURE")


@pytest.mark.asyncio
async def test_actor_failure_terminates_waiting_timer_task() -> None:
    actor = FailingTimerActor()
    await actor.start()
    actor.release.set()

    with pytest.raises(RuntimeError, match="ACTOR_FAILURE"):
        assert actor._task is not None
        await actor._task

    assert actor.closed is True
    assert actor._timer_task is not None
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(actor._timer_task, timeout=0.5)
    assert actor._timer_task.done()


class CancelSignalTimer:
    def __init__(self) -> None:
        self.cancel_called = asyncio.Event()
        self._task = asyncio.create_task(asyncio.Event().wait())

    def cancel(self) -> None:
        self.cancel_called.set()
        self._task.cancel()

    def __await__(self) -> object:
        return self._task.__await__()

    def done(self) -> bool:
        return self._task.done()


@pytest.mark.asyncio
async def test_stop_preserves_caller_cancellation() -> None:
    actor = make_actor(core_at_role_reveal())
    await actor.start()
    assert actor._timer_task is not None
    actor._timer_task.cancel()
    with suppress(asyncio.CancelledError):
        await actor._timer_task
    timer = CancelSignalTimer()
    actor._timer_task = cast(asyncio.Task[None], timer)

    stop_task = asyncio.create_task(actor.stop())
    try:
        await timer.cancel_called.wait()
        stop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop_task
    finally:
        if actor._task is not None and not actor._task.done():
            await actor.stop()
