"""Logica di aggiornamento: API GitHub Releases, confronto versioni, download e avvio installer.

Tutte le funzioni sono bloccanti: la GUI le esegue in un worker del ``QThreadPool``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pytrader.version import APP_NAME, GITHUB_REPO

_API_URL = "https://api.github.com/repos/{repo}/releases/latest"
_VERSION_RE = re.compile(r"^\s*v?(\d+(?:\.\d+)*)")


class UpdateError(RuntimeError):
    """Errore durante verifica o download dell'aggiornamento."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    notes: str
    html_url: str
    assets: list[ReleaseAsset] = field(default_factory=list)


def parse_version(text: str) -> tuple[int, ...]:
    """``"v1.2.3"`` -> ``(1, 2, 3)``; i suffissi (``-beta``) sono ignorati."""
    match = _VERSION_RE.match(text)
    if match is None:
        raise ValueError(f"Versione non valida: {text!r}")
    parts = [int(x) for x in match.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    try:
        return parse_version(remote) > parse_version(local)
    except ValueError:
        return False


def _parse_release(payload: dict[str, Any]) -> ReleaseInfo:
    tag = str(payload.get("tag_name", ""))
    assets = [
        ReleaseAsset(
            name=str(a.get("name", "")),
            url=str(a.get("browser_download_url", "")),
            size=int(a.get("size", 0)),
        )
        for a in payload.get("assets", [])
    ]
    return ReleaseInfo(
        version=tag.lstrip("v"),
        tag=tag,
        notes=str(payload.get("body") or ""),
        html_url=str(payload.get("html_url", "")),
        assets=assets,
    )


def fetch_latest_release(repo: str = GITHUB_REPO, timeout: float = 10.0) -> ReleaseInfo:
    request = urllib.request.Request(
        _API_URL.format(repo=repo),
        headers={"Accept": "application/vnd.github+json", "User-Agent": APP_NAME},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Nessuna release pubblicata") from exc
        raise UpdateError(f"Errore HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(f"Verifica aggiornamenti non riuscita: {exc}") from exc
    return _parse_release(payload)


def select_asset(release: ReleaseInfo, platform: str = sys.platform) -> Optional[ReleaseAsset]:
    """Asset adatto alla piattaforma: installer NSIS su Windows, archivio altrove."""
    if platform.startswith("win"):
        wanted: Callable[[str], bool] = lambda n: n.endswith("-setup.exe")  # noqa: E731
    elif platform == "darwin":
        wanted = lambda n: "macos" in n  # noqa: E731
    else:
        wanted = lambda n: "linux" in n  # noqa: E731
    return next((a for a in release.assets if wanted(a.name.lower())), None)


def download_asset(
    asset: ReleaseAsset,
    dest_dir: Path,
    progress: Optional[Callable[[int], None]] = None,
    timeout: float = 30.0,
) -> Path:
    """Scarica l'asset in ``dest_dir``; ``progress`` riceve la percentuale 0-100."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / Path(asset.name).name
    request = urllib.request.Request(asset.url, headers={"User-Agent": APP_NAME})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, target.open("wb") as fh:
            total = int(response.headers.get("Content-Length") or asset.size or 0)
            done = 0
            while chunk := response.read(64 * 1024):
                fh.write(chunk)
                done += len(chunk)
                if progress is not None and total:
                    progress(min(100, done * 100 // total))
    except (urllib.error.URLError, OSError) as exc:
        target.unlink(missing_ok=True)
        raise UpdateError(f"Download non riuscito: {exc}") from exc
    return target


def launch_installer(path: Path) -> bool:
    """Avvia l'installer (solo Windows). ``False`` se la piattaforma richiede azione manuale."""
    if not sys.platform.startswith("win"):
        return False
    try:
        subprocess.Popen([str(path)], close_fds=True)  # noqa: S603
    except OSError as exc:
        raise UpdateError(f"Impossibile avviare l'installer: {exc}") from exc
    return True
