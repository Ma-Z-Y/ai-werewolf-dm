import hashlib
import json

from werewolf_dm.domain.contracts import DomainEvent
from werewolf_dm.domain.model import GameState

EMPTY_EVENT_LOG_DIGEST = "0" * 64


def advance_event_log_digest(
    previous_digest: str,
    events: tuple[DomainEvent, ...],
) -> str:
    digest = previous_digest
    for event in events:
        canonical = json.dumps(
            event.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        next_digest = hashlib.sha256()
        next_digest.update(digest.encode("ascii"))
        next_digest.update(canonical.encode("ascii"))
        digest = next_digest.hexdigest()
    return digest


def event_log_digest(events: tuple[DomainEvent, ...]) -> str:
    return advance_event_log_digest(EMPTY_EVENT_LOG_DIGEST, events)


def state_hash(state: GameState) -> str:
    canonical = {
        "rulepack_version": state.rulepack_version,
        "revision": state.revision,
        "current_state": state.phase.value,
        "ordered_player_states": [
            {
                "seat_id": player.seat_id,
                "alive": player.alive,
                "role": player.role.value if player.role else None,
            }
            for player in sorted(state.players, key=lambda player: player.seat_id)
        ],
        "potion_states": state.potions.model_dump(mode="json"),
        "vote_rounds": state.vote_round.model_dump(mode="json") if state.vote_round else None,
        "winner": state.winner.value if state.winner else None,
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()
