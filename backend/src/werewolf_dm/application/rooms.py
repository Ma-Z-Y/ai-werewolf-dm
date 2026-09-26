from __future__ import annotations

import asyncio
import contextlib
import hashlib
import secrets
import string
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from werewolf_dm.application.core import Clock, GameCore
from werewolf_dm.domain.contracts import (
    ActorType,
    AuthenticatedActor,
    CommandEnvelope,
    CommandErrorCode,
)
from werewolf_dm.domain.model import StrictModel
from werewolf_dm.domain.visibility import (
    PublicView,
    SeatView,
    project_public_view,
    project_seat_view,
)


class TokenSource(Protocol):
    def token(self) -> str:
        raise NotImplementedError

    def room_code(self) -> str:
        raise NotImplementedError


class SecretsTokenSource:
    def token(self) -> str:
        return secrets.token_urlsafe(32)

    def room_code(self) -> str:
        alphabet = string.ascii_uppercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(6))


class SequenceTokenSource:
    def __init__(self, tokens: tuple[str, ...], room_codes: tuple[str, ...]) -> None:
        self._tokens = iter(tokens)
        self._room_codes = iter(room_codes)

    def token(self) -> str:
        return next(self._tokens)

    def room_code(self) -> str:
        return next(self._room_codes)


class TokenRecord(StrictModel):
    token_digest: str
    room_id: UUID
    actor_type: ActorType
    seat_id: int | None = Field(default=None, ge=1, le=6)
    session_id: UUID | None = None
    issued_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_actor_session_shape(self) -> Self:
        if self.actor_type == "seat":
            if self.seat_id is None:
                raise ValueError("seat token requires seat_id")
            if self.session_id is not None:
                raise ValueError("seat token cannot carry session_id")
        elif self.actor_type == "host":
            if self.seat_id is not None:
                raise ValueError("host token cannot carry seat_id")
            if self.session_id is not None:
                raise ValueError("host token cannot carry session_id")
        else:
            if self.seat_id is not None:
                raise ValueError("display token cannot carry seat_id")
            if self.session_id is None:
                raise ValueError("display token requires session_id")
        return self


@dataclass(slots=True)
class DisplayPairing:
    pairing_code_digest: str
    room_id: UUID
    expires_at: datetime
    failed_attempts: int = 0


def can_subscribe(
    record: TokenRecord,
    channel: str,
    requested_seat: int | None,
) -> bool:
    if channel == "public":
        return True
    return (
        channel == "seat"
        and record.actor_type == "seat"
        and record.seat_id is not None
        and requested_seat == record.seat_id
    )


class RoomClosedError(RuntimeError):
    pass


class RoomNotStartedError(RuntimeError):
    pass


class CreatedRoom(StrictModel):
    room_id: UUID
    room_code: str
    host_token: str
    expires_at: datetime


class JoinedRoom(StrictModel):
    room_id: UUID
    room_code: str
    seat_id: int
    seat_token: str
    expires_at: datetime


class HostControlView(StrictModel):
    public_view: PublicView
    paused: bool
    revision: int


class RoomSnapshot(StrictModel):
    room_id: UUID
    room_code: str
    revision: int
    outbox_seq: int
    public_view: PublicView
    seat_view: SeatView | None = None
    host_control: HostControlView | None = None


class PublicViewUpdate(StrictModel):
    type: Literal["public.view.updated"] = "public.view.updated"
    server_time: datetime
    outbox_seq: int
    public_view: PublicView


class SeatViewUpdate(StrictModel):
    type: Literal["seat.view.updated"] = "seat.view.updated"
    server_time: datetime
    outbox_seq: int
    seat_id: int = Field(ge=1, le=6)
    seat_view: SeatView


class HostControlUpdate(StrictModel):
    type: Literal["host.control.updated"] = "host.control.updated"
    server_time: datetime
    outbox_seq: int
    host_control: HostControlView


class SessionReadyUpdate(StrictModel):
    type: Literal["session.ready"] = "session.ready"
    server_time: datetime
    snapshot: RoomSnapshot


RoomUpdate = SessionReadyUpdate | PublicViewUpdate | SeatViewUpdate | HostControlUpdate


