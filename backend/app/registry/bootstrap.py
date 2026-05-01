"""Registry bootstrap — load YAML capabilities at startup."""
from __future__ import annotations

import logging
from pathlib import Path

from app.registry.loader import load_capabilities_from_dir
from app.registry.store import get_store

_LOG = logging.getLogger("agent_ops.registry")


def default_capabilities_dir() -> Path:
    """Resolve the project-level ``capabilities/`` directory."""
    # backend/app/registry/bootstrap.py  →  parents[3] = project root
    return Path(__file__).resolve().parents[3] / "capabilities"


def bootstrap_registry(capabilities_dir: Path | None = None) -> int:
    """Load every YAML capability under ``capabilities/`` into the global store.

    Returns the number of contracts registered.
    """
    root = capabilities_dir or default_capabilities_dir()
    contracts = load_capabilities_from_dir(root)
    registered = get_store().register_many(contracts)
    if registered:
        _LOG.info("registered %d capabilities from %s", registered, root)
    else:
        _LOG.warning("no capabilities registered (dir=%s)", root)
    return registered
