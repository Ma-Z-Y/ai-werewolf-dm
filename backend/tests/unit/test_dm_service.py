import inspect
import os
import socket
from uuid import UUID

import pytest

from werewolf_dm.application.dm_contracts import (
    DMAnnouncementSlot,
    TemplateCatalog,
    TemplateFact,
    TemplateIntent,
    TemplateRenderResult,
)
from werewolf_dm.application.dm_service import TemplateDMService, TemplateRenderError
from werewolf_dm.application.dm_templates import TemplateRegistry
from werewolf_dm.domain.enums import Phase

CATALOG_VERSION = "s4-template-v1"
ROOM_ID = UUID("00000000-0000-0000-0000-000000000001")
INTENT_ID = UUID("00000000-0000-0000-0000-000000000301")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000101")
OTHER_EVENT_ID = UUID("00000000-0000-0000-0000-000000000102")
FACT_ID = UUID("00000000-0000-0000-0000-000000000401")


class EmptyRegistry(TemplateRegistry):
    def catalog(self, catalog_version: str) -> TemplateCatalog:
        raise ValueError("CATALOG_NOT_FOUND")


def _intent() -> TemplateIntent:
    return TemplateIntent(
        intent_id=INTENT_ID,
        source_event_id=EVENT_ID,
        source_revision=3,
        source_phase=Phase.DAY_ANNOUNCE,
        catalog_version=CATALOG_VERSION,
        category="DEATH_NOTICE",
        channel="public",
        audience_seat_ids=(),
        audience_bindings=(),
        template_variant_id="s4-public-death-formal-v1",
        style="formal",
        source_event_ids=(EVENT_ID,),
        allowed_fact_kinds=frozenset({"player_died"}),
    )


def _facts(*, source_event_id: UUID = EVENT_ID) -> list[TemplateFact]:
    return [
        TemplateFact(
            fact_id=FACT_ID,
            source_event_id=source_event_id,
            visibility="public",
            audience_seat_ids=(),
            kind="player_died",
            fields={"seat_ids": "2,4", "count": 2},
        )
    ]


def _slot() -> DMAnnouncementSlot:
    return DMAnnouncementSlot(
        domain_seq=1,
        room_id=ROOM_ID,
        revision=3,
        trigger_at_monotonic_ms=1000,
        admission_deadline_monotonic_ms=3000,
    )


def test_service_resolves_without_provider_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("service must not use environment or network")

    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    service = TemplateDMService(registry=TemplateRegistry())

    result = service.resolve(_intent(), _facts(), _slot(), CATALOG_VERSION)

    assert result.source == "template"
    assert result.final_text == "昨夜,2,4 号玩家出局。"


def test_service_fails_closed_when_template_is_missing() -> None:
    service = TemplateDMService(registry=EmptyRegistry())

    with pytest.raises(TemplateRenderError, match="TEMPLATE_MISSING"):
        service.resolve(_intent(), _facts(), _slot(), CATALOG_VERSION)


def test_service_is_deterministic_for_same_input() -> None:
    service = TemplateDMService(registry=TemplateRegistry())

    first = service.resolve(_intent(), _facts(), _slot(), CATALOG_VERSION)
    second = service.resolve(_intent(), _facts(), _slot(), CATALOG_VERSION)

    assert first.model_dump_json().encode() == second.model_dump_json().encode()


def test_service_rejects_catalog_version_mismatch() -> None:
    service = TemplateDMService(registry=EmptyRegistry())

    with pytest.raises(TemplateRenderError, match="INVALID_CATALOG"):
        service.resolve(_intent(), _facts(), _slot(), "other-catalog-version")


def test_service_wraps_renderer_failures_as_closed_errors() -> None:
    service = TemplateDMService(registry=TemplateRegistry())

    with pytest.raises(TemplateRenderError, match="FACT_NOT_ALLOWED"):
        service.resolve(
            _intent(),
            _facts(source_event_id=OTHER_EVENT_ID),
            _slot(),
            CATALOG_VERSION,
        )


def test_service_resolve_is_synchronous() -> None:
    service = TemplateDMService(registry=TemplateRegistry())

    result = service.resolve(_intent(), _facts(), _slot(), CATALOG_VERSION)

    assert not inspect.iscoroutinefunction(service.resolve)
    assert isinstance(result, TemplateRenderResult)
    assert not inspect.isawaitable(result)