class RoomSubscriber(Protocol):
    subscription_id: UUID
    actor_type: ActorType
    seat_id: int | None
    session_id: UUID | None
    channels: frozenset[str]

    def offer(self, message: RoomUpdate) -> bool:
        raise NotImplementedError

    def request_close(self, code: int) -> None:
        raise NotImplementedError


class RoomCommandAck(StrictModel):
    command_id: UUID
    accepted: bool
    revision: int
    error_code: CommandErrorCode | None = None
    outbox_seq: int


@dataclass(slots=True)
class SubmitCommandEvent:
    envelope: CommandEnvelope
    actor: AuthenticatedActor
    result: asyncio.Future[RoomCommandAck]


@dataclass(slots=True)
class TimerTickEvent:
    revision: int
    deadline_at: datetime
    now: datetime


@dataclass(slots=True)
class SyncDisplaySessionEvent:
    active_session_id: UUID | None


@dataclass(slots=True)
class AttachSubscriberEvent:
    subscriber: RoomSubscriber
    result: asyncio.Future[None]
    initial: bool


@dataclass(slots=True)
class DetachSubscriberEvent:
    subscription_id: UUID


@dataclass(slots=True)
class StopEvent:
    pass


RoomEvent = (
    SubmitCommandEvent
    | TimerTickEvent
    | SyncDisplaySessionEvent
    | AttachSubscriberEvent
    | DetachSubscriberEvent
    | StopEvent
)


class TimerScheduler:
    def __init__(self, actor: RoomActor, clock: Clock) -> None:
        self.actor = actor
        self.clock = clock

    async def run(self) -> None:
        while not self.actor.closed:
            deadline = self.actor.current_deadline()
            paused = self.actor.core.state.paused
            self.actor.deadline_changed.clear()
            if deadline != self.actor.current_deadline() or paused != self.actor.core.state.paused:
                continue
            if deadline is None or paused:
                await self.actor.deadline_changed.wait()
                continue
            delay = max(0.0, (deadline - self.clock()).total_seconds())
            try:
                await asyncio.wait_for(
                    self.actor.deadline_changed.wait(),
                    timeout=delay,
                )
                continue
            except TimeoutError:
                await self.actor.enqueue_timer_tick(
                    revision=self.actor.core.state.revision,
                    deadline_at=deadline,
                    now=self.clock(),
                )


