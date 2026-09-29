"""Verifica e download degli aggiornamenti da GitHub Releases (nessuna dipendenza Qt)."""

from pytrader.updater.checker import (
    ReleaseAsset,
    ReleaseInfo,
    UpdateError,
    download_asset,
    fetch_latest_release,
    is_newer,
    launch_installer,
    parse_version,
    select_asset,
)

__all__ = [
    "ReleaseAsset",
    "ReleaseInfo",
    "UpdateError",
    "download_asset",
    "fetch_latest_release",
    "is_newer",
    "launch_installer",
    "parse_version",
    "select_asset",
]
