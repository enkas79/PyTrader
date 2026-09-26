# PyTrader v0.2.0

Trading bot asincrono per criptovalute (default **BTC/USDT perpetual**) pensato
per girare 24/7 su Raspberry Pi. Strategia breakout **Donchian(55)** su 15m con
filtro **VWAP giornaliero ± 2σ**, stop/target in multipli di **ATR(14)** e
position sizing a **rischio fisso dell'1%**.

## Architettura

| Modulo (`src/`) | Responsabilità |
|---|---|
| `config.py` | Parametri e credenziali da `.env`/ambiente, validazione, `__version__` (SemVer) |
| `indicators.py` | `IndicatorEngine`: ATR, Donchian, VWAP daily + bande σ (`pandas_ta`) |
| `strategy.py` | `Strategy` (segnali e SL/TP) e `PositionSizer` (`(balance × 1%) / (2 × ATR)` + vincoli exchange) |
| `execution.py` | `ExecutionEngine`: `ccxt.async_support`, ordini, riconciliazione, `@with_backoff` |
| `db_manager.py` | `DatabaseManager`: SQLite (WAL) per trade aperto e stato |
| `main.py` | `TradingBot`: loop sincronizzato a :00/:15/:30/:45 UTC |
| `backtest.py` | `Backtester`: simulazione con commissioni, slippage, gap e vincoli exchange |

Le chiamate di sola lettura verso l'exchange usano exponential backoff
(2s, 4s, 8s) su `NetworkError` (incluso `RateLimitExceeded`) ed `ExchangeError`
transitori; gli errori permanenti (credenziali, fondi, ordine non valido) non
vengono ritentati. La creazione di ordini non è mai ritentata automaticamente:
un timeout dopo l'accettazione dell'ordine produrrebbe un duplicato.

Flusso per candela: risveglio a `chiusura + 5s` → download candele **chiuse**
→ riconciliazione del trade aperto → segnale → size → ordine market → SL/TP
reduce-only sull'exchange → persistenza.

## Regole

| | Long | Short |
|---|---|---|
| Trigger | `close > DCU₅₅.shift(1)` | `close < DCL₅₅.shift(1)` |
| Filtro | `close < VWAP + 2σ` | `close > VWAP − 2σ` |
| Stop loss | `entry − 2·ATR` | `entry + 2·ATR` |
| Take profit | `entry + 5·ATR` | `entry − 5·ATR` |

- SL/TP sono ancorati al **prezzo di esecuzione reale**, con l'ATR della candela
  di segnale: la distanza dello stop resta esattamente 2·ATR, quindi il rischio
  resta quello calcolato dal sizing.
- Un solo trade aperto per simbolo; i segnali con posizione aperta sono ignorati.
- σ delle bande è la deviazione standard **ponderata per volume** del prezzo
  tipico dall'inizio della sessione UTC (formula esatta; `pandas_ta` usa
  un'approssimazione dipendente dal percorso, vedi docstring di `indicators.py`).

## Sizing

```
size = (balance × 0.01) / (2 × ATR)
```

