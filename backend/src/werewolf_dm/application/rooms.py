from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import secrets
import string
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic_ns
from typing import Literal, Protocol, Self
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import Field, JsonValue, ValidationError, model_validator

from werewolf_dm.application.core import (
    Clock,
    CoreMutation,
    DomainSeqAllocator,
    GameCore,
    PersistenceCoordinator,
)
from werewolf_dm.application.dm_contracts import (
    DMAdmissionResult,
    DMAnnouncementSlot,
    DMTemplateMessage,
    DMTraceRecord,
    SuppressReason,
    TemplateFact,
    TemplateIntent,
)
from werewolf_dm.application.dm_intents import (
    project_template_facts,
    select_template_intent,
)
from werewolf_dm.application.dm_service import TemplateDMService, TemplateRenderError
from werewolf_dm.application.dm_templates import TemplateRegistry
from werewolf_dm.domain.contracts import (
    EVENT_PAYLOAD_MODELS,
    ActorType,
    AuthenticatedActor,
    CommandEnvelope,
    CommandErrorCode,
    DomainEvent,
    PhaseChangedPayload,
)
from werewolf_dm.domain.model import GameState, OutboxItem, StrictModel
from werewolf_dm.domain.visibility import (
    PublicView,
    SeatView,
    project_public_view,
    project_seat_view,
)
from werewolf_dm.infrastructure.persistence import (
    PersistedAdmission,
    PersistedDMTrace,
    PersistedPublication,
    PersistedRecoveryAudit,
    PersistedRoom,
    PersistedRoomRuntime,
    PersistedSnapshot,
    PersistedToken,
    PersistenceError,
    PersistenceNotFoundError,
    SQLiteRoomStore,
)

_DM_CATALOG_VERSION = "s4-template-v1"
_RULEPACK_VERSION: Literal["1.1.0"] = "1.1.0"
UuidSource = Callable[[], UUID]
logger = logging.getLogger(__name__)


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


class DMTemplateMessageUpdate(StrictModel):
    type: Literal["dm.message"] = "dm.message"
    server_time: datetime
    outbox_seq: int
    message: DMTemplateMessage


class SessionReadyUpdate(StrictModel):
    type: Literal["session.ready"] = "session.ready"
    server_time: datetime
    snapshot: RoomSnapshot


RoomUpdate = (
    SessionReadyUpdate
    | PublicViewUpdate
    | SeatViewUpdate
    | HostControlUpdate
    | DMTemplateMessageUpdate
)


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
    recovery_epoch: int = 0


@dataclass(slots=True)
class TimerTickEvent:
    revision: int
    deadline_at: datetime
    now: datetime
    recovery_epoch: int = 0


@dataclass(slots=True)
class SyncDisplaySessionEvent:
    active_session_id: UUID | None
    recovery_epoch: int = 0


@dataclass(slots=True)
class AttachSubscriberEvent:
    subscriber: RoomSubscriber
    result: asyncio.Future[None]
    initial: bool
    recovery_epoch: int = 0


@dataclass(slots=True)
class DetachSubscriberEvent:
    subscription_id: UUID
    recovery_epoch: int = 0