class TokenService:
    def __init__(self, source: TokenSource, clock: Clock) -> None:
        self.source = source
        self.clock = clock
        self._records: dict[str, TokenRecord] = {}
        self._seat_records: dict[UUID, dict[int, TokenRecord]] = {}
        self._display_records: dict[UUID, TokenRecord] = {}

    @staticmethod
    def digest(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    def issue_host(self, room_id: UUID, ttl: timedelta) -> tuple[str, datetime]:
        raw = self.source.token()
        issued_at = self.clock()
        expires_at = issued_at + ttl
        self._records[self.digest(raw)] = TokenRecord(
            token_digest=self.digest(raw),
            room_id=room_id,
            actor_type="host",
            issued_at=issued_at,
            expires_at=expires_at,
        )
        return raw, expires_at

    def issue_seat(
        self,
        room_id: UUID,
        seat_id: int,
        ttl: timedelta,
    ) -> tuple[str, datetime]:
        raw = self.source.token()
        issued_at = self.clock()
        expires_at = issued_at + ttl
        record = TokenRecord(
            token_digest=self.digest(raw),
            room_id=room_id,
            actor_type="seat",
            seat_id=seat_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        self._records[record.token_digest] = record
        self._seat_records.setdefault(room_id, {})[seat_id] = record
        return raw, expires_at

    def resolve(self, raw_token: str) -> TokenRecord:
        record = self._records.get(self.digest(raw_token))
        if record is None:
            raise ValueError("TOKEN_INVALID")
        if record.expires_at <= self.clock():
            raise ValueError("TOKEN_EXPIRED")
        return record

    def active_seat_ids(self, room_id: UUID) -> set[int]:
        return {
            seat_id
            for seat_id, record in self._seat_records.get(room_id, {}).items()
            if record.expires_at > self.clock()
        }

    def issue_display(
        self,
        room_id: UUID,
        ttl: timedelta,
        *,
        room_expires_at: datetime | None = None,
    ) -> tuple[str, datetime]:
        issued_at = self.clock()
        if room_expires_at is not None and room_expires_at <= issued_at:
            raise ValueError("ROOM_NOT_FOUND")
        raw = self.source.token()
        expires_at = issued_at + ttl
        if room_expires_at is not None:
            expires_at = min(expires_at, room_expires_at)
        record = TokenRecord(
            token_digest=self.digest(raw),
            room_id=room_id,
            actor_type="display",
            session_id=uuid4(),
            issued_at=issued_at,
            expires_at=expires_at,
        )
        previous = self._display_records.get(room_id)
        self._records[record.token_digest] = record
        self._display_records[room_id] = record
        if previous is not None:
            self._records.pop(previous.token_digest, None)
        return raw, expires_at

    def display_record(self, room_id: UUID) -> TokenRecord | None:
        return self._display_records.get(room_id)

    def revoke_display(self, room_id: UUID) -> None:
        record = self._display_records.pop(room_id, None)
        if record is not None:
            self._records.pop(record.token_digest, None)

    def remove_room(self, room_id: UUID) -> None:
        for digest, record in tuple(self._records.items()):
            if record.room_id == room_id:
                del self._records[digest]
        self._seat_records.pop(room_id, None)
        self._display_records.pop(room_id, None)


class RoomActor:
    def __init__(
        self,
        room_id: UUID,
        room_code: str,
        seed: int,
        clock: Clock,
        expires_at: datetime,
        last_activity_at: datetime,
        core: GameCore | None = None,
    ) -> None:
        self.room_id = room_id
        self.room_code = room_code
        self.clock = clock
        self.expires_at = expires_at
        self.last_activity_at = last_activity_at
        self.core = core or GameCore(room_id=room_id, seed=seed, clock=clock)
        self.events: asyncio.Queue[RoomEvent] = asyncio.Queue()
        self.subscribers: dict[UUID, RoomSubscriber] = {}
        self.outbox_seq = 0
        self.closed = False
        self._started = False
        self._task: asyncio.Task[None] | None = None
        self._timer_task: asyncio.Task[None] | None = None
        self._pending_futures: set[asyncio.Future[None] | asyncio.Future[RoomCommandAck]] = set()
        self.deadline_changed = asyncio.Event()
        self.display_token_digest: str | None = None
        self.display_session_id: UUID | None = None

    def current_deadline(self) -> datetime | None:
        return self.core.state.deadline_at

    def sync_display_session(self) -> None:
        self.events.put_nowait(SyncDisplaySessionEvent(active_session_id=self.display_session_id))

    def touch(self, *, now: datetime | None = None) -> None:
        self.last_activity_at = self.clock() if now is None else now

    def _reject_pending_futures(self, exc: BaseException) -> None:
        failure = RoomClosedError("ROOM_CLOSED") if isinstance(exc, asyncio.CancelledError) else exc
        for future in tuple(self._pending_futures):
            if future.done():
                continue
            future.set_exception(failure)

    def _close(self, exc: BaseException) -> None:
        self.closed = True
        for subscriber in tuple(self.subscribers.values()):
            with contextlib.suppress(Exception):
                subscriber.request_close(4001)
        self.subscribers.clear()
        self.deadline_changed.set()
        self._reject_pending_futures(exc)
        if self._timer_task is not None:
            self._timer_task.cancel()

    def _on_actor_task_done(self, task: asyncio.Task[None]) -> None:
        del task
        self._close(RoomClosedError("ROOM_CLOSED"))

    async def start(self) -> None:
        if self.closed:
            raise RoomClosedError("ROOM_CLOSED")
        if self._started:
            return
        self._started = True
        if self._task is None:
            task = asyncio.create_task(self.run())
            self._task = task
            task.add_done_callback(self._on_actor_task_done)
        if self._timer_task is None:
            self._timer_task = asyncio.create_task(TimerScheduler(self, self.clock).run())

    async def _cancel_timer_task(self) -> None:
        timer_task = self._timer_task
        if timer_task is None:
            return
        timer_task.cancel()
        try:
            await asyncio.shield(timer_task)
        except asyncio.CancelledError:
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling():
                raise

    async def stop(self) -> None:
        task_was_done = self._task is not None and self._task.done()
        self._close(RoomClosedError("ROOM_CLOSED"))
        await self.events.put(StopEvent())
        await self._cancel_timer_task()
        if self._task is not None:
            if task_was_done:
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    self._task.result()
            else:
                await self._task

    async def enqueue_timer_tick(
        self,
        revision: int,
        deadline_at: datetime,
        now: datetime,
    ) -> None:
        if not self._started:
            raise RoomNotStartedError("ROOM_NOT_STARTED")
        await self.events.put(
            TimerTickEvent(
                revision=revision,
                deadline_at=deadline_at,
                now=now,
            )
        )

    async def submit_command(
        self,
        envelope: CommandEnvelope,
        actor: AuthenticatedActor,
    ) -> RoomCommandAck:
        if self.closed:
            raise RoomClosedError("ROOM_CLOSED")
        if not self._started:
            raise RoomNotStartedError("ROOM_NOT_STARTED")
        future: asyncio.Future[RoomCommandAck] = asyncio.get_running_loop().create_future()
        self._pending_futures.add(future)
        await self.events.put(
            SubmitCommandEvent(
                envelope=envelope,
                actor=actor,
                result=future,
            )
        )
        try:
            return await future
        finally:
            self._pending_futures.discard(future)

    async def attach_subscriber(self, subscriber: RoomSubscriber) -> None:
        if self.closed:
            raise RoomClosedError("ROOM_CLOSED")
        if not self._started:
            raise RoomNotStartedError("ROOM_NOT_STARTED")
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._pending_futures.add(future)
        await self.events.put(
            AttachSubscriberEvent(
                subscriber=subscriber,
                result=future,
                initial=True,
            )
        )
        try:
            await future
        finally:
            self._pending_futures.discard(future)

    async def publish_current(self, subscriber: RoomSubscriber) -> None:
        if self.closed:
            raise RoomClosedError("ROOM_CLOSED")
        if not self._started:
            raise RoomNotStartedError("ROOM_NOT_STARTED")
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._pending_futures.add(future)
        await self.events.put(
            AttachSubscriberEvent(
                subscriber=subscriber,
                result=future,
                initial=False,
            )
        )
        try:
            await future
        finally:
            self._pending_futures.discard(future)

    async def detach_subscriber(self, subscription_id: UUID) -> None:
        await self.events.put(DetachSubscriberEvent(subscription_id=subscription_id))

    async def run(self) -> None:
        try:
            await self._run_loop()
        except BaseException as exc:
            self._close(exc)
            await self._cancel_timer_task()
            raise
        else:
            self._close(RoomClosedError("ROOM_CLOSED"))

    async def _run_loop(self) -> None:
        while True:
            event = await self.events.get()
            if isinstance(event, StopEvent):
                return
            if isinstance(event, SubmitCommandEvent):
                revision_before = self.core.state.revision
                result = self.core.submit(event.envelope, event.actor)
                if result.accepted and result.revision > revision_before:
                    self.touch()
                    self._publish_updates()
                if not event.result.done():
                    event.result.set_result(
                        RoomCommandAck(
                            command_id=result.command_id,
                            accepted=result.accepted,
                            revision=result.revision,
                            error_code=result.error_code,
                            outbox_seq=self.outbox_seq,
                        )
                    )
                self.deadline_changed.set()
                continue
            if isinstance(event, TimerTickEvent):
                if (
                    event.revision == self.core.state.revision
                    and event.deadline_at == self.core.state.deadline_at
                    and not self.core.state.paused
                ):
                    results = self.core.tick()
                    if any(result.accepted for result in results):
                        self.touch()
                        self._publish_updates()
                    self.deadline_changed.set()
                continue
            if isinstance(event, SyncDisplaySessionEvent):
                for subscription_id, subscriber in tuple(self.subscribers.items()):
                    if (
                        subscriber.actor_type == "display"
                        and subscriber.session_id != event.active_session_id
                    ):
                        self.subscribers.pop(subscription_id, None)
                        subscriber.request_close(4001)
                continue
            if isinstance(event, AttachSubscriberEvent):
                subscriber = event.subscriber
                if (
                    subscriber.actor_type == "display"
                    and subscriber.session_id != self.display_session_id
                ):
                    subscriber.request_close(4001)
                    if not event.result.done():
                        event.result.set_result(None)
                    continue
                now = self.clock()
                self.touch(now=now)
                identity = self._subscriber_identity(subscriber)
                for subscription_id, existing in tuple(self.subscribers.items()):
                    if subscription_id == subscriber.subscription_id:
                        continue
                    if self._subscriber_identity(existing) == identity:
                        self.subscribers.pop(subscription_id, None)
                        existing.request_close(4003)
                self.subscribers[subscriber.subscription_id] = subscriber
                if event.initial:
                    subscriber.offer(
                        SessionReadyUpdate(
                            server_time=now,
                            snapshot=self._snapshot_for(subscriber),
                        )
                    )
                else:
                    self._publish_current_to(subscriber, server_time=now)
                if not event.result.done():
                    event.result.set_result(None)
                continue
            if isinstance(event, DetachSubscriberEvent):
                self.touch()
                self.subscribers.pop(event.subscription_id, None)
                continue
            raise RuntimeError("UNHANDLED_ROOM_EVENT")

    @staticmethod
    def _subscriber_identity(
        subscriber: RoomSubscriber,
    ) -> tuple[ActorType, int | None, UUID | None]:
        return subscriber.actor_type, subscriber.seat_id, subscriber.session_id

    def _publish_updates(self) -> None:
        self.outbox_seq += 1
        server_time = self.clock()
        for subscription_id, subscriber in tuple(self.subscribers.items()):
            if (
                subscriber.actor_type == "display"
                and subscriber.session_id != self.display_session_id
            ):
                self.subscribers.pop(subscription_id, None)
                subscriber.request_close(4001)
                continue
            self._publish_current_to(subscriber, server_time=server_time)

    def _snapshot_for(self, subscriber: RoomSubscriber) -> RoomSnapshot:
        public = project_public_view(self.core.state)
        seat_view = None
        if (
            subscriber.actor_type == "seat"
            and subscriber.seat_id is not None
            and "seat" in subscriber.channels
        ):
            seat_view = project_seat_view(
                self.core.state,
                subscriber.seat_id,
                AuthenticatedActor(
                    actor_type="seat",
                    seat_id=subscriber.seat_id,
                    room_id=self.room_id,
                ),
            )
        host_control = None
        if subscriber.actor_type == "host" and "host.control" in subscriber.channels:
            host_control = HostControlView(
                public_view=public,
                paused=self.core.state.paused,
                revision=self.core.state.revision,
            )
        return RoomSnapshot(
            room_id=self.room_id,
            room_code=self.room_code,
            revision=self.core.state.revision,
            outbox_seq=self.outbox_seq,
            public_view=public,
            seat_view=seat_view,
            host_control=host_control,
        )

    def _publish_current_to(
        self,
        subscriber: RoomSubscriber,
        *,
        server_time: datetime,
    ) -> None:
        if subscriber.actor_type == "display" and subscriber.session_id != self.display_session_id:
            subscriber.request_close(4001)
            return
        public = project_public_view(self.core.state)
        if "public" in subscriber.channels:
            subscriber.offer(
                PublicViewUpdate(
                    server_time=server_time,
                    outbox_seq=self.outbox_seq,
                    public_view=public,
                )
            )
        if (
            subscriber.actor_type == "seat"
            and subscriber.seat_id is not None
            and "seat" in subscriber.channels
        ):
            subscriber.offer(
                SeatViewUpdate(
                    server_time=server_time,
                    outbox_seq=self.outbox_seq,
                    seat_id=subscriber.seat_id,
                    seat_view=project_seat_view(
                        self.core.state,
                        subscriber.seat_id,
                        AuthenticatedActor(
                            actor_type="seat",
                            seat_id=subscriber.seat_id,
                            room_id=self.room_id,
                        ),
                    ),
                )
            )
        if subscriber.actor_type == "host" and "host.control" in subscriber.channels:
            subscriber.offer(
                HostControlUpdate(
                    server_time=server_time,
                    outbox_seq=self.outbox_seq,
                    host_control=HostControlView(
                        public_view=public,
                        paused=self.core.state.paused,
                        revision=self.core.state.revision,
                    ),
                )
            )


class RoomRegistry:
    def __init__(
        self,
        clock: Clock,
        token_source: TokenSource,
        seed_source: Callable[[], int],
        *,
        max_rooms: int = 256,
        max_connections: int = 1024,
    ) -> None:
        if max_rooms <= 0 or max_connections <= 0:
            raise ValueError("ROOM_LIMIT_INVALID")
        self.clock = clock
        self.tokens = TokenService(token_source, clock)
        self.seed_source = seed_source
        self.max_rooms = max_rooms
        self.max_connections = max_connections
        self.rooms: dict[str, RoomActor] = {}
        self.active_connections = 0
        self.auth_failures = 0
        self.slow_connection_closes = 0
        self._display_pairings: dict[UUID, DisplayPairing] = {}
        self._pairing_attempts: dict[tuple[UUID, str], tuple[datetime, int]] = {}

    def active_connection_count(self) -> int:
        return self.active_connections

    def connection_opened(self) -> bool:
        if self.active_connections >= self.max_connections:
            return False
        self.active_connections += 1
        return True

    def connection_closed(self, *, slow: bool = False) -> None:
        self.active_connections = max(0, self.active_connections - 1)
        if slow:
            self.slow_connection_closes += 1

    def get_by_code(self, room_code: str) -> RoomActor:
        actor = self.rooms.get(room_code)
        if actor is None or actor.expires_at <= self.clock():
            raise ValueError("ROOM_NOT_FOUND")
        return actor

    def rooms_by_id(self) -> dict[UUID, RoomActor]:
        now = self.clock()
        return {actor.room_id: actor for actor in self.rooms.values() if actor.expires_at > now}

    def actor_for(self, record: TokenRecord) -> AuthenticatedActor:
        return AuthenticatedActor(
            actor_type=record.actor_type,
            seat_id=record.seat_id,
            room_id=record.room_id,
        )

    def snapshot(
        self,
        actor: AuthenticatedActor,
        record: TokenRecord,
    ) -> RoomSnapshot:
        actor = AuthenticatedActor.revalidate(actor)
        if self.tokens._records.get(record.token_digest) != record:
            raise ValueError("TOKEN_INVALID")
        if record.expires_at <= self.clock():
            raise ValueError("TOKEN_EXPIRED")
        if (
            actor.room_id != record.room_id
            or actor.actor_type != record.actor_type
            or actor.seat_id != record.seat_id
        ):
            raise ValueError("ACTOR_NOT_AUTHORIZED")
        room = self.rooms_by_id().get(record.room_id)
        if room is None:
            raise ValueError("ROOM_NOT_FOUND")
        public_view = project_public_view(room.core.state)
        seat_view = (
            project_seat_view(room.core.state, actor.seat_id, actor)
            if actor.actor_type == "seat" and actor.seat_id is not None
            else None
        )
        host_control = (
            HostControlView(
                public_view=public_view,
                paused=room.core.state.paused,
                revision=room.core.state.revision,
            )
            if actor.actor_type == "host"
            else None
        )
        return RoomSnapshot(
            room_id=room.room_id,
            room_code=room.room_code,
            revision=room.core.state.revision,
            outbox_seq=room.outbox_seq,
            public_view=public_view,
            seat_view=seat_view,
            host_control=host_control,
        )

    def create_room(self, ttl: timedelta) -> CreatedRoom:
        if len(self.rooms) >= self.max_rooms:
            raise ValueError("ROOM_LIMIT_REACHED")
        room_id = uuid4()
        for _ in range(100):
            try:
                room_code = self.tokens.source.room_code()
            except StopIteration:
                raise RuntimeError("ROOM_CODE_EXHAUSTED") from None
            if room_code not in self.rooms:
                break
        else:
            raise RuntimeError("ROOM_CODE_EXHAUSTED")
        token, expires_at = self.tokens.issue_host(room_id, ttl)
        actor = RoomActor(
            room_id=room_id,
            room_code=room_code,
            seed=self.seed_source(),
            clock=self.clock,
            expires_at=expires_at,
            last_activity_at=self.clock(),
        )
        self.rooms[room_code] = actor
        return CreatedRoom(
            room_id=room_id,
            room_code=room_code,
            host_token=token,
            expires_at=expires_at,
        )

    def join_room(self, room_code: str, display_name: str) -> JoinedRoom:
        actor = self.get_by_code(room_code)
        joined_seats = self.tokens.active_seat_ids(actor.room_id)
        seat_id = next(
            (seat for seat in range(1, 7) if seat not in joined_seats),
            None,
        )
        if seat_id is None:
            raise ValueError("ROOM_FULL")
        actor.touch()
        token, expires_at = self.tokens.issue_seat(
            actor.room_id,
            seat_id,
            timedelta(hours=4),
        )
        return JoinedRoom(
            room_id=actor.room_id,
            room_code=room_code,
            seat_id=seat_id,
            seat_token=token,
            expires_at=expires_at,
        )

    def allow_pairing_attempt(self, room_id: UUID, source: str) -> bool:
        now = self.clock()
        window_start, count = self._pairing_attempts.get(
            (room_id, source),
            (now, 0),
        )
        if (now - window_start).total_seconds() >= 60:
            window_start, count = now, 0
        if count >= 10:
            return False
        self._pairing_attempts[(room_id, source)] = (window_start, count + 1)
        return True

    def create_display_pairing(
        self,
        room_code: str,
    ) -> tuple[str, datetime, int]:
        room = self.get_by_code(room_code)
        raw = f"{secrets.randbelow(1_000_000):06d}"
        now = self.clock()
        expires_at = min(now + timedelta(minutes=5), room.expires_at)
        expires_in_seconds = max(0, int((expires_at - now).total_seconds()))
        self._display_pairings[room.room_id] = DisplayPairing(
            pairing_code_digest=self.tokens.digest(raw),
            room_id=room.room_id,
            expires_at=expires_at,
        )
        return raw, expires_at, expires_in_seconds

    def exchange_display_pairing(
        self,
        room_code: str,
        pairing_code: str,
        source: str,
    ) -> tuple[UUID, str, datetime]:
        room = self.get_by_code(room_code)
        if not self.allow_pairing_attempt(room.room_id, source):
            raise ValueError("RATE_LIMITED")
        pairing = self._display_pairings.get(room.room_id)
        now = self.clock()
        if pairing is None or pairing.expires_at <= now:
            raise ValueError("TOKEN_INVALID")
        if self.tokens.digest(pairing_code) != pairing.pairing_code_digest:
            pairing.failed_attempts += 1
            if pairing.failed_attempts >= 5:
                self._display_pairings.pop(room.room_id, None)
            raise ValueError("TOKEN_INVALID")
        self._display_pairings.pop(room.room_id, None)
        raw, expires_at = self.tokens.issue_display(
            room.room_id,
            timedelta(hours=1),
            room_expires_at=room.expires_at,
        )
        record = self.tokens.display_record(room.room_id)
        assert record is not None and record.session_id is not None
        room.display_session_id = record.session_id
        room.display_token_digest = self.tokens.digest(raw)
        room.sync_display_session()
        return room.room_id, raw, expires_at

    def revoke_display(self, room_code: str) -> None:
        room = self.get_by_code(room_code)
        self.tokens.revoke_display(room.room_id)
        room.display_token_digest = None
        room.display_session_id = None
        room.sync_display_session()

    async def start_room(self, room_code: str) -> None:
        actor = self.get_by_code(room_code)
        await actor.start()

    async def remove_room(self, room_code: str) -> None:
        actor = self.rooms.pop(room_code, None)
        if actor is not None:
            try:
                await actor.stop()
            finally:
                self._display_pairings.pop(actor.room_id, None)
                for key in tuple(self._pairing_attempts):
                    if key[0] == actor.room_id:
                        del self._pairing_attempts[key]
                self.tokens.remove_room(actor.room_id)

    async def reap_expired(self) -> int:
        now = self.clock()
        expired = [
            room_code
            for room_code, actor in self.rooms.items()
            if actor.expires_at <= now or actor.last_activity_at + timedelta(hours=6) <= now
        ]
        for room_code in expired:
            await self.remove_room(room_code)
        return len(expired)
