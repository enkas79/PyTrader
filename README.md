# PyTrader

Applicazione desktop (PyQt6) per l'analisi tecnica di serie OHLCV: individua supporti e
resistenze, riconosce pattern candlestick e genera setup operativi con entry, stop loss e
take profit, verificandoli con un backtest a barre.

## Funzionalità

- **Dati**: CSV locale, exchange crypto via `ccxt`, azioni/indici/forex via `yfinance`.
  Validazione della serie (duplicati, candele incoerenti); i buchi temporali vengono
  segnalati, mai riempiti con candele fittizie.
- **Ricerca ticker**: digitando il nome di un'azienda, fondo o ETF compare una lista di
  suggerimenti (Yahoo Finance, con tolleranza agli errori di battitura) o delle coppie
  dell'exchange (ccxt); la selezione inserisce il ticker corretto.
- **Analisi**: ATR di Wilder, volume medio, swing pivot con conferma ritardata, livelli S/R
  per clustering dei pivot (tolleranza in multipli di ATR, tocchi minimi).
- **Pattern**: hammer / shooting star (pin bar), bullish/bearish engulfing,
  morning/evening star, doji. Calcolo vettorizzato con `pandas`.
- **Segnali**: pattern rialzista su supporto o ribassista su resistenza (entro *k*·ATR).
  Entry all'apertura della candela successiva, SL oltre la zona + buffer ATR, TP sul livello
  strutturale successivo (setup scartato se R:R < minimo) o a R:R fisso.
- **Backtest**: una posizione alla volta, SL prioritario se SL e TP cadono nella stessa
  candela, commissioni opzionali; metriche in R (win rate, expectancy, profit factor, drawdown).
- **Capitale e rischio**: position sizing a rischio fisso (% del capitale per trade), limite
  di leva, reinvestimento opzionale dei profitti; P&L per trade, capitale finale, rendimento e
  drawdown in valuta.
- **Segnali live**: watchlist multi-mercato controllata a ogni chiusura di candela, notifiche
  desktop anche con la finestra ridotta nell'area di notifica, storico persistente dei segnali
  con quantità suggerita. Nessun ordine viene inviato all'exchange.
- **GUI**: grafico interattivo pyqtgraph con fasce S/R e livelli dei setup, tabella dei
  setup, export JSON, guida integrata, verifica aggiornamenti da GitHub Releases.

### Nessun look-ahead

Ogni valore alla candela *t* usa solo candele chiuse ≤ *t*. Per le sorgenti remote la candela
ancora in formazione restituita da exchange e Yahoo viene esclusa. Un pivot con finestra *n* è
utilizzabile solo da *t + n*. I test (`tests/test_signals.py`, `tests/test_analysis.py`)
verificano che troncare la serie non modifichi pivot, pattern e setup già generati.

## Struttura

```
src/
├── main.py                 # entry point: python src/main.py
└── pytrader/
    ├── models/             # dataclass immutabili: Level, PatternHit, TradeSetup, ...
    ├── data/               # DataSource (CSV, ccxt, yfinance) + validate_ohlcv
    ├── analysis/           # indicatori, pivot, livelli, pattern
    ├── signals/            # SignalEngine: confluenza e gestione del rischio
    ├── backtest/           # Backtester e metriche
    ├── services.py         # casi d'uso senza Qt (caricamento, analisi, export)
    ├── live/               # watchlist, scanner a chiusura candela, storico segnali
    ├── updater/            # GitHub Releases: confronto versioni, download, installer
    └── gui/                # MainWindow, grafico, worker QThreadPool, dialoghi
tests/                      # pytest
installer/pytrader.nsi      # installer Windows (NSIS)
styles.qss                  # tema Qt centralizzato
version.txt                 # versione corrente (fonte unica)
```

## Sviluppo

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest ruff
python src/main.py          # avvio
pytest                      # test (headless: QT_QPA_PLATFORM=offscreen)
ruff check . --fix
```

`requirements.txt` blocca le versioni usate per le build (Python 3.11+).
`pyproject.toml` dichiara vincoli minimi più ampi.

## Formato CSV

Colonne richieste (nomi case-insensitive, alias comuni accettati):
`timestamp` (ISO 8601 o epoch s/ms), `open`, `high`, `low`, `close`, `volume` (facoltativo).

## Release

Ogni modifica a `version.txt` su `main` avvia `.github/workflows/build-installers.yml`:
test, build PyInstaller per Windows (installer NSIS), Linux e macOS, e pubblicazione della
release `vX.Y.Z`. L'app verifica le nuove release all'avvio.

## Avvertenza

Strumento di analisi, non consulenza finanziaria. L'efficacia dei pattern candlestick è
statisticamente debole se non filtrata dal contesto: valuta sempre l'expectancy su un
campione ampio prima di operare.
