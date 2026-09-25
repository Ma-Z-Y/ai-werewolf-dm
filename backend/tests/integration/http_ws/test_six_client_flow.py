from __future__ import annotations

import asyncio
import json
import math
import socket
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import uvicorn
from websockets.sync.client import ClientConnection, connect

from werewolf_dm.application.core import FrozenClock
from werewolf_dm.application.rooms import RoomActor, RoomRegistry, SequenceTokenSource
from werewolf_dm.domain.contracts import (
    AbstainCommand,
    AuthenticatedActor,
    CommandEnvelope,
    CommandPayload,
    HostPauseCommand,
    HostResumeCommand,
    JoinRoomCommand,
    PassSpeechCommand,
    SeerInspectCommand,
    SetReadyCommand,
    VoteCommand,
    WitchSkipCommand,
    WolfNominateKillCommand,
)
from werewolf_dm.domain.enums import Phase, Role
from werewolf_dm.domain.model import GameState
from werewolf_dm.domain.replay import state_hash
from werewolf_dm.domain.visibility import project_host_audit, project_player_replay
from werewolf_dm.interfaces.http_ws.app import create_app

START_TIME = datetime(2026, 9, 25, tzinfo=UTC)
ROOM_CODE = "E2E012"
READ_TIMEOUT_SECONDS = 5.0
FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "event_ids",
        "legal_actions",
        "private_facts",
        "recipient_seat_id",
        "role",
        "target_faction",
        "token_digest",
    }
)
PUBLIC_VIEW_KEYS = frozenset(
    {
        "schema_version",
        "room_id",
        "revision",
        "phase",
        "day",
        "living_seats",
        "public_timeline",
        "vote_summary",
        "deadline_at",
    }
)


class StartedServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.started_event = threading.Event()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        self.started_event.set()


