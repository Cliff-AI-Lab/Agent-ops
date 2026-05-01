"""ProductType + companions — flat top-level module, importable from anywhere
without triggering the ``app.ontology`` package init (which would pull in
``app.core.stability.contracts`` and create a circular import)."""
from __future__ import annotations

from typing import Literal

ProductType = Literal[
    "app",         # full application: multi-page + auth + data + API
    "dashboard",   # admin / analytics dashboard
    "page",        # single page / landing / marketing
    "component",   # reusable UI component(s)
    "tool",        # function-style tool, no or minimal UI
    "agent",       # agent runtime artifact
    "report",      # generated document / report
]

ALL_PRODUCT_TYPES: tuple[ProductType, ...] = (
    "app", "dashboard", "page", "component", "tool", "agent", "report",
)

UI_PRODUCT_TYPES: tuple[ProductType, ...] = ("app", "dashboard", "page", "component")
