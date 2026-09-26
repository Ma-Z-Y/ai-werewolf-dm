import os
from datetime import UTC, datetime, timedelta

import uvicorn
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from werewolf_dm.application.rooms import RoomRegistry, SequenceTokenSource
from werewolf_dm.interfaces.http_ws.app import create_app


class TestClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def __call__(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)


control_token = os.environ["E2E_CONTROL_TOKEN"]
clock = TestClock(datetime(2099, 1, 1, tzinfo=UTC))
source = SequenceTokenSource(
    tokens=tuple(f"token-{index}" for index in range(200)),
    room_codes=tuple(f"ROOM{index:02d}" for index in range(10)),
)
registry = RoomRegistry(
    clock=clock,
    token_source=source,
    seed_source=lambda: 1001,
)


class AdvanceRequest(BaseModel):
    seconds: float = Field(gt=0, le=3600)


def build_test_control_router(
    room_registry: RoomRegistry,
    test_clock: TestClock,
    expected_control_token: str,
) -> APIRouter:
    router = APIRouter()

    @router.post("/__test__/rooms/{room_code}/advance")
    async def advance_room(
        room_code: str,
        body: AdvanceRequest,
        request: Request,
    ) -> dict[str, str]:
        if request.headers.get("X-Test-Control") != expected_control_token:
            raise HTTPException(status_code=401, detail="UNAUTHORIZED")
        room = room_registry.get_by_code(room_code)
        test_clock.advance(seconds=body.seconds)
        deadline_at = room.core.state.deadline_at
        if deadline_at is not None:
            await room.enqueue_timer_tick(
                revision=room.core.state.revision,
                deadline_at=deadline_at,
                now=test_clock(),
            )
        return {"status": "ok"}

    return router


app = create_app(registry=registry)
app.include_router(build_test_control_router(registry, clock, control_token))

uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
