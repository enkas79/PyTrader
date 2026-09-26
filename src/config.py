"""Configurazione centralizzata del bot.

Tutti i parametri sono letti da variabili d'ambiente (opzionalmente caricate
da un file ``.env`` tramite ``python-dotenv``) e validati all'avvio. Le
credenziali API non devono mai essere scritte nel codice sorgente.

Esempio minimo di ``.env``::

    EXCHANGE_ID=binanceusdm
    SYMBOL=BTC/USDT:USDT
    API_KEY=...
    API_SECRET=...
    DRY_RUN=false
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

try:  # python-dotenv è opzionale: se manca si usano solo le env vars.
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]


#: Versione del progetto (Semantic Versioning 2.0.0).
__version__: Final[str] = "0.3.0"

#: Radice del repository (i sorgenti sono in ``src/``).
BASE_DIR: Final[Path] = Path(__file__).resolve().parent.parent

#: Durata in secondi dei timeframe supportati dal loop di sincronizzazione.
TIMEFRAME_SECONDS: Final[dict[str, int]] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
}


class ConfigError(ValueError):
    """Sollevata quando la configurazione è incoerente o incompleta."""


def _env_str(name: str, default: str) -> str:
    """Legge una stringa dall'ambiente.

    Args:
        name: Nome della variabile d'ambiente.
        default: Valore restituito se la variabile non è definita.

    Returns:
        Il valore della variabile, privo di spazi iniziali/finali.
    """
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    """Legge un booleano dall'ambiente (``1/true/yes/on`` → ``True``).

    Args:
        name: Nome della variabile d'ambiente.
        default: Valore restituito se la variabile non è definita.

    Returns:
        Il valore booleano interpretato.

    Raises:
        ConfigError: Se il valore non è riconoscibile come booleano.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name}={raw!r} non è un booleano valido")


def _env_float(name: str, default: float) -> float:
    """Legge un float dall'ambiente.

    Args:
        name: Nome della variabile d'ambiente.
        default: Valore restituito se la variabile non è definita.

    Returns:
        Il valore convertito in ``float``.

    Raises:
        ConfigError: Se il valore non è numerico.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} non è un numero valido") from exc


def _env_int(name: str, default: int) -> int:
    """Legge un intero dall'ambiente.

    Args:
        name: Nome della variabile d'ambiente.
        default: Valore restituito se la variabile non è definita.

    Returns:
        Il valore convertito in ``int``.

    Raises:
        ConfigError: Se il valore non è un intero.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}={raw!r} non è un intero valido") from exc


