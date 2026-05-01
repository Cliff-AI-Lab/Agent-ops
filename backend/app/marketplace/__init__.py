"""Marketplace + Asset Hub (Batch X).

Records every successful Generation as a GeneratedAsset, persists the zip on
disk, writes a sibling agent.yaml under ``agents/__generated__/{asset_id}/``,
and exposes promotion + discovery so platform-generated agents become
first-class citizens of the RegistryHub (self-reinforcing loop).
"""
from app.marketplace.asset_store import (
    AssetStore,
    bootstrap_asset_store,
    default_assets_root,
    get_asset_store,
    reset_asset_store,
)

__all__ = [
    "AssetStore",
    "get_asset_store",
    "reset_asset_store",
    "bootstrap_asset_store",
    "default_assets_root",
]
