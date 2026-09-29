"""Entry point: ``python src/main.py``."""

from __future__ import annotations

import sys
from pathlib import Path

# Consente l'avvio diretto senza installare il pacchetto
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pytrader.gui.app import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run())