@contextmanager
def running_server(
    registry: RoomRegistry,
) -> Iterator[tuple[StartedServer, asyncio.AbstractEventLoop, int]]:
    app = create_app(registry)
    server = StartedServer(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="warning",
            lifespan="on",
            access_log=False,
        )
    )
    loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

    def run_server() -> None:
        loop = asyncio.new_event_loop()
        loop_holder["loop"] = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        finally:
            loop.close()

    thread = threading.Thread(target=run_server, name="six-client-uvicorn", daemon=True)
    thread.start()
    if not server.started_event.wait(timeout=READ_TIMEOUT_SECONDS):
        raise RuntimeError("uvicorn server did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield server, loop_holder["loop"], port
    finally:
        try:
            loop = loop_holder.get("loop")
            if loop is not None and loop.is_running():
                cleanup = asyncio.run_coroutine_threadsafe(
                    remove_all_rooms(registry),
                    loop,
                )
                cleanup.result(timeout=READ_TIMEOUT_SECONDS)
        finally:
            server.should_exit = True
            thread.join(timeout=READ_TIMEOUT_SECONDS)
            if thread.is_alive():
                raise RuntimeError("uvicorn server did not stop")


async def remove_all_rooms(registry: RoomRegistry) -> None:
    for room_code in tuple(registry.rooms):
        await registry.remove_room(room_code)


def make_registry() -> RoomRegistry:
    return RoomRegistry(
        clock=FrozenClock(START_TIME),
        token_source=SequenceTokenSource(
            tokens=(
                "host-token",
                "seat-1-token",
                "seat-2-token",
                "seat-3-token",
                "seat-4-token",
                "seat-5-token",
                "seat-6-token",
                "display-token",
            ),
            room_codes=(ROOM_CODE,),
        ),
        seed_source=lambda: 101,
    )


def receive_json(socket_: ClientConnection) -> dict[str, Any]:
    raw = socket_.recv(timeout=READ_TIMEOUT_SECONDS)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    message = json.loads(raw)
    if not isinstance(message, dict):
        raise AssertionError(f"expected object message, got {message!r}")
    return cast(dict[str, Any], message)


def receive_type(socket_: ClientConnection, expected_type: str) -> dict[str, Any]:
    message = receive_json(socket_)
    if message.get("type") != expected_type:
        raise AssertionError(f"expected {expected_type}, got {message!r}")
    return message


def open_authenticated_client(
    stack: ExitStack,
    port: int,
    token: str,
) -> tuple[ClientConnection, dict[str, Any]]:
    socket_ = stack.enter_context(
        connect(
            f"ws://127.0.0.1:{port}/ws",
            proxy=None,
            open_timeout=READ_TIMEOUT_SECONDS,
            close_timeout=READ_TIMEOUT_SECONDS,
            max_queue=64,
        )
    )
    receive_type(socket_, "auth.required")
    socket_.send(json.dumps({"type": "auth", "token": token, "last_seq": 0}))
    return socket_, receive_type(socket_, "session.ready")


def command_message(
    room_id: UUID,
    payload: CommandPayload,
    *,
    expected_revision: int,
    now: datetime,
) -> dict[str, Any]:
    envelope = CommandEnvelope(
        command_id=uuid4(),
        room_id=room_id,
        expected_revision=expected_revision,
        issued_at=now,
        payload=payload,
    )
    return {
        "type": "command",
        "command": envelope.model_dump(mode="json"),
    }


def submit_rejected(
    socket_: ClientConnection,
    room: RoomActor,
    payload: CommandPayload,
    *,
    expected_error: str,
) -> dict[str, Any]:
    message = command_message(
        room.room_id,
        payload,
        expected_revision=room.core.state.revision,
        now=room.clock(),
    )
    socket_.send(json.dumps(message))
    ack = receive_type(socket_, "command.ack")
    if ack.get("accepted") is not False or ack.get("error_code") != expected_error:
        raise AssertionError(f"expected rejected {expected_error}, got {ack!r}")
    return ack


def assert_no_forbidden_keys(value: object, path: str = "public_view") -> None:
    if isinstance(value, dict):
        mapping = cast(dict[str, object], value)
        forbidden = FORBIDDEN_PUBLIC_KEYS.intersection(mapping)
        if forbidden:
            raise AssertionError(f"{path} leaked private keys: {sorted(forbidden)}")
        for key, nested in mapping.items():
            assert_no_forbidden_keys(nested, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, nested in enumerate(cast(list[object], value)):
            assert_no_forbidden_keys(nested, f"{path}[{index}]")


def privacy_markers(state: GameState) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    role_values = frozenset(
        player.role.value for player in state.players if player.role is not None
    )
    private_fact_types = frozenset(fact.fact_type for fact in state.private_facts)
    role_claims = frozenset(
        f"{player.display_name} is {player.role.value}"
        for player in state.players
        if player.role is not None
    )
    return role_values, private_fact_types, role_claims


def assert_no_private_value_leak(
    value: object,
    *,
    role_values: frozenset[str],
    private_fact_types: frozenset[str],
    role_claims: frozenset[str],
    path: str = "public_view",
) -> None:
    if isinstance(value, dict):
        for key, nested in cast(dict[str, object], value).items():
            assert_no_private_value_leak(
                nested,
                role_values=role_values,
                private_fact_types=private_fact_types,
                role_claims=role_claims,
                path=f"{path}.{key}",
            )
        return
    if isinstance(value, list):
        for index, nested in enumerate(cast(list[object], value)):
            assert_no_private_value_leak(
                nested,
                role_values=role_values,
                private_fact_types=private_fact_types,
                role_claims=role_claims,
                path=f"{path}[{index}]",
            )
        return
    if not isinstance(value, str):
        return
    if value in role_values:
        raise AssertionError(f"{path} leaked a private role value: {value!r}")
    for marker in private_fact_types:
        if marker in value:
            raise AssertionError(f"{path} leaked private fact type {marker!r}")
    for claim in role_claims:
        if claim in value:
            raise AssertionError(f"{path} leaked private role claim {claim!r}")


def capture_public_message(
    message: dict[str, Any],
    state: GameState,
    public_count: int,
) -> int:
    if set(message) != {"type", "outbox_seq", "public_view"}:
        raise AssertionError(f"unexpected public message shape: {message!r}")
    public_view = cast(dict[str, Any], message["public_view"])
    if set(public_view) != PUBLIC_VIEW_KEYS:
        raise AssertionError(f"unexpected public view shape: {public_view!r}")
    assert_no_forbidden_keys(public_view)
    role_values, private_fact_types, role_claims = privacy_markers(state)
    assert_no_private_value_leak(
        public_view,
        role_values=role_values,
        private_fact_types=private_fact_types,
        role_claims=role_claims,
    )
    return public_count + 1


def assert_public_view_matches_state(
    public_view: dict[str, Any],
    *,
    revision: int,
    phase: str,
) -> None:
    if public_view["revision"] != revision:
        raise AssertionError("broadcast revision does not match current state")
    if public_view["phase"] != phase:
        raise AssertionError("broadcast phase does not match current state")


def receive_broadcasts(
    seats: list[ClientConnection],
    host: ClientConnection,
    room: RoomActor,
    *,
    started_at: float,
    public_count: int,
    latency_ms: list[float],
) -> int:
    for seat_index, seat in enumerate(seats):
        public = receive_type(seat, "public.view.updated")
        latency_ms.append((perf_counter() - started_at) * 1000.0)
        expected_revision = room.core.state.revision
        expected_phase = room.core.state.phase.value
        expected_paused = room.core.state.paused
        public_view = cast(dict[str, Any], public["public_view"])
        assert_public_view_matches_state(
            public_view,
            revision=expected_revision,
            phase=expected_phase,
        )
        public_count = capture_public_message(public, room.core.state, public_count)
        seat_view = receive_type(seat, "seat.view.updated")
        if seat_view["seat_id"] != seat_index + 1:
            raise AssertionError(f"seat {seat_index + 1} received {seat_view!r}")
        assert_public_view_matches_state(
            seat_view["seat_view"],
            revision=expected_revision,
            phase=expected_phase,
        )
        if seat_view["outbox_seq"] != public["outbox_seq"]:
            raise AssertionError("seat update and public update outbox_seq diverged")

    host_public = receive_type(host, "public.view.updated")
    assert_public_view_matches_state(
        cast(dict[str, Any], host_public["public_view"]),
        revision=expected_revision,
        phase=expected_phase,
    )
    public_count = capture_public_message(host_public, room.core.state, public_count)
    host_control = receive_type(host, "host.control.updated")
    assert_public_view_matches_state(
        host_control["host_control"]["public_view"],
        revision=expected_revision,
        phase=expected_phase,
    )
    if host_control["host_control"]["revision"] != room.core.state.revision:
        raise AssertionError("host control revision does not match current state")
    if host_control["host_control"]["paused"] is not expected_paused:
        raise AssertionError("host control pause flag does not match current state")
    if host_control["outbox_seq"] != host_public["outbox_seq"]:
        raise AssertionError("host update and public update outbox_seq diverged")
    return public_count


def submit_accepted(
    seats: list[ClientConnection],
    host: ClientConnection,
    sender: ClientConnection,
    room: RoomActor,
    payload: CommandPayload,
    *,
    public_count: int,
    latency_ms: list[float],
) -> tuple[dict[str, Any], int]:
    message = command_message(
        room.room_id,
        payload,
        expected_revision=room.core.state.revision,
        now=room.clock(),
    )
    started_at = perf_counter()
    sender.send(json.dumps(message))
    public_count = receive_broadcasts(
        seats,
        host,
        room,
        started_at=started_at,
        public_count=public_count,
        latency_ms=latency_ms,
    )
    ack = receive_type(sender, "command.ack")
    if ack.get("accepted") is not True:
        raise AssertionError(f"expected accepted command, got {ack!r}")
    return ack, public_count


def enqueue_timer_tick(
    loop: asyncio.AbstractEventLoop,
    room: RoomActor,
    *,
    deadline_at: datetime,
) -> None:
    state = room.core.state
    cast(FrozenClock, room.clock).set(deadline_at)
    future = asyncio.run_coroutine_threadsafe(
        room.enqueue_timer_tick(
            revision=state.revision,
            deadline_at=deadline_at,
            now=deadline_at,
        ),
        loop,
    )
    future.result(timeout=READ_TIMEOUT_SECONDS)


def seat_for_role(state: GameState, role: Role) -> int:
    matches = tuple(player.seat_id for player in state.players if player.role is role)
    if len(matches) != 1:
        raise AssertionError(f"expected one {role.value} seat, got {matches!r}")
    return matches[0]


def living_seat_with_roles(
    state: GameState,
    *,
    excluded_roles: frozenset[Role] = frozenset(),
) -> int:
    for player in state.players:
        if (
            player.alive
            and player.role is not None
            and player.role not in excluded_roles
            and player.role is not Role.WEREWOLF
        ):
            return player.seat_id
    raise AssertionError("no living target with the requested role")


def pass_discussion(
    seats: list[ClientConnection],
    host: ClientConnection,
    room: RoomActor,
    *,
    public_count: int,
    latency_ms: list[float],
) -> int:
    while room.core.state.phase in {Phase.DAY_DISCUSSION, Phase.DAY_PK_DISCUSSION}:
        discussion = room.core.state.discussion
        if discussion is None or discussion.current_seat_id is None:
            raise AssertionError("discussion is not initialized")
        speaker = discussion.current_seat_id
        _, public_count = submit_accepted(
            seats,
            host,
            seats[speaker - 1],
            room,
            PassSpeechCommand(),
            public_count=public_count,
            latency_ms=latency_ms,
        )
    return public_count


def submit_tied_normal_vote(
    seats: list[ClientConnection],
    host: ClientConnection,
    room: RoomActor,
    *,
    public_count: int,
    latency_ms: list[float],
) -> int:
    vote_round = room.core.state.vote_round
    if vote_round is None or room.core.state.phase is not Phase.DAY_VOTE:
        raise AssertionError("normal vote round is not open")
    eligible = vote_round.eligible_voter_ids
    if len(eligible) < 3:
        raise AssertionError("normal vote requires at least three eligible voters")
    candidate_one, candidate_two = eligible[:2]
    votes: dict[int, int | None] = {
        candidate_one: candidate_two,
        candidate_two: candidate_one,
    }
    remaining = tuple(voter for voter in eligible if voter not in {candidate_one, candidate_two})
    balanced_count = len(remaining) // 2
    for index, voter in enumerate(remaining):
        if index < balanced_count:
            votes[voter] = candidate_one
        elif index < balanced_count * 2:
            votes[voter] = candidate_two
        else:
            votes[voter] = None
    for voter_seat_id, target_seat_id in votes.items():
        _, public_count = submit_accepted(
            seats,
            host,
            seats[voter_seat_id - 1],
            room,
            (
                AbstainCommand()
                if target_seat_id is None
                else VoteCommand(target_seat_id=target_seat_id)
            ),
            public_count=public_count,
            latency_ms=latency_ms,
        )
    if room.core.state.phase is not Phase.DAY_PK_DISCUSSION:
        raise AssertionError("balanced normal vote did not enter PK discussion")
    return public_count


def submit_pk_tie(
    seats: list[ClientConnection],
    host: ClientConnection,
    room: RoomActor,
    *,
    public_count: int,
    latency_ms: list[float],
) -> int:
    vote_round = room.core.state.vote_round
    if vote_round is None or room.core.state.phase is not Phase.DAY_PK_VOTE:
        raise AssertionError("PK vote round is not open")
    eligible = vote_round.eligible_voter_ids
    if len(vote_round.candidate_seat_ids) != 2:
        raise AssertionError("PK vote requires exactly two candidates")
    candidate_one, candidate_two = vote_round.candidate_seat_ids
    votes: dict[int, int | None] = {}
    balanced_count = len(eligible) // 2
    for index, voter in enumerate(eligible):
        if index < balanced_count:
            votes[voter] = candidate_one
        elif index < balanced_count * 2:
            votes[voter] = candidate_two
        else:
            votes[voter] = None
    for voter_seat_id, target_seat_id in votes.items():
        _, public_count = submit_accepted(
            seats,
            host,
            seats[voter_seat_id - 1],
            room,
            (
                AbstainCommand()
                if target_seat_id is None
                else VoteCommand(target_seat_id=target_seat_id)
            ),
            public_count=public_count,
            latency_ms=latency_ms,
        )
    if room.core.state.phase is not Phase.NIGHT_WOLF:
        raise AssertionError("balanced PK vote did not enter the next night")
    return public_count


def submit_night_actions(
    seats: list[ClientConnection],
    host: ClientConnection,
    room: RoomActor,
    *,
    target_seat_id: int,
    public_count: int,
    latency_ms: list[float],
) -> int:
    state = room.core.state
    if state.phase is not Phase.NIGHT_WOLF:
        raise AssertionError("night wolf phase is not open")
    for player in state.players:
        if player.alive and player.role is Role.WEREWOLF:
            _, public_count = submit_accepted(
                seats,
                host,
                seats[player.seat_id - 1],
                room,
                WolfNominateKillCommand(target_seat_id=target_seat_id),
                public_count=public_count,
                latency_ms=latency_ms,
            )
    if room.core.state.phase is not Phase.NIGHT_SEER:
        raise AssertionError("wolves did not enter the seer phase")

    seer_id = seat_for_role(room.core.state, Role.SEER)
    seer_target = next(
        player.seat_id
        for player in room.core.state.players
        if player.alive and player.seat_id != seer_id
    )
    _, public_count = submit_accepted(
        seats,
        host,
        seats[seer_id - 1],
        room,
        SeerInspectCommand(target_seat_id=seer_target),
        public_count=public_count,
        latency_ms=latency_ms,
    )
    if room.core.state.phase is not Phase.NIGHT_WITCH:
        raise AssertionError("seer did not enter the witch phase")

    witch_id = seat_for_role(room.core.state, Role.WITCH)
    _, public_count = submit_accepted(
        seats,
        host,
        seats[witch_id - 1],
        room,
        WitchSkipCommand(),
        public_count=public_count,
        latency_ms=latency_ms,
    )
    return public_count


def assert_reconnect_snapshot(
    room: RoomActor,
    snapshot: dict[str, Any],
    *,
    seat_id: int,
) -> None:
    if snapshot["snapshot"]["revision"] != room.core.state.revision:
        raise AssertionError("reconnect revision is stale")
    if snapshot["snapshot"]["outbox_seq"] != room.outbox_seq:
        raise AssertionError("reconnect outbox_seq is stale")
    seat_view = snapshot["snapshot"]["seat_view"]
    if seat_view is None or seat_view["seat_id"] != seat_id:
        raise AssertionError("reconnect seat view is not bound to the requested seat")


def p95(values: list[float]) -> float:
    if not values:
        raise AssertionError("latency samples are empty")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def test_six_client_end_to_end_service_flow() -> None:
    registry = make_registry()
    with (
        running_server(registry) as (_, loop, port),
        httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            trust_env=False,
            timeout=READ_TIMEOUT_SECONDS,
        ) as http,
    ):
        created_response = http.post("/rooms", json={"display_name": "Host"})
        created_response.raise_for_status()
        created = created_response.json()
        room_id = UUID(created["room_id"])
        joined = []
        for seat_id in range(1, 7):
            response = http.post(
                f"/rooms/{created['room_code']}/join",
                json={"display_name": f"P{seat_id}"},
            )
            response.raise_for_status()
            joined.append(response.json())

        room = registry.get_by_code(created["room_code"])
        public_count = 0
        latency_ms: list[float] = []

        with ExitStack() as stack:
            seats: list[ClientConnection] = []
            for seat_id, joined_seat in enumerate(joined, start=1):
                socket_, ready = open_authenticated_client(
                    stack,
                    port,
                    joined_seat["seat_token"],
                )
                seats.append(socket_)
                seat_view = ready["snapshot"]["seat_view"]
                if seat_view is None or seat_view["seat_id"] != seat_id:
                    raise AssertionError("session.ready seat binding is incorrect")
                if ready["snapshot"]["host_control"] is not None:
                    raise AssertionError("seat session received host control")

            host, host_ready = open_authenticated_client(
                stack,
                port,
                created["host_token"],
            )
            if (
                host_ready["snapshot"]["host_control"] is None
                or host_ready["snapshot"]["seat_view"] is not None
            ):
                raise AssertionError("host session.ready shape is incorrect")

            submit_rejected(
                seats[0],
                room,
                JoinRoomCommand(seat_id=2, display_name="P1"),
                expected_error="ACTOR_NOT_AUTHORIZED",
            )
            for seat_id, socket_ in enumerate(seats, start=1):
                _, public_count = submit_accepted(
                    seats,
                    host,
                    socket_,
                    room,
                    JoinRoomCommand(seat_id=seat_id, display_name=f"P{seat_id}"),
                    public_count=public_count,
                    latency_ms=latency_ms,
                )
            if tuple(player.seat_id for player in room.core.state.players) != tuple(range(1, 7)):
                raise AssertionError("JOIN_ROOM did not bind all six token seats")

            for socket_ in seats:
                _, public_count = submit_accepted(
                    seats,
                    host,
                    socket_,
                    room,
                    SetReadyCommand(ready=True),
                    public_count=public_count,
                    latency_ms=latency_ms,
                )
            if room.core.state.phase is not Phase.ROLE_REVEAL:
                raise AssertionError("all-ready did not enter role reveal")

            revision_before_reconnect = room.core.state.revision
            hash_before_reconnect = state_hash(room.core.state)
            seats[1].close()
            seats[1], ready = open_authenticated_client(
                stack,
                port,
                joined[1]["seat_token"],
            )
            assert_reconnect_snapshot(room, ready, seat_id=2)
            if (
                room.core.state.revision != revision_before_reconnect
                or state_hash(room.core.state) != hash_before_reconnect
            ):
                raise AssertionError("reconnect changed game state")

            _, public_count = submit_accepted(
                seats,
                host,
                host,
                room,
                HostPauseCommand(reason="e2e"),
                public_count=public_count,
                latency_ms=latency_ms,
            )
            paused_state = room.core.state
            if (
                not paused_state.paused
                or paused_state.deadline_at is None
                or paused_state.paused_at is None
            ):
                raise AssertionError("host pause did not pause the room")
            original_deadline = paused_state.deadline_at
            paused_at = paused_state.paused_at
            cast(FrozenClock, room.clock).set(original_deadline)
            enqueue_timer_tick(loop, room, deadline_at=original_deadline)

            resume_clock = original_deadline + timedelta(seconds=7)
            cast(FrozenClock, room.clock).set(resume_clock)
            _, public_count = submit_accepted(
                seats,
                host,
                host,
                room,
                HostResumeCommand(),
                public_count=public_count,
                latency_ms=latency_ms,
            )
            if room.core.state.paused:
                raise AssertionError("host resume left the room paused")
            shifted_deadline = room.core.state.deadline_at
            expected_shift = resume_clock - paused_at
            if shifted_deadline != original_deadline + expected_shift:
                raise AssertionError("host resume did not shift the timer deadline")

            started_at = perf_counter()
            enqueue_timer_tick(loop, room, deadline_at=shifted_deadline)
            public_count = receive_broadcasts(
                seats,
                host,
                room,
                started_at=started_at,
                public_count=public_count,
                latency_ms=latency_ms,
            )
            if room.core.state.phase is not Phase.NIGHT_WOLF:
                raise AssertionError("explicit timer tick did not enter night wolf")

            revision_before_reconnect = room.core.state.revision
            hash_before_reconnect = state_hash(room.core.state)
            seats[4].close()
            seats[4], ready = open_authenticated_client(
                stack,
                port,
                joined[4]["seat_token"],
            )
            assert_reconnect_snapshot(room, ready, seat_id=5)
            if (
                room.core.state.revision != revision_before_reconnect
                or state_hash(room.core.state) != hash_before_reconnect
            ):
                raise AssertionError("second reconnect changed game state")

            first_kill_target = living_seat_with_roles(
                room.core.state,
                excluded_roles=frozenset({Role.WITCH, Role.SEER}),
            )
            public_count = submit_night_actions(
                seats,
                host,
                room,
                target_seat_id=first_kill_target,
                public_count=public_count,
                latency_ms=latency_ms,
            )
            if room.core.state.phase is not Phase.DAY_DISCUSSION:
                raise AssertionError("night one did not enter day discussion")

            public_count = pass_discussion(
                seats,
                host,
                room,
                public_count=public_count,
                latency_ms=latency_ms,
            )
            if room.core.state.phase is not Phase.DAY_VOTE:
                raise AssertionError("day one discussion did not enter vote")
            public_count = submit_tied_normal_vote(
                seats,
                host,
                room,
                public_count=public_count,
                latency_ms=latency_ms,
            )
            public_count = pass_discussion(
                seats,
                host,
                room,
                public_count=public_count,
                latency_ms=latency_ms,
            )
            public_count = submit_pk_tie(
                seats,
                host,
                room,
                public_count=public_count,
                latency_ms=latency_ms,
            )
            if room.core.state.day != 2:
                raise AssertionError("PK tie did not advance to day two")

            second_kill_target = living_seat_with_roles(
                room.core.state,
                excluded_roles=frozenset({Role.WITCH, Role.SEER}),
            )
            public_count = submit_night_actions(
                seats,
                host,
                room,
                target_seat_id=second_kill_target,
                public_count=public_count,
                latency_ms=latency_ms,
            )
            if room.core.state.phase is not Phase.GAME_END:
                raise AssertionError("second night did not reach GAME_END")

            final_state = room.core.state
            if final_state.winner is None:
                raise AssertionError("GAME_END did not record a winner")

            pairing_response = http.post(
                f"/rooms/{created['room_code']}/display-pairings",
                headers={"Authorization": f"Bearer {created['host_token']}"},
            )
            pairing_response.raise_for_status()
            display_response = http.post(
                f"/rooms/{created['room_code']}/display-sessions",
                json={"pairing_code": pairing_response.json()["pairing_code"]},
            )
            display_response.raise_for_status()
            display, display_ready = open_authenticated_client(
                stack,
                port,
                display_response.json()["display_token"],
            )
            if (
                display_ready["snapshot"]["seat_view"] is not None
                or display_ready["snapshot"]["host_control"] is not None
                or display_ready["snapshot"]["public_view"] is None
            ):
                raise AssertionError("display session.ready shape is incorrect")
            revision_before_display_command = room.core.state.revision
            display.send(
                json.dumps(
                    command_message(
                        room.room_id,
                        SetReadyCommand(ready=True),
                        expected_revision=room.core.state.revision,
                        now=room.clock(),
                    )
                )
            )
            display_error = receive_type(display, "error")
            if display_error.get("code") != "ACTOR_NOT_AUTHORIZED":
                raise AssertionError(f"display command was not rejected: {display_error!r}")
            if room.core.state.revision != revision_before_display_command:
                raise AssertionError("display command changed game state")

            replay_response = http.get(
                f"/rooms/{created['room_code']}/replay",
                headers={"Authorization": f"Bearer {joined[0]['seat_token']}"},
            )
            replay_response.raise_for_status()
            replay = replay_response.json()
            audit_response = http.get(
                f"/rooms/{created['room_code']}/audit",
                headers={"Authorization": f"Bearer {created['host_token']}"},
            )
            audit_response.raise_for_status()
            audit = audit_response.json()

            player_actor = AuthenticatedActor(
                actor_type="seat",
                seat_id=1,
                room_id=room_id,
            )
            host_actor = AuthenticatedActor(
                actor_type="host",
                seat_id=None,
                room_id=room_id,
            )
            expected_replay = project_player_replay(
                final_state,
                room.core.events,
                player_actor,
            )
            expected_audit = project_host_audit(
                final_state,
                room.core.events,
                host_actor,
            )
            if replay != expected_replay.model_dump(mode="json"):
                raise AssertionError("player replay does not match final state projection")
            if audit != expected_audit.model_dump(mode="json"):
                raise AssertionError("host audit does not match final state projection")

            audit_state = GameState.validate_json_payload(audit["state"])
            if audit_state != final_state or state_hash(audit_state) != state_hash(final_state):
                raise AssertionError("host audit state does not match final state")
            if (
                replay["revision"] != final_state.revision
                or audit["revision"] != final_state.revision
            ):
                raise AssertionError("replay revision does not match final state")
            if replay["room_id"] != str(room_id) or audit["room_id"] != str(room_id):
                raise AssertionError("replay room binding is incorrect")
            if replay["public_timeline"] != final_state.model_dump(mode="json")["public_timeline"]:
                raise AssertionError("player replay timeline does not match final state")

            if public_count < 20:
                raise AssertionError("real WebSocket flow produced too few public updates")
            measured_p95 = p95(latency_ms)
            if len(latency_ms) < 100:
                raise AssertionError("real WebSocket flow produced too few latency samples")
            print(
                "LAT-004 real six-client WebSocket broadcast "
                f"p95={measured_p95:.3f}ms samples={len(latency_ms)}"
            )
            if measured_p95 >= 300:
                raise AssertionError(f"LAT-004 six-client broadcast p95={measured_p95:.3f}ms")