@dataclass(slots=True)
class StopEvent:
    recovery_epoch: int = 0


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
    def __init__(
        self,
        source: TokenSource,
        clock: Clock,
        *,
        uuid_source: UuidSource = uuid4,
    ) -> None:
        self.source = source
        self.clock = clock
        self._uuid_source = uuid_source
        self._records: dict[str, TokenRecord] = {}
        self._seat_records: dict[UUID, dict[int, TokenRecord]] = {}
        self._display_records: dict[UUID, TokenRecord] = {}

    @contextlib.contextmanager
    def mutation_scope(self) -> Iterator[None]:
        records = self._records.copy()
        seat_records = {
            room_id: room_records.copy() for room_id, room_records in self._seat_records.items()
        }
        display_records = self._display_records.copy()
        try:
            yield
        except BaseException:
            self._records = records
            self._seat_records = seat_records
            self._display_records = display_records
            raise

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

    def restore(self, records: Iterable[TokenRecord]) -> None:
        self._records.clear()
        self._seat_records.clear()
        self._display_records.clear()
        now = self.clock()
        for record in records:
            if record.expires_at <= now:
                continue
            self._records[record.token_digest] = record
            if record.actor_type == "seat":
                assert record.seat_id is not None
                self._seat_records.setdefault(record.room_id, {})[record.seat_id] = record
            elif record.actor_type == "display":
                self._display_records[record.room_id] = record

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
            session_id=self._uuid_source(),
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
        dm_service: TemplateDMService | None = None,
        monotonic_ms: Callable[[], int] | None = None,
        uuid_source: UuidSource = uuid4,
        activity_sink: Callable[[RoomActor], None] | None = None,
        coordinator: PersistenceCoordinator | None = None,
        runtime: PersistedRoomRuntime | None = None,
        dm_traces: tuple[DMTraceRecord, ...] = (),
        dm_transport_traces: tuple[DMTraceRecord, ...] = (),
        recovery_audit: tuple[PersistedRecoveryAudit, ...] = (),
        snapshots: tuple[PersistedSnapshot, ...] = (),
    ) -> None:
        self.room_id = room_id
        self.room_code = room_code
        self.clock = clock
        self.expires_at = expires_at
        self.last_activity_at = last_activity_at
        self.core = core or GameCore(room_id=room_id, seed=seed, clock=clock)
        self.dm_service = dm_service or TemplateDMService(registry=TemplateRegistry())
        self._coordinator = coordinator or PersistenceCoordinator(None)
        self.events: asyncio.Queue[RoomEvent] = asyncio.Queue()
        self.subscribers: dict[UUID, RoomSubscriber] = {}
        self.outbox_seq = runtime.outbox_seq if runtime is not None else 0
        self.processed_announcement_seq = (
            runtime.processed_announcement_seq if runtime is not None else 0
        )
        self.domain_to_transport: dict[int, int] = (
            dict(runtime.domain_to_transport) if runtime is not None else {}
        )
        self.published_messages: list[DMTemplateMessage] = []
        self.published_kinds: list[str] = []
        self.dm_trace: list[DMTraceRecord] = list(dm_traces)
        self.dm_transport_trace: list[DMTraceRecord] = list(dm_transport_traces)
        self.last_trace: DMTraceRecord | None = None
        self._recovery_audit = recovery_audit
        self._snapshots = snapshots
        self.seat_session_ids: dict[int, UUID] = {}
        self.recovery_epoch = runtime.recovery_epoch if runtime is not None else 0
        self._discarded_command_tombstones = (
            tuple(runtime.discarded_command_tombstones) if runtime is not None else ()
        )
        self._published_message_ids = set(
            runtime.published_message_ids if runtime is not None else ()
        )
        domain_seq_start = runtime.next_domain_seq if runtime is not None else 0
        self.domain_seq_allocator = DomainSeqAllocator(
            max(
                domain_seq_start,
                self._next_domain_seq_floor(self.core.state),
            )
        )
        self.closed = False
        self._started = False
        self._task: asyncio.Task[None] | None = None
        self._timer_task: asyncio.Task[None] | None = None
        self._pending_futures: set[asyncio.Future[None] | asyncio.Future[RoomCommandAck]] = set()
        self.deadline_changed = asyncio.Event()
        self.display_token_digest: str | None = None
        self.display_session_id: UUID | None = None
        self._completed_domain_seqs: set[int] = set(
            runtime.completed_domain_seqs if runtime is not None else ()
        )
        self._announcement_candidates: dict[
            int,
            tuple[str, TemplateIntent, list[TemplateFact]],
        ] = {}
        self._announcement_slots: dict[int, DMAnnouncementSlot] = {}
        self._seat_subscription_ids: dict[int, UUID] = {}
        self._monotonic_ms_source = monotonic_ms or (lambda: monotonic_ns() // 1_000_000)
        self._fixed_monotonic_ms: int | None = None
        self._uuid_source = uuid_source
        self._activity_sink = activity_sink

    @property
    def client_outbox_seq(self) -> int:
        return self.outbox_seq

    @property
    def published_message_ids(self) -> tuple[UUID, ...]:
        return tuple(sorted(self._published_message_ids, key=str))

    def host_recovery_audit(self) -> tuple[dict[str, JsonValue], ...]:
        return tuple(record.model_dump(mode="json") for record in self._recovery_audit)

    def host_snapshots(self) -> tuple[dict[str, JsonValue], ...]:
        return tuple(snapshot.model_dump(mode="json") for snapshot in self._snapshots)

    def refresh_host_audit(self) -> None:
        store = self._coordinator.store
        if store is None:
            self._recovery_audit = ()
            self._snapshots = ()
            return
        self._recovery_audit = store.load_recovery_audit(self.room_id)
        self._snapshots = store.load_snapshots(self.room_id)

    @staticmethod
    def _next_domain_seq_floor(state: GameState) -> int:
        if not state.outbox:
            return 1
        return state.outbox[-1].seq + 1

    def monotonic_now_ms(self) -> int:
        if self._fixed_monotonic_ms is not None:
            return self._fixed_monotonic_ms
        return self._monotonic_ms_source()

    def set_monotonic_now(self, now_ms: int) -> None:
        self._fixed_monotonic_ms = now_ms

    def seat_session_id(self, seat_id: int) -> UUID | None:
        return self.seat_session_ids.get(seat_id)

    async def consume_announcements(self) -> None:
        for item in sorted(self.core.state.outbox, key=lambda outbox_item: outbox_item.seq):
            if item.seq in self._completed_domain_seqs:
                continue
            await self.consume_slot(domain_seq=item.seq)

    async def consume_slot(self, domain_seq: int) -> DMAdmissionResult:
        earlier = sorted(
            item.seq
            for item in self.core.state.outbox
            if item.seq < domain_seq and item.seq not in self._completed_domain_seqs
        )
        for earlier_seq in earlier:
            await self._consume_one(earlier_seq)
        return await self._consume_one(domain_seq)

    async def consume_announcement(self, domain_seq: int) -> DMAdmissionResult:
        return await self.consume_slot(domain_seq=domain_seq)

    async def _consume_one(self, domain_seq: int) -> DMAdmissionResult:
        return await self.admit(self._slot_for_domain_seq(domain_seq))

    async def admit(
        self,
        slot: DMAnnouncementSlot,
        *,
        intent: TemplateIntent | None = None,
        facts: list[TemplateFact] | None = None,
    ) -> DMAdmissionResult:
        if slot.room_id != self.room_id:
            raise ValueError("ANNOUNCEMENT_ROOM_MISMATCH")

        now_ms = self.monotonic_now_ms()
        slot_item = self._outbox_item(slot.domain_seq)
        explicit_candidate: tuple[str, TemplateIntent, list[TemplateFact]] | None = None
        if intent is not None:
            if slot_item is None:
                raise ValueError("ANNOUNCEMENT_ITEM_NOT_FOUND")
            try:
                intent = TemplateIntent.revalidate(intent)
            except ValidationError as error:
                raise ValueError("ANNOUNCEMENT_INTENT_INVALID") from error
            intent_seat_id = intent.audience_seat_ids[0] if intent.channel == "seat" else None
            if intent_seat_id != slot_item.audience_seat_id:
                raise ValueError("ANNOUNCEMENT_AUDIENCE_MISMATCH")
            explicit_candidate = (
                slot_item.kind,
                intent,
                [] if facts is None else list(facts),
            )

        if (
            slot_item is not None
            and slot_item.audience_seat_id is not None
            and self.seat_session_ids.get(slot_item.audience_seat_id) is None
        ):
            self._complete_silently(slot.domain_seq)
            return self._suppressed_result(slot.domain_seq)

        candidate = self._announcement_candidates.get(slot.domain_seq)
        if explicit_candidate is not None:
            candidate = explicit_candidate
            self._announcement_candidates[slot.domain_seq] = candidate
        elif candidate is None:
            try:
                candidate = self._candidate_for_domain_seq(slot.domain_seq)
            except (KeyError, ValueError):
                candidate = None

        if slot.domain_seq in self._completed_domain_seqs:
            if candidate is not None:
                self._record_suppressed(slot, candidate[1], "duplicate_slot", now_ms)
            return self._suppressed_result(slot.domain_seq)

        if candidate is None:
            trace_intent = self._trace_intent_for_domain_seq(slot.domain_seq)
            if trace_intent is not None:
                item = self._outbox_item(slot.domain_seq)
                reason: SuppressReason = (
                    "stale_revision"
                    if item is None or item.revision != slot.revision
                    else "stale_phase"
                )
                self._complete_suppressed(slot, trace_intent, reason, now_ms)
            return self._suppressed_result(slot.domain_seq)

        kind, candidate_intent, candidate_facts = candidate

        if now_ms > slot.admission_deadline_monotonic_ms:
            self._complete_suppressed(slot, candidate_intent, "admission_timeout", now_ms)
            return self._suppressed_result(slot.domain_seq)

        if slot.revision != self.core.state.revision:
            self._complete_suppressed(slot, candidate_intent, "stale_revision", now_ms)
            return self._suppressed_result(slot.domain_seq)

        item = self._outbox_item(slot.domain_seq)
        if (
            item is None
            or item.revision != slot.revision
            or item.event_id != candidate_intent.source_event_id
            or candidate_intent.source_revision != slot.revision
            or candidate_intent.source_phase != self.core.state.phase
        ):
            self._complete_suppressed(slot, candidate_intent, "stale_phase", now_ms)
            return self._suppressed_result(slot.domain_seq)

        if self.closed or self.expires_at <= self.clock():
            self._complete_suppressed(slot, candidate_intent, "room_closed", now_ms)
            return self._suppressed_result(slot.domain_seq)

        try:
            rendered = self.dm_service.resolve(
                candidate_intent,
                candidate_facts,
                slot,
                _DM_CATALOG_VERSION,
            )
        except TemplateRenderError:
            completed = set(self._completed_domain_seqs)
            completed.add(slot.domain_seq)
            processed = max(self.processed_announcement_seq, slot.domain_seq)
            trace = self._build_trace(
                slot,
                candidate_intent,
                admission_status="failed",
                suppress_reason=None,
                elapsed_ms=max(0, now_ms - slot.trigger_at_monotonic_ms),
            )
            runtime = self._runtime_snapshot(
                completed_domain_seqs=completed,
                processed_announcement_seq=processed,
            )
            self._commit_runtime_update(runtime, (trace,))
            self._apply_runtime(runtime, traces=(trace,))
            return self._suppressed_result(slot.domain_seq)

        if not self._audience_matches_current_binding(rendered.audience_bindings):
            self._complete_suppressed(slot, candidate_intent, "stale_phase", now_ms)
            return self._suppressed_result(slot.domain_seq)

        transport_seq = self.outbox_seq + 1
        mapping = dict(self.domain_to_transport)
        mapping[slot.domain_seq] = transport_seq
        completed = set(self._completed_domain_seqs)
        completed.add(slot.domain_seq)
        processed = max(self.processed_announcement_seq, slot.domain_seq)
        message = DMTemplateMessage(
            message_id=uuid5(
                NAMESPACE_URL,
                f"{candidate_intent.intent_id}:{slot.domain_seq}:{transport_seq}",
            ),
            room_id=self.room_id,
            revision=slot.revision,
            channel=rendered.channel,
            audience_bindings=rendered.audience_bindings,
            text=rendered.final_text,
            source="template",
        )
        published_message_ids = set(self._published_message_ids)
        published_message_ids.add(message.message_id)
        trace = self._build_trace(
            slot,
            candidate_intent,
            admission_status="admitted",
            suppress_reason=None,
            elapsed_ms=max(0, now_ms - slot.trigger_at_monotonic_ms),
        )
        runtime = self._runtime_snapshot(
            outbox_seq=transport_seq,
            domain_to_transport=mapping,
            completed_domain_seqs=completed,
            processed_announcement_seq=processed,
            published_message_ids=published_message_ids,
        )
        self._commit_runtime_update(
            runtime,
            (trace,),
            admissions=(
                PersistedAdmission(
                    domain_seq=slot.domain_seq,
                    message_id=message.message_id,
                    transport_seq=transport_seq,
                    recovery_epoch=self.recovery_epoch,
                ),
            ),
            publications=(
                PersistedPublication(
                    transport_seq=transport_seq,
                    message_id=message.message_id,
                    recovery_epoch=self.recovery_epoch,
                ),
            ),
        )
        self._apply_runtime(runtime, traces=(trace,))
        self.published_messages.append(message)
        self.published_kinds.append(kind)
        self._publish_dm_message(
            message,
            transport_seq=transport_seq,
            slot=slot,
            intent=candidate_intent,
            now_ms=now_ms,
        )
        return DMAdmissionResult(
            domain_seq=slot.domain_seq,
            transport_seq=transport_seq,
            admitted=True,
            message=message,
        )

    def _candidate_for_domain_seq(
        self,
        domain_seq: int,
    ) -> tuple[str, TemplateIntent, list[TemplateFact]]:
        item = self._outbox_item(domain_seq)
        if item is None:
            raise KeyError(domain_seq)
        event = self._event_for_domain_seq(domain_seq)
        if event is None:
            raise ValueError("ANNOUNCEMENT_EVENT_NOT_FOUND")
        if item.audience_seat_id is None:
            intent = select_template_intent(
                self.core.state,
                event,
                catalog_version=_DM_CATALOG_VERSION,
            )
        else:
            session_id = self.seat_session_ids.get(item.audience_seat_id)
            intent = select_template_intent(
                self.core.state,
                event,
                catalog_version=_DM_CATALOG_VERSION,
                seat_id=item.audience_seat_id,
                session_id=session_id,
                expected_session_id=session_id,
            )
        facts = project_template_facts(self.core.state, event, intent)
        candidate = (item.kind, intent, facts)
        self._announcement_candidates[domain_seq] = candidate
        return candidate

    def _slot_for_domain_seq(self, domain_seq: int) -> DMAnnouncementSlot:
        slot = self._announcement_slots.get(domain_seq)
        if slot is not None:
            return slot
        now_ms = self.monotonic_now_ms()
        event = self._event_for_domain_seq(domain_seq)
        trigger_at_monotonic_ms = now_ms
        if event is not None:
            elapsed_ms = max(
                0,
                int((self.clock() - event.created_at).total_seconds() * 1000),
            )
            trigger_at_monotonic_ms = max(0, now_ms - elapsed_ms)
        slot = DMAnnouncementSlot(
            domain_seq=domain_seq,
            room_id=self.room_id,
            revision=self.core.state.revision,
            trigger_at_monotonic_ms=trigger_at_monotonic_ms,
            admission_deadline_monotonic_ms=trigger_at_monotonic_ms + 2000,
        )
        self._announcement_slots[domain_seq] = slot
        return slot

    def _trace_intent_for_domain_seq(self, domain_seq: int) -> TemplateIntent | None:
        event = self._event_for_domain_seq(domain_seq)
        if event is None:
            return None
        event_state = self.core.state.model_copy(update={"revision": event.revision})
        payload = EVENT_PAYLOAD_MODELS[event.event_type].validate_json_payload(event.fact_payload)
        if isinstance(payload, PhaseChangedPayload):
            event_state = event_state.model_copy(update={"phase": payload.next_phase})
        try:
            item = self._outbox_item(domain_seq)
            if item is not None and item.audience_seat_id is not None:
                session_id = self.seat_session_ids.get(item.audience_seat_id)
                return select_template_intent(
                    event_state,
                    event,
                    catalog_version=_DM_CATALOG_VERSION,
                    seat_id=item.audience_seat_id,
                    session_id=session_id,
                    expected_session_id=session_id,
                )
            return select_template_intent(
                event_state,
                event,
                catalog_version=_DM_CATALOG_VERSION,
            )
        except ValueError:
            return None

    def _complete_silently(self, domain_seq: int) -> None:
        completed = set(self._completed_domain_seqs)
        completed.add(domain_seq)
        processed = max(self.processed_announcement_seq, domain_seq)
        runtime = self._runtime_snapshot(
            completed_domain_seqs=completed,
            processed_announcement_seq=processed,
        )
        self._commit_runtime_update(runtime)
        self._apply_runtime(runtime)

    def _event_for_domain_seq(self, domain_seq: int) -> DomainEvent | None:
        item = self._outbox_item(domain_seq)
        if item is None:
            return None
        return next(
            (event for event in reversed(self.core.events) if event.event_id == item.event_id),
            None,
        )

    def _outbox_item(self, domain_seq: int) -> OutboxItem | None:
        return next(
            (item for item in self.core.state.outbox if item.seq == domain_seq),
            None,
        )

    def _audience_matches_current_binding(
        self,
        bindings: tuple[object, ...],
    ) -> bool:
        for binding in bindings:
            seat_id = getattr(binding, "seat_id", None)
            session_id = getattr(binding, "session_id", None)
            if seat_id is None:
                return False
            if self.seat_session_ids.get(seat_id) != session_id:
                return False
        return True

    def _complete_suppressed(
        self,
        slot: DMAnnouncementSlot,
        intent: TemplateIntent,
        reason: SuppressReason,
        now_ms: int,
    ) -> None:
        completed = set(self._completed_domain_seqs)
        completed.add(slot.domain_seq)
        processed = max(self.processed_announcement_seq, slot.domain_seq)
        trace = self._build_trace(
            slot,
            intent,
            admission_status="suppressed",
            suppress_reason=reason,
            elapsed_ms=max(0, now_ms - slot.trigger_at_monotonic_ms),
        )
        runtime = self._runtime_snapshot(
            completed_domain_seqs=completed,
            processed_announcement_seq=processed,
        )
        self._commit_runtime_update(runtime, (trace,))
        self._apply_runtime(runtime, traces=(trace,))

    def _record_suppressed(
        self,
        slot: DMAnnouncementSlot,
        intent: TemplateIntent,
        reason: SuppressReason,
        now_ms: int,
    ) -> None:
        trace = self._build_trace(
            slot,
            intent,
            admission_status="suppressed",
            suppress_reason=reason,
            elapsed_ms=max(0, now_ms - slot.trigger_at_monotonic_ms),
        )
        runtime = self._runtime_snapshot()
        self._commit_runtime_update(runtime, (trace,))
        self._apply_runtime(runtime, traces=(trace,))

    def _build_trace(
        self,
        slot: DMAnnouncementSlot,
        intent: TemplateIntent,
        *,
        admission_status: Literal["admitted", "suppressed", "failed"],
        suppress_reason: SuppressReason | None,
        elapsed_ms: int,
    ) -> DMTraceRecord:
        return DMTraceRecord(
            trace_id=self._uuid_source(),
            intent_id=intent.intent_id,
            template_variant_id=intent.template_variant_id,
            catalog_version=intent.catalog_version,
            source_event_ids=intent.source_event_ids,
            channel=intent.channel,
            audience_seat_ids=intent.audience_seat_ids,
            admission_status=admission_status,
            suppress_reason=suppress_reason,
            elapsed_ms=elapsed_ms,
        )

    def _suppressed_result(self, domain_seq: int) -> DMAdmissionResult:
        return DMAdmissionResult(
            domain_seq=domain_seq,
            transport_seq=self.client_outbox_seq,
            admitted=False,
            message=None,
        )

    def current_deadline(self) -> datetime | None:
        return self.core.state.deadline_at

    def _runtime_snapshot(
        self,
        *,
        outbox_seq: int | None = None,
        domain_to_transport: dict[int, int] | None = None,
        completed_domain_seqs: set[int] | None = None,
        processed_announcement_seq: int | None = None,
        published_message_ids: set[UUID] | None = None,
        next_domain_seq: int | None = None,
        recovery_epoch: int | None = None,
    ) -> PersistedRoomRuntime:
        return PersistedRoomRuntime.model_construct(
            outbox_seq=self.outbox_seq if outbox_seq is None else outbox_seq,
            domain_to_transport=(
                dict(self.domain_to_transport)
                if domain_to_transport is None
                else domain_to_transport
            ),
            completed_domain_seqs=tuple(
                sorted(
                    self._completed_domain_seqs
                    if completed_domain_seqs is None
                    else completed_domain_seqs
                )
            ),
            processed_announcement_seq=(
                self.processed_announcement_seq
                if processed_announcement_seq is None
                else processed_announcement_seq
            ),
            published_message_ids=tuple(
                sorted(
                    self._published_message_ids
                    if published_message_ids is None
                    else published_message_ids,
                    key=str,
                )
            ),
            next_domain_seq=(
                self.domain_seq_allocator.current() if next_domain_seq is None else next_domain_seq
            ),
            recovery_epoch=(self.recovery_epoch if recovery_epoch is None else recovery_epoch),
            discarded_command_tombstones=self._discarded_command_tombstones,
        )

    @staticmethod
    def _persisted_trace(
        trace: DMTraceRecord,
        *,
        transport: bool = False,
    ) -> PersistedDMTrace:
        return PersistedDMTrace(
            trace_kind="DM_TRANSPORT_TRACE" if transport else "DM_TRACE",
            trace=trace,
        )

    def _commit_core_mutation(self, mutation: CoreMutation) -> None:
        must_persist = (
            mutation.next_state != self.core.state
            or bool(mutation.appended_events)
            or mutation.cache_result
        )
        if not must_persist:
            return
        try:
            self._coordinator.commit_core_mutation(
                self.room_id,
                mutation,
                self._runtime_snapshot(),
                (),
            )
        except BaseException:
            for reservation in reversed(mutation.seq_reservations):
                self.domain_seq_allocator.rollback(reservation)
            raise
        self.core.commit(mutation)
        for reservation in mutation.seq_reservations:
            self.domain_seq_allocator.commit(reservation)

    def _commit_runtime_update(
        self,
        runtime: PersistedRoomRuntime,
        traces: tuple[DMTraceRecord, ...] = (),
        *,
        transport_traces: bool = False,
        admissions: tuple[PersistedAdmission, ...] = (),
        publications: tuple[PersistedPublication, ...] = (),
    ) -> None:
        persisted_traces = tuple(
            self._persisted_trace(trace, transport=transport_traces) for trace in traces
        )
        self._coordinator.commit_runtime_update(
            self.room_id,
            runtime,
            persisted_traces,
            admissions,
            publications,
        )

    def _apply_runtime(
        self,
        runtime: PersistedRoomRuntime,
        *,
        traces: tuple[DMTraceRecord, ...] = (),
    ) -> None:
        self.outbox_seq = runtime.outbox_seq
        self.domain_to_transport = dict(runtime.domain_to_transport)
        self.processed_announcement_seq = runtime.processed_announcement_seq
        self._completed_domain_seqs = set(runtime.completed_domain_seqs)
        self._published_message_ids = set(runtime.published_message_ids)
        self.recovery_epoch = runtime.recovery_epoch
        self._discarded_command_tombstones = tuple(runtime.discarded_command_tombstones)
        if traces:
            self.dm_trace.extend(traces)
            self.last_trace = traces[-1]

    def sync_display_session(self) -> None:
        self.events.put_nowait(
            SyncDisplaySessionEvent(
                active_session_id=self.display_session_id,
                recovery_epoch=self.recovery_epoch,
            )
        )

    def touch(self, *, now: datetime | None = None, persist: bool = True) -> None:
        self.last_activity_at = self.clock() if now is None else now
        if persist and self._activity_sink is not None:
            self._activity_sink(self)

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
        self.seat_session_ids.clear()
        self._seat_subscription_ids.clear()
        self.deadline_changed.set()
        self._reject_pending_futures(exc)
        if self._timer_task is not None:
            self._timer_task.cancel()

    def _on_actor_task_done(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()
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
        await self.events.put(StopEvent(recovery_epoch=self.recovery_epoch))
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
                recovery_epoch=self.recovery_epoch,
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
                recovery_epoch=self.recovery_epoch,
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
                recovery_epoch=self.recovery_epoch,
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
                recovery_epoch=self.recovery_epoch,
            )
        )
        try:
            await future
        finally:
            self._pending_futures.discard(future)

    async def detach_subscriber(self, subscription_id: UUID) -> None:
        await self.events.put(
            DetachSubscriberEvent(
                subscription_id=subscription_id,
                recovery_epoch=self.recovery_epoch,
            )
        )

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
            if (
                getattr(event, "recovery_epoch", None) is not None
                and event.recovery_epoch != self.recovery_epoch
            ):
                if isinstance(event, SubmitCommandEvent) and not event.result.done():
                    event.result.set_result(
                        RoomCommandAck(
                            command_id=event.envelope.command_id,
                            accepted=False,
                            revision=self.core.state.revision,
                            error_code=CommandErrorCode.COMMAND_VOIDED_BY_REWIND,
                            outbox_seq=self.outbox_seq,
                        )
                    )
                elif isinstance(event, AttachSubscriberEvent) and not event.result.done():
                    event.result.set_result(None)
                continue
            if isinstance(event, SubmitCommandEvent):
                revision_before = self.core.state.revision
                mutation = self.core.stage_submit(
                    event.envelope,
                    event.actor,
                    next_domain_seq=self.domain_seq_allocator,
                    discarded_command_tombstones=self._discarded_command_tombstones,
                )
                self._commit_core_mutation(mutation)
                result = mutation.command_result
                if result.accepted and result.revision > revision_before:
                    self.touch()
                    await self.consume_announcements()
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
                    mutation = self.core.stage_tick(
                        next_domain_seq=self.domain_seq_allocator,
                    )
                    if mutation.command_result.accepted:
                        self._commit_core_mutation(mutation)
                        self.touch()
                        await self.consume_announcements()
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
                if (
                    subscriber.actor_type == "seat"
                    and subscriber.seat_id is not None
                    and (event.initial or subscriber.seat_id not in self.seat_session_ids)
                ):
                    self.seat_session_ids[subscriber.seat_id] = self._uuid_source()
                    self._seat_subscription_ids[subscriber.seat_id] = subscriber.subscription_id
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
                detached_subscriber = self.subscribers.get(event.subscription_id)
                if detached_subscriber is not None:
                    del self.subscribers[event.subscription_id]
                if (
                    detached_subscriber is not None
                    and detached_subscriber.actor_type == "seat"
                    and detached_subscriber.seat_id is not None
                    and self._seat_subscription_ids.get(detached_subscriber.seat_id)
                    == event.subscription_id
                ):
                    self._seat_subscription_ids.pop(detached_subscriber.seat_id, None)
                    self.seat_session_ids.pop(detached_subscriber.seat_id, None)
                continue
            raise RuntimeError("UNHANDLED_ROOM_EVENT")

    @staticmethod
    def _subscriber_identity(
        subscriber: RoomSubscriber,
    ) -> tuple[ActorType, int | None, UUID | None]:
        return subscriber.actor_type, subscriber.seat_id, subscriber.session_id

    def _publish_dm_message(
        self,
        message: DMTemplateMessage,
        *,
        transport_seq: int,
        slot: DMAnnouncementSlot,
        intent: TemplateIntent,
        now_ms: int,
    ) -> None:
        update = DMTemplateMessageUpdate(
            server_time=self.clock(),
            outbox_seq=transport_seq,
            message=message,
        )
        delivery_failed = False
        for subscriber in tuple(self.subscribers.values()):
            if not self._dm_message_targets_subscriber(message, subscriber):
                continue
            if not subscriber.offer(update):
                delivery_failed = True
        if delivery_failed:
            self._record_transport_failure(
                slot,
                intent,
                elapsed_ms=max(0, now_ms - slot.trigger_at_monotonic_ms),
            )

    def _dm_message_targets_subscriber(
        self,
        message: DMTemplateMessage,
        subscriber: RoomSubscriber,
    ) -> bool:
        if subscriber.actor_type == "display" and subscriber.session_id != self.display_session_id:
            self.subscribers.pop(subscriber.subscription_id, None)
            subscriber.request_close(4001)
            return False
        if message.channel == "public":
            return "public" in subscriber.channels
        binding = message.audience_bindings[0]
        seat_id = binding.seat_id
        session_id = binding.session_id
        return (
            seat_id is not None
            and session_id is not None
            and subscriber.actor_type == "seat"
            and subscriber.seat_id == seat_id
            and "seat" in subscriber.channels
            and self.seat_session_ids.get(seat_id) == session_id
            and self._seat_subscription_ids.get(seat_id) == subscriber.subscription_id
        )

    def _record_transport_failure(
        self,
        slot: DMAnnouncementSlot,
        intent: TemplateIntent,
        *,
        elapsed_ms: int,
    ) -> None:
        trace = DMTraceRecord(
            trace_id=self._uuid_source(),
            intent_id=intent.intent_id,
            template_variant_id=intent.template_variant_id,
            catalog_version=intent.catalog_version,
            source_event_ids=intent.source_event_ids,
            channel=intent.channel,
            audience_seat_ids=intent.audience_seat_ids,
            admission_status="suppressed",
            suppress_reason="transport_failed",
            elapsed_ms=elapsed_ms,
        )
        runtime = self._runtime_snapshot()
        self._commit_runtime_update(
            runtime,
            (trace,),
            transport_traces=True,
        )
        self.dm_transport_trace.append(trace)

    def _publish_updates(self) -> None:
        runtime = self._runtime_snapshot(outbox_seq=self.outbox_seq + 1)
        self._commit_runtime_update(runtime)
        self._apply_runtime(runtime)
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
        uuid_source: UuidSource = uuid4,
        store: SQLiteRoomStore | None = None,
    ) -> None:
        if max_rooms <= 0 or max_connections <= 0:
            raise ValueError("ROOM_LIMIT_INVALID")
        self.clock = clock
        self.tokens = TokenService(
            token_source,
            clock,
            uuid_source=uuid_source,
        )
        self.seed_source = seed_source
        self._uuid_source = uuid_source
        self.max_rooms = max_rooms
        self.max_connections = max_connections
        self.store = store
        self.rooms: dict[str, RoomActor] = {}
        self.active_connections = 0
        self.auth_failures = 0
        self.slow_connection_closes = 0
        self._display_pairings: dict[UUID, DisplayPairing] = {}
        self._pairing_attempts: dict[tuple[UUID, str], tuple[datetime, int]] = {}
        if self.store is not None:
            self._restore_from_store(self.store)

    def _restore_from_store(self, store: SQLiteRoomStore) -> None:
        now = self.clock()
        actors_by_id: dict[UUID, RoomActor] = {}
        for persisted in store.load_rooms():
            if persisted.expires_at <= now:
                store.delete_room(persisted.room_id)
                continue
            try:
                core, runtime, dm_traces, dm_transport_traces = self._restore_room(
                    store,
                    persisted,
                )
            except (PersistenceError, ValueError) as exc:
                logger.warning(
                    "Skipping corrupt persisted room code=%s exception_type=%s",
                    persisted.room_code,
                    type(exc).__name__,
                )
                continue
            actor = self._new_actor(
                room_id=persisted.room_id,
                room_code=persisted.room_code,
                seed=persisted.seed,
                expires_at=persisted.expires_at,
                last_activity_at=persisted.last_activity_at,
                core=core,
                runtime=runtime,
                dm_traces=dm_traces,
                dm_transport_traces=dm_transport_traces,
                recovery_audit=store.load_recovery_audit(persisted.room_id),
                snapshots=store.load_snapshots(persisted.room_id),
            )
            self.rooms[persisted.room_code] = actor
            actors_by_id[persisted.room_id] = actor

        records = tuple(
            self._token_record(token)
            for token in store.load_tokens()
            if not token.revoked and token.expires_at > now and token.room_id in actors_by_id
        )
        self.tokens.restore(records)
        for record in records:
            if record.actor_type != "display":
                continue
            actor = actors_by_id[record.room_id]
            actor.display_token_digest = record.token_digest
            actor.display_session_id = record.session_id

    def _restore_room(
        self,
        store: SQLiteRoomStore,
        persisted: PersistedRoom,
    ) -> tuple[
        GameCore,
        PersistedRoomRuntime,
        tuple[DMTraceRecord, ...],
        tuple[DMTraceRecord, ...],
    ]:
        try:
            state, events = store.load_core(persisted.room_id)
            core = GameCore.restore(
                room_id=persisted.room_id,
                seed=persisted.seed,
                clock=self.clock,
                state=state,
                events=events,
                command_results=store.load_command_results(persisted.room_id),
            )
        except PersistenceNotFoundError:
            core = GameCore.new_room(
                persisted.room_id,
                persisted.seed,
                self.clock,
            )
        try:
            runtime = store.load_room_runtime(persisted.room_id)
        except PersistenceNotFoundError:
            next_domain_seq = core.state.outbox[-1].seq + 1 if core.state.outbox else 1
            runtime = PersistedRoomRuntime(
                outbox_seq=0,
                domain_to_transport={},
                completed_domain_seqs=(),
                processed_announcement_seq=0,
                published_message_ids=(),
                next_domain_seq=next_domain_seq,
                recovery_epoch=0,
                discarded_command_tombstones=(),
            )
        dm_traces = store.load_dm_traces(
            persisted.room_id,
            "DM_TRACE",
        )
        dm_transport_traces = store.load_dm_traces(
            persisted.room_id,
            "DM_TRANSPORT_TRACE",
        )
        return core, runtime, dm_traces, dm_transport_traces

    def _new_actor(
        self,
        *,
        room_id: UUID,
        room_code: str,
        seed: int,
        expires_at: datetime,
        last_activity_at: datetime,
        core: GameCore | None = None,
        runtime: PersistedRoomRuntime | None = None,
        dm_traces: tuple[DMTraceRecord, ...] = (),
        dm_transport_traces: tuple[DMTraceRecord, ...] = (),
        recovery_audit: tuple[PersistedRecoveryAudit, ...] = (),
        snapshots: tuple[PersistedSnapshot, ...] = (),
    ) -> RoomActor:
        return RoomActor(
            room_id=room_id,
            room_code=room_code,
            seed=seed,
            clock=self.clock,
            expires_at=expires_at,
            last_activity_at=last_activity_at,
            core=core,
            coordinator=PersistenceCoordinator(self.store),
            runtime=runtime,
            dm_traces=dm_traces,
            dm_transport_traces=dm_transport_traces,
            recovery_audit=recovery_audit,
            snapshots=snapshots,
            uuid_source=self._uuid_source,
            activity_sink=(self._persist_room_activity if self.store is not None else None),
        )

    @staticmethod
    def _token_record(token: PersistedToken) -> TokenRecord:
        return TokenRecord(
            token_digest=token.token_digest,
            room_id=token.room_id,
            actor_type=token.actor_type,
            seat_id=token.seat_id,
            session_id=token.session_id,
            issued_at=token.issued_at,
            expires_at=token.expires_at,
        )

    @staticmethod
    def _persisted_room(actor: RoomActor) -> PersistedRoom:
        return PersistedRoom(
            room_code=actor.room_code,
            room_id=actor.room_id,
            seed=actor.core.state.seed,
            rulepack_version=_RULEPACK_VERSION,
            expires_at=actor.expires_at,
            last_activity_at=actor.last_activity_at,
        )

    @staticmethod
    def _persisted_token(record: TokenRecord, *, revoked: bool = False) -> PersistedToken:
        return PersistedToken(
            token_digest=record.token_digest,
            room_id=record.room_id,
            actor_type=record.actor_type,
            seat_id=record.seat_id,
            session_id=record.session_id,
            issued_at=record.issued_at,
            expires_at=record.expires_at,
            revoked=revoked,
        )

    def _persist_room_activity(self, actor: RoomActor) -> None:
        if self.store is not None:
            self.store.save_room(self._persisted_room(actor))

    def _persist_room_and_token(self, actor: RoomActor, record: TokenRecord) -> None:
        if self.store is None:
            return
        with self.store.transaction():
            self.store.save_room(self._persisted_room(actor))
            self.store.save_token(self._persisted_token(record))

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
        room_id = self._uuid_source()
        for _ in range(100):
            try:
                room_code = self.tokens.source.room_code()
            except StopIteration:
                raise RuntimeError("ROOM_CODE_EXHAUSTED") from None
            if room_code not in self.rooms:
                break
        else:
            raise RuntimeError("ROOM_CODE_EXHAUSTED")
        with self.tokens.mutation_scope():
            token, expires_at = self.tokens.issue_host(room_id, ttl)
            actor = self._new_actor(
                room_id=room_id,
                room_code=room_code,
                seed=self.seed_source(),
                expires_at=expires_at,
                last_activity_at=self.clock(),
            )
            self._persist_room_and_token(actor, self.tokens.resolve(token))
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
        previous_activity = actor.last_activity_at
        try:
            with self.tokens.mutation_scope():
                actor.touch(persist=False)
                token, expires_at = self.tokens.issue_seat(
                    actor.room_id,
                    seat_id,
                    timedelta(hours=4),
                )
                self._persist_room_and_token(actor, self.tokens.resolve(token))
        except BaseException:
            actor.last_activity_at = previous_activity
            raise
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
        previous_display = self.tokens.display_record(room.room_id)
        previous_digest = room.display_token_digest
        previous_session = room.display_session_id
        try:
            with self.tokens.mutation_scope():
                raw, expires_at = self.tokens.issue_display(
                    room.room_id,
                    timedelta(hours=1),
                    room_expires_at=room.expires_at,
                )
                record = self.tokens.display_record(room.room_id)
                assert record is not None and record.session_id is not None
                room.display_session_id = record.session_id
                room.display_token_digest = self.tokens.digest(raw)
                if self.store is not None:
                    with self.store.transaction():
                        if previous_display is not None:
                            self.store.save_token(
                                self._persisted_token(previous_display, revoked=True)
                            )
                        self.store.save_token(self._persisted_token(record))
                        self.store.save_room(self._persisted_room(room))
        except BaseException:
            room.display_token_digest = previous_digest
            room.display_session_id = previous_session
            raise
        self._display_pairings.pop(room.room_id, None)
        room.sync_display_session()
        return room.room_id, raw, expires_at

    def revoke_display(self, room_code: str) -> None:
        room = self.get_by_code(room_code)
        record = self.tokens.display_record(room.room_id)
        previous_digest = room.display_token_digest
        previous_session = room.display_session_id
        try:
            with self.tokens.mutation_scope():
                self.tokens.revoke_display(room.room_id)
                room.display_token_digest = None
                room.display_session_id = None
                if self.store is not None and record is not None:
                    with self.store.transaction():
                        self.store.save_token(self._persisted_token(record, revoked=True))
                        self.store.save_room(self._persisted_room(room))
        except BaseException:
            room.display_token_digest = previous_digest
            room.display_session_id = previous_session
            raise
        room.sync_display_session()

    async def start_room(self, room_code: str) -> None:
        actor = self.get_by_code(room_code)
        await actor.start()

    async def start_rooms(self) -> None:
        for actor in tuple(self.rooms.values()):
            await actor.start()

    async def remove_room(self, room_code: str) -> None:
        actor = self.rooms.get(room_code)
        if actor is None:
            return
        if self.store is not None:
            self.store.delete_room(actor.room_id)
        self.rooms.pop(room_code, None)
        try:
            await actor.stop()
        finally:
            self._display_pairings.pop(actor.room_id, None)
            for key in tuple(self._pairing_attempts):
                if key[0] == actor.room_id:
                    del self._pairing_attempts[key]
            self.tokens.remove_room(actor.room_id)

    async def close(self, *, close_store: bool = False) -> None:
        try:
            for actor in tuple(self.rooms.values()):
                await actor.stop()
        finally:
            if close_store and self.store is not None:
                self.store.close()

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