poi: tetto al nozionale (`MAX_NOTIONAL_MULTIPLE × balance`) → conversione in
contratti (`contractSize`) → **troncamento** alla precisione dell'exchange →
verifica `min amount` e `min cost`. Se la size è sotto i minimi il trade viene
**scartato**, mai arrotondato per eccesso (si rischierebbe più dell'1%).

## Installazione su Raspberry Pi

`pandas_ta` 0.4.x (l'unica versione oggi su PyPI) richiede **Python ≥ 3.12**
(non 3.11 come indicato in `claude_md.md`):
Raspberry Pi OS *Trixie* (Python 3.13) va bene; su *Bookworm* (3.11) installa
un interprete recente con [`uv`](https://docs.astral.sh/uv/). Serve un sistema
a **64 bit** (wheel `numba`/`llvmlite` per aarch64).

```bash
git clone https://github.com/enkas79/PyTrader.git && cd PyTrader
python3 -m venv .venv && . .venv/bin/activate      # oppure: uv venv -p 3.12
pip install -r requirements.txt                    # versioni bloccate
cp .env.example .env && nano .env                  # DRY_RUN=true per iniziare
python src/main.py
```

Servizio permanente con riavvio automatico:

```bash
sudo cp deploy/pytrader.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now pytrader
```

Il Raspberry non ha RTC: verifica che NTP sia attivo (`timedatectl`). All'avvio
il bot logga lo scarto rispetto all'orologio dell'exchange.

Log: `logs/pytrader.log` (rotazione 5 × 5 MB). Stato: `data/pytrader.sqlite3`.

## Modalità operative

| Modalità | Config | Uscite |
|---|---|---|
| Paper (default) | `DRY_RUN=true` | software, polling ogni `EXIT_POLL_SECONDS` |
| Live derivati | `DRY_RUN=false`, `SYMBOL=BTC/USDT:USDT` | ordini SL/TP reduce-only sull'exchange |
| Live spot | `SYMBOL=BTC/USDT`, `ALLOW_SHORT=false`, `EXCHANGE_STOPS=false`, `MAX_NOTIONAL_MULTIPLE≤1` | software |

Gli short richiedono un derivato: sullo spot non sono possibili. Sullo spot SL e
TP simultanei bloccherebbero due volte lo stesso saldo, per questo le uscite
sono gestite via software (che però **non** protegge durante un blackout).

### Recupero dopo riavvio

- Trade aperto nel DB + posizione ancora aperta: verifica che SL/TP esistano,
  altrimenti li ripiazza.
- Trade aperto nel DB ma posizione chiusa sull'exchange (SL/TP scattati mentre
  il Pi era spento): ricostruisce il prezzo medio d'uscita da `fetch_my_trades`,
  cancella l'ordine di protezione residuo, chiude il trade nel DB.
- Posizione sull'exchange non presente nel DB: log `CRITICAL`, nessun nuovo
  ingresso finché non viene chiusa manualmente.
- Se SL/TP non possono essere piazzati dopo l'ingresso, la posizione viene
  chiusa immediatamente (`protection_failed`).

## Backtest

Riusa `Strategy` e `PositionSizer` del bot live, quindi testa il codice che
andrà in produzione.

```bash
# scarica lo storico (API pubbliche, nessuna chiave) e lo salva in CSV
python src/backtest.py --fetch --since 2023-01-01 --save data/btcusdt_15m.csv
# rilancia sul CSV variando i costi
python src/backtest.py --csv data/btcusdt_15m.csv --fee 0.0005 --slippage-bps 2 \
    --trades-out data/trades.csv
```

Modello di esecuzione (conservativo):

- ingresso all'**apertura della candela successiva** al segnale, con slippage;
- SL e TP toccati nella stessa candela → si assume **prima lo SL**;
- gap in apertura oltre lo SL → uscita all'apertura (perdita > 1R);
- commissione taker su ingresso e uscita; tetto al nozionale e minimi exchange
  come nel bot live; posizione residua chiusa a fine storico.

Non modellati: funding dei perpetual, liquidità del book, latenza reale.

Output: trade, win rate osservato vs **win rate di pareggio**, expectancy in R,
profit factor, max drawdown, Sharpe, peso delle commissioni sul PnL lordo,
confronto con il buy & hold e scomposizione **per anno** (stabilità tra
regimi di mercato).

Come leggerlo senza ingannarsi:

- ogni parametro modificato dopo aver visto i risultati è overfitting: tieni
  un periodo *out-of-sample* che guardi una sola volta (`--until` / `--since`);
- l'expectancy va valutata insieme al numero di trade: con meno di ~100
  trade l'errore statistico è dello stesso ordine del risultato;
- un anno molto positivo e gli altri negativi indicano dipendenza dal regime,
  non un vantaggio strutturale.

Controllo di coerenza: su un random walk sintetico il backtest restituisce
expectancy ≈ −0,1R per trade, cioè circa il costo di commissioni e slippage,
come atteso in assenza di vantaggio.

## Test

```bash
pip install -r requirements-dev.txt
pytest
```

I test verificano gli indicatori contro implementazioni di riferimento (VWAP e σ
calcolati a forza bruta), le regole di ingresso, il sizing, la persistenza, il
backoff, il ciclo completo in paper trading con un exchange simulato (nessuna
rete) e il backtest (costi esatti, gap, SL/TP nella stessa candela, assenza di
look-ahead).

## Avvertenze (leggere prima di andare live)

- **La strategia non è validata.** Esegui il backtest su più anni e un periodo
  di paper trading prima di andare live.
- **Commissioni.** Con uno stop di 2·ATR su 15m (tipicamente 0,3–0,6% del
  prezzo su BTC) il nozionale è 1,7–3,3× il saldo. Con taker 0,05% per lato il
  costo round-trip vale circa il 17–33% di R: l'R:R netto scende da 2,5 a
  ~1,9–2,1 e il win rate di pareggio sale da 28,6% a ~33–38%.
- **Il rischio non è esattamente 1%**: lo slippage sugli stop market (gap,
  cascate di liquidazioni) può superarlo; il tetto al nozionale e il
  troncamento lo riducono.
- Un'API key per il bot deve avere solo permessi di trading, **mai di prelievo**,
  con whitelist IP se l'exchange lo consente.
- Il simbolo va usato in modo esclusivo dal bot: ordini o posizioni manuali
  sullo stesso simbolo interferiscono con la riconciliazione.

## Licenza

MIT — vedi `LICENSE`.
