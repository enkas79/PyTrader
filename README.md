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
- **Walk-forward**: ottimizzazione su griglia di parametri scelta in-sample e verificata
  out-of-sample su finestre consecutive (mobili o ancorate), obiettivo SQN/expectancy/totale R,
  efficienza walk-forward e stabilità delle scelte per riconoscere l'overfitting.
- **Screener multi-simbolo**: classifica un elenco di simboli (Yahoo o ccxt) con un punteggio
  0-100 a percentili su volume relativo e momentum (pesi regolabili, anche negativi), prezzi
  Yahoo rettificati per split e dividendi. La verifica storica confronta il punteggio con i
  rendimenti successivi (IC di Spearman, t-stat, rendimento per quantile) e dichiara se il
  vantaggio non è dimostrato. I simboli scelti si aggiungono alla watchlist live.
- **Dimensionamento**: rischio % per trade (leva come tetto), importo fisso o % del capitale
  (leva come moltiplicatore); indicazione del rischio effettivo per trade e dei trade in cui la
  perdita allo stop supererebbe il margine.
- **Valori per famiglia di asset**: crypto, azioni, ETF/indici, forex e materie prime.
  Commissione (metà spread inclusa) e leva (limiti ESMA retail) per famiglia; R:R minimo
  calcolato dal costo di un trade in R sulla serie caricata (netto ≥ 2:1); periodi dello
  screener convertiti da mesi a candele secondo calendario e timeframe. Anteprima motivata
  prima di applicare. Nessuna ottimizzazione sui rendimenti passati.
- **Segnali live**: watchlist multi-mercato controllata a ogni chiusura di candela (o a un
  intervallo scelto per mercato, entro limiti plausibili per sorgente e timeframe), notifiche
  desktop anche con la finestra ridotta nell'area di notifica, storico persistente dei segnali
  con quantità suggerita. Nessun ordine viene inviato all'exchange.
- **GUI**: grafico interattivo pyqtgraph con fasce S/R e livelli dei setup, tabella dei
  setup, export JSON, tema scuro/chiaro/di sistema, verifica aggiornamenti da GitHub Releases.
- **Guida integrata**: 20 sezioni con indice, ricerca e glossario; esportabile in PDF (A4)
  dalla stessa fonte, quindi sempre allineata alla versione installata.
- **Layout**: barra degli strumenti; barra laterale con sorgente, riepilogo impostazioni e
  metriche del backtest, senza scorrimento da 1080p (anche con zoom 125%); parametri e capitale
  in un pannello agganciabile o staccabile; colonne delle tabelle selezionabili; disposizione
  ricordata tra un avvio e l'altro (`~/.pytrader/ui.ini`).

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
    ├── optimize/           # walk-forward: griglia, finestre IS/OOS, selezione
    ├── screener/           # feature OHLCV, punteggio cross-sezionale, verifica storica
    ├── presets.py          # valori consigliati per famiglia di asset
    ├── services.py         # casi d'uso senza Qt (caricamento, analisi, export)
    ├── live/               # watchlist, pianificazione controlli, scanner, storico segnali
    ├── settings.py         # preferenze (tema) in ~/.pytrader/settings.json
    ├── updater/            # GitHub Releases: confronto versioni, download, installer
    └── gui/                # MainWindow, grafico, temi, worker QThreadPool, dialoghi
tests/                      # pytest
installer/pytrader.nsi      # installer Windows (NSIS)
styles.qss                  # foglio di stile unico con segnaposto dei colori (gui/theme.py)
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
