from werewolf_dm.application.dm_contracts import (
    DMAnnouncementSlot,
    TemplateFact,
    TemplateIntent,
    TemplateRenderRequest,
    TemplateRenderResult,
)
from werewolf_dm.application.dm_templates import TemplateRegistry, render_template

_MISSING_TEMPLATE_CODES = frozenset(
    {
        "TEMPLATE_NOT_FOUND",
        "CATALOG_NOT_FOUND",
        "TEMPLATE_VARIANT_NOT_FOUND",
    }
)


class TemplateRenderError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TemplateDMService:
    def __init__(self, registry: TemplateRegistry) -> None:
        self._registry = registry

    def resolve(
        self,
        intent: TemplateIntent,
        facts: list[TemplateFact],
        slot: DMAnnouncementSlot,
        catalog_version: str,
    ) -> TemplateRenderResult:
        del slot
        if catalog_version != intent.catalog_version:
            raise TemplateRenderError("INVALID_CATALOG")

        try:
            catalog = self._registry.catalog(catalog_version)
            request = TemplateRenderRequest(
                intent=intent,
                catalog_version=catalog_version,
                facts=tuple(facts),
            )
            return render_template(request, catalog)
        except TemplateRenderError:
            raise
        except ValueError as error:
            code = str(error)
            if code in _MISSING_TEMPLATE_CODES:
                code = "TEMPLATE_MISSING"
            raise TemplateRenderError(code) from error