@dataclass(frozen=True, slots=True)
class ExchangeConfig:
    """Parametri di connessione all'exchange.

    Attributes:
        exchange_id: Identificativo ccxt dell'exchange (es. ``binanceusdm``).
        symbol: Simbolo unificato ccxt. Per i perpetual USDT-margined usare
            la forma ``BTC/USDT:USDT``; per lo spot ``BTC/USDT``.
        api_key: Chiave API (vuota in modalità paper).
        api_secret: Secret API (vuoto in modalità paper).
        api_password: Passphrase, richiesta solo da alcuni exchange (OKX, KuCoin).
        sandbox: Se ``True`` usa la testnet dell'exchange (se disponibile).
        leverage: Leva da impostare sul simbolo (solo derivati).
        margin_mode: ``isolated`` o ``cross`` (solo derivati).
    """

    exchange_id: str = "binanceusdm"
    symbol: str = "BTC/USDT:USDT"
    api_key: str = field(default="", repr=False)
    api_secret: str = field(default="", repr=False)
    api_password: str = field(default="", repr=False)
    sandbox: bool = False
    leverage: int = 5
    margin_mode: str = "isolated"

    @property
    def is_spot(self) -> bool:
        """``True`` se il simbolo è spot (nessun suffisso di settlement ``:``)."""
        return ":" not in self.symbol

    @property
    def quote_currency(self) -> str:
        """Valuta di quotazione del simbolo (es. ``USDT``)."""
        return self.symbol.split("/")[1].split(":")[0]

    @property
    def base_currency(self) -> str:
        """Asset base del simbolo (es. ``BTC``)."""
        return self.symbol.split("/")[0]


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """Parametri matematici della strategia Donchian + VWAP.

    Attributes:
        timeframe: Timeframe delle candele (default ``15m``).
        atr_length: Periodi dell'ATR.
        donchian_length: Periodi dei canali di Donchian.
        vwap_band_std: Moltiplicatore della deviazione standard per le bande VWAP.
        sl_atr_mult: Distanza dello stop loss in multipli di ATR.
        tp_atr_mult: Distanza del take profit in multipli di ATR.
        ohlcv_limit: Numero di candele scaricate a ogni ciclo. Deve coprire
            il warm-up degli indicatori e un'intera sessione giornaliera
            (96 candele da 15m) per ancorare correttamente il VWAP.
        allow_short: Abilita i segnali short (impossibile sullo spot).
    """

    timeframe: str = "15m"
    atr_length: int = 14
    donchian_length: int = 55
    vwap_band_std: float = 2.0
    sl_atr_mult: float = 2.0
    tp_atr_mult: float = 5.0
    ohlcv_limit: int = 300
    allow_short: bool = True

    @property
    def timeframe_seconds(self) -> int:
        """Durata di una candela in secondi."""
        return TIMEFRAME_SECONDS[self.timeframe]

    @property
    def reward_risk_ratio(self) -> float:
        """Rapporto rendimento/rischio teorico (lordo di commissioni)."""
        return self.tp_atr_mult / self.sl_atr_mult


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """Parametri di gestione del rischio.

    Attributes:
        risk_per_trade: Frazione del capitale rischiata per trade (0.01 = 1%).
        max_notional_multiple: Tetto al controvalore della posizione espresso
            in multipli del saldo. Con uno stop di 2×ATR a 15m il nozionale
            teorico supera spesso il saldo: il tetto evita leve implicite
            eccessive (quando interviene, il rischio effettivo scende sotto l'1%).
    """

    risk_per_trade: float = 0.01
    max_notional_multiple: float = 3.0


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Parametri operativi del processo.

    Attributes:
        dry_run: Se ``True`` gli ordini sono simulati (paper trading) usando
            dati di mercato reali. Default ``True`` per sicurezza.
        paper_balance: Saldo iniziale simulato in valuta di quotazione.
        exchange_stops: Se ``True`` SL/TP sono ordini reduce-only sull'exchange
            (sopravvivono a crash e blackout del Raspberry). Se ``False`` le
            uscite sono gestite via software con polling del prezzo.
        exit_poll_seconds: Intervallo di polling per le uscite software.
        candle_close_delay: Secondi di attesa dopo la chiusura teorica della
            candela prima di scaricarla (tempo di consolidamento lato exchange).
        db_path: Percorso del database SQLite.
        log_path: Percorso del file di log (con rotazione).
        log_level: Livello di logging (``DEBUG``, ``INFO``, ...).
    """

    dry_run: bool = True
    paper_balance: float = 10_000.0
    exchange_stops: bool = True
    exit_poll_seconds: int = 10
    candle_close_delay: float = 5.0
    db_path: Path = BASE_DIR / "data" / "pytrader.sqlite3"
    log_path: Path = BASE_DIR / "logs" / "pytrader.log"
    log_level: str = "INFO"

    @property
    def use_exchange_stops(self) -> bool:
        """``True`` se SL/TP devono essere piazzati come ordini reali."""
        return self.exchange_stops and not self.dry_run


@dataclass(frozen=True, slots=True)
class BotConfig:
    """Configurazione completa del bot, composta dalle sezioni tematiche."""

    exchange: ExchangeConfig
    strategy: StrategyConfig
    risk: RiskConfig
    runtime: RuntimeConfig

    def validate(self) -> None:
        """Verifica la coerenza dei parametri.

        Raises:
            ConfigError: Alla prima incoerenza rilevata.
        """
        s, r, rt, ex = self.strategy, self.risk, self.runtime, self.exchange
        if s.timeframe not in TIMEFRAME_SECONDS:
            raise ConfigError(f"Timeframe non supportato: {s.timeframe}")
        if not 0 < r.risk_per_trade <= 0.05:
            raise ConfigError("RISK_PER_TRADE deve essere in (0, 0.05]")
        if r.max_notional_multiple <= 0:
            raise ConfigError("MAX_NOTIONAL_MULTIPLE deve essere > 0")
        if s.sl_atr_mult <= 0 or s.tp_atr_mult <= 0:
            raise ConfigError("I moltiplicatori ATR devono essere > 0")
        candles_per_day = 86_400 // s.timeframe_seconds
        min_limit = max(s.donchian_length, s.atr_length) + candles_per_day + 2
        if s.ohlcv_limit < min_limit:
            raise ConfigError(
                f"OHLCV_LIMIT={s.ohlcv_limit} insufficiente: servono almeno "
                f"{min_limit} candele per warm-up e ancoraggio VWAP giornaliero"
            )
        if ex.is_spot and s.allow_short:
            raise ConfigError(
                "Short non possibile su mercato spot: usa un perpetual "
                "(es. BTC/USDT:USDT) oppure imposta ALLOW_SHORT=false"
            )
        if ex.is_spot and rt.use_exchange_stops:
            raise ConfigError(
                "Sullo spot SL e TP simultanei bloccherebbero due volte lo "
                "stesso saldo: imposta EXCHANGE_STOPS=false (uscite software)"
            )
        if not ex.is_spot and ex.leverage <= r.max_notional_multiple:
            raise ConfigError(
                "LEVERAGE deve superare MAX_NOTIONAL_MULTIPLE: con leva pari al "
                "tetto il margine richiesto assorbirebbe l'intero saldo"
            )
        if ex.is_spot and r.max_notional_multiple > 1:
            raise ConfigError("Sullo spot MAX_NOTIONAL_MULTIPLE deve essere <= 1")
        if not rt.dry_run and not (ex.api_key and ex.api_secret):
            raise ConfigError("API_KEY e API_SECRET obbligatorie con DRY_RUN=false")
        if rt.exit_poll_seconds < 1:
            raise ConfigError("EXIT_POLL_SECONDS deve essere >= 1")


def load_config(env_file: Path | None = None) -> BotConfig:
    """Costruisce e valida la configurazione a partire dall'ambiente.

    Args:
        env_file: File ``.env`` opzionale; di default ``<progetto>/.env``.
            Le variabili già presenti nell'ambiente hanno la precedenza.

    Returns:
        Un'istanza immutabile e validata di :class:`BotConfig`.

    Raises:
        ConfigError: Se un parametro è mancante o incoerente.
    """
    if load_dotenv is not None:
        load_dotenv(env_file or BASE_DIR / ".env", override=False)

    symbol = _env_str("SYMBOL", "BTC/USDT:USDT")
    exchange = ExchangeConfig(
        exchange_id=_env_str("EXCHANGE_ID", "binanceusdm"),
        symbol=symbol,
        api_key=_env_str("API_KEY", ""),
        api_secret=_env_str("API_SECRET", ""),
        api_password=_env_str("API_PASSWORD", ""),
        sandbox=_env_bool("SANDBOX", False),
        leverage=_env_int("LEVERAGE", 5),
        margin_mode=_env_str("MARGIN_MODE", "isolated"),
    )
    strategy = StrategyConfig(
        timeframe=_env_str("TIMEFRAME", "15m"),
        atr_length=_env_int("ATR_LENGTH", 14),
        donchian_length=_env_int("DONCHIAN_LENGTH", 55),
        vwap_band_std=_env_float("VWAP_BAND_STD", 2.0),
        sl_atr_mult=_env_float("SL_ATR_MULT", 2.0),
        tp_atr_mult=_env_float("TP_ATR_MULT", 5.0),
        ohlcv_limit=_env_int("OHLCV_LIMIT", 300),
        allow_short=_env_bool("ALLOW_SHORT", not exchange.is_spot),
    )
    risk = RiskConfig(
        risk_per_trade=_env_float("RISK_PER_TRADE", 0.01),
        max_notional_multiple=_env_float(
            "MAX_NOTIONAL_MULTIPLE", 1.0 if exchange.is_spot else 3.0
        ),
    )
    runtime = RuntimeConfig(
        dry_run=_env_bool("DRY_RUN", True),
        paper_balance=_env_float("PAPER_BALANCE", 10_000.0),
        exchange_stops=_env_bool("EXCHANGE_STOPS", not exchange.is_spot),
        exit_poll_seconds=_env_int("EXIT_POLL_SECONDS", 10),
        candle_close_delay=_env_float("CANDLE_CLOSE_DELAY", 5.0),
        db_path=Path(_env_str("DB_PATH", str(BASE_DIR / "data" / "pytrader.sqlite3"))),
        log_path=Path(_env_str("LOG_PATH", str(BASE_DIR / "logs" / "pytrader.log"))),
        log_level=_env_str("LOG_LEVEL", "INFO").upper(),
    )
    config = BotConfig(exchange=exchange, strategy=strategy, risk=risk, runtime=runtime)
    config.validate()
    return config
