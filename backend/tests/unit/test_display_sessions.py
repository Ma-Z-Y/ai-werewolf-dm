from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from tests.clocks import MutableClock
from werewolf_dm.application.rooms import SequenceTokenSource, TokenRecord, TokenService
from werewolf_dm.domain.contracts import AuthenticatedActor


def test_display_actor_cannot_carry_seat_id() -> None:
    with pytest.raises(ValidationError):
        AuthenticatedActor(
            actor_type="display",
            seat_id=1,
            room_id=uuid4(),
        )


def test_issuing_new_display_token_replaces_old_token() -> None:
    room_id = uuid4()
    clock = MutableClock(datetime(2026, 9, 25, tzinfo=UTC))
    tokens = TokenService(SequenceTokenSource(("old", "new"), ()), clock)

    old_raw, _ = tokens.issue_display(room_id, timedelta(hours=1))
    old_session_id = tokens.resolve(old_raw).session_id
    new_raw, _ = tokens.issue_display(room_id, timedelta(hours=1))

    assert tokens.display_record(room_id).token_digest == tokens.digest(new_raw)
    assert tokens.resolve(new_raw).session_id != old_session_id
    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        tokens.resolve(old_raw)
    assert tokens.resolve(new_raw).actor_type == "display"


def test_revoke_display_removes_record() -> None:
    room_id = uuid4()
    tokens = TokenService(
        SequenceTokenSource(("display",), ()),
        MutableClock(datetime(2026, 9, 25, tzinfo=UTC)),
    )
    raw, _ = tokens.issue_display(room_id, timedelta(hours=1))

    tokens.revoke_display(room_id)

    assert tokens.display_record(room_id) is None
    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        tokens.resolve(raw)


def test_failed_display_issue_keeps_previous_session() -> None:
    room_id = uuid4()
    tokens = TokenService(
        SequenceTokenSource(("old",), ()),
        MutableClock(datetime(2026, 9, 25, tzinfo=UTC)),
    )
    old_raw, _ = tokens.issue_display(room_id, timedelta(hours=1))

    with pytest.raises(StopIteration):
        tokens.issue_display(room_id, timedelta(hours=1))

    assert tokens.resolve(old_raw).session_id is not None


@pytest.mark.parametrize(
    "shape",
    [
        {"actor_type": "seat", "seat_id": None, "session_id": None},
        {"actor_type": "seat", "seat_id": 1, "session_id": uuid4()},
        {"actor_type": "host", "seat_id": 1, "session_id": None},
        {"actor_type": "host", "seat_id": None, "session_id": uuid4()},
        {"actor_type": "display", "seat_id": 1, "session_id": uuid4()},
        {"actor_type": "display", "seat_id": None, "session_id": None},
    ],
)
def test_token_record_enforces_actor_session_shape(
    shape: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TokenRecord(
            token_digest="digest",
            room_id=uuid4(),
            issued_at=datetime(2026, 9, 25, tzinfo=UTC),
            expires_at=datetime(2026, 9, 25, 1, tzinfo=UTC),
            **shape,
        )


def test_remove_room_cleans_display_record() -> None:
    room_id = uuid4()
    tokens = TokenService(
        SequenceTokenSource(("display",), ()),
        MutableClock(datetime(2026, 9, 25, tzinfo=UTC)),
    )
    raw, _ = tokens.issue_display(room_id, timedelta(hours=1))

    tokens.remove_room(room_id)

    assert tokens.display_record(room_id) is None
    with pytest.raises(ValueError, match="TOKEN_INVALID"):
        tokens.resolve(raw)
