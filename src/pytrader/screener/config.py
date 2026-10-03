"""Ultima configurazione dello screener, salvata in ``~/.pytrader/screener.json``."""

from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

from pytrader.screener.features import ScreenerParams
from pytrader.services import SourceKind
from pytrader.storage import app_data_dir, read_json, write_json_atomic


@dataclass
class ScreenerConfig:
    kind: SourceKind = SourceKind.YFINANCE
    exchange: str = "binance"
    timeframe: str = "1d"
    bars: int = 1000
    symbols: str = ""
    params: ScreenerParams = field(default_factory=ScreenerParams)

    @staticmethod
    def default_path() -> Path:
        return app_data_dir() / "screener.json"

    @classmethod
    def load(cls, path: Optional[Path] = None) -> ScreenerConfig:
        """Valori mancanti o non validi tornano ai default: un file rovinato non blocca l'app."""
        raw = read_json(path or cls.default_path(), {})
        cfg = cls()
        if not isinstance(raw, dict):
            return cfg
        with contextlib.suppress(ValueError, TypeError):
            kind = SourceKind(raw.get("kind", cfg.kind.value))
            if kind is not SourceKind.CSV:
                cfg.kind = kind
        for name in ("exchange", "timeframe", "symbols"):
            if isinstance(raw.get(name), str):
                setattr(cfg, name, raw[name])
        if isinstance(raw.get("bars"), int):
            cfg.bars = raw["bars"]
        params = raw.get("params")
        if isinstance(params, dict):
            known = {f.name for f in fields(ScreenerParams)}
            values: dict[str, Any] = {k: v for k, v in params.items() if k in known}
            with contextlib.suppress(ValueError, TypeError):
                cfg.params = ScreenerParams(**values)
        return cfg

    def save(self, path: Optional[Path] = None) -> None:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        write_json_atomic(path or self.default_path(), payload)
