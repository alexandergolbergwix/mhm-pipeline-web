"""wikibaseintegrator helpers missing from the upstream package."""

from __future__ import annotations

from typing import Any

from wikibaseintegrator.datatypes.basedatatype import BaseDataType


class Boolean(BaseDataType):
    """Wikibase ``boolean`` snak — upstream wikibaseintegrator omits this type."""

    DTYPE = "boolean"

    def __init__(self, value: bool | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.set_value(value=value)

    def set_value(self, value: bool | None = None) -> None:
        if value is None:
            return
        if not isinstance(value, bool):
            raise TypeError(f"Expected bool, found {type(value)} ({value!r})")
        self.mainsnak.datavalue = {
            "value": "1" if value else "0",
            "type": "boolean",
        }
