# Guidelines per Claude Code - Crypto Trading Bot (Raspberry Pi)

## 1. Stack e Contesto Principale
* **Linguaggio Principale:** Python 3.12+ (minimo imposto da `pandas_ta` 0.4.x, l'unica versione disponibile su PyPI). Focus assoluto sull'uso di `asyncio`.
* **Ambiente di Esecuzione:** Headless su Raspberry Pi (OS Linux-based). Nessuna GUI.
* **Librerie Core:** `ccxt.async_support` (connessione exchange), `pandas` & `pandas_ta` (calcoli vettorizzati/indicatori), `sqlite3` (persistenza stato).
* **Controllo Versione:** Git e GitHub.

## 2. Architettura del Sistema (Modulare)
Il progetto deve mantenere una rigorosa separazione delle responsabilità (sorgenti in `src/`):
* **`config.py`**: Gestione credenziali, costanti e chiavi API (caricate tramite `.env`).
* **`db_manager.py`**: Interazione con SQLite (modalità WAL attiva). Memorizzazione stato dei trade per resilienza ai riavvii.
* **`indicators.py`**: Calcolo vettorizzato degli indicatori tramite `pandas_ta` (ATR, VWAP giornaliero con bande σ, Donchian Channels).
* **`execution.py`**: Comunicazione asincrona con l'exchange tramite `ccxt`, gestione ordini e sincronizzazione stato con il DB all'avvio.
* **`strategy.py`**: Regole di ingresso/uscita (segnali vettorizzati, livelli SL/TP) e Position Sizing (rischio 1% per trade, vincoli minimi dell'exchange).
* **`main.py`**: Entry point asincrono, loop principale sincronizzato con la chiusura delle candele a 15 minuti.
* **`backtest.py`**: Backtest che riusa `Strategy` e `PositionSizer` del bot live (mai reimplementare la logica).
* **`tradingview/pytrader_strategy.pine`**: Replica Pine Script v6 per lo Strategy Tester di TradingView; va aggiornata a ogni modifica delle regole in `strategy.py`/`indicators.py`.

## 3. Standard di Sviluppo e Resilienza
* **Esecuzione Asincrona:** Tutte le chiamate di rete (fetching dati, piazzamento ordini) DEVONO usare `async/await` per non bloccare il loop principale.
* **Gestione Errori di Rete:** Il Raspberry Pi può subire disconnessioni temporanee. Le chiamate di **sola lettura** (OHLCV, ticker, saldo, posizioni, stato ordini) usano SEMPRE il decoratore `@with_backoff` (o `retry_async`) di `execution.py`: *exponential backoff* su `ccxt.NetworkError` (che include `RateLimitExceeded`) ed `ccxt.ExchangeError` transitori. Gli errori permanenti (`AuthenticationError`, `InsufficientFunds`, `InvalidOrder`, `BadSymbol`, ...) NON vanno ritentati.
* **Ordini mai ritentati automaticamente:** `create_order` non va avvolto nel backoff: un timeout può arrivare dopo che l'exchange ha accettato l'ordine e il nuovo tentativo aprirebbe una posizione doppia. L'errore risale al loop principale, che lo registra e prosegue; la riconciliazione con l'exchange al ciclo successivo verifica lo stato reale.
* **Il bot non deve mai crashare:** ogni ciclo del loop intercetta le eccezioni; gli errori fatali di avvio terminano il processo e systemd lo riavvia.
* **Rate Limiting:** Mantieni sempre `enableRateLimit = True` nell'inizializzazione di `ccxt`.
* **Persistenza e SD Card:** Usa `PRAGMA journal_mode=WAL;` in SQLite per prevenire corruzioni del DB dovute a cali di tensione e ridurre l'usura della scheda SD. Non salvare log pesanti sul DB, usa i file di testo.

## 4. Sistema di Notifiche e Logging (No GUI)
* **Logging Strutturato:** Usa il modulo `logging` di Python. I log di livello INFO (es. esecuzione ordini, avvio bot) e ERROR vanno scritti in un file (es. `bot.log`) con rotazione (es. `RotatingFileHandler`).
* **Notifiche Remote (Sviluppo Futuro):** In assenza di GUI, eventuali interventi umani o report di esecuzione andranno veicolati tramite Webhook o Telegram Bot API in asincrono.

## 5. Comandi di Sviluppo & Test
* **Esecuzione App:** `python src/main.py`
* **Backtest:** `python src/backtest.py --csv data/btcusdt_15m.csv` (oppure `--fetch --since YYYY-MM-DD`)
* **Test Suite:** `pytest` (creare test mock per `ccxt` e DB in ram `sqlite3:memory:`).
* **Linter / Formatting:** `ruff check . --fix` (in alternativa `black .`).
* **Dipendenze:** `pip freeze > requirements.txt` da un virtualenv con le sole dipendenze runtime (Assicurati di bloccare le versioni per stabilità); strumenti di sviluppo in `requirements-dev.txt`.

## 6. Regole Operative per l'Agente
* **Lingua:** Rispondi e inserisci commenti nel codice sempre in **italiano**.
* **Stile Risposte:** Sii sintetico e diretto. Vai subito al codice e all'architettura. Niente spiegazioni prolisse se non espressamente richieste.
* **Autonomia e Git:** Se modifichi i file core, aggiorna la versione semantica in `__version__` di `src/config.py` e in `version` di `pyproject.toml`, aggiungendo la voce in `CHANGELOG.md`. Procedi ai commit/push in autonomia nel branch operativo.
* **Pulizia Repo:** Non creare file di log fittizi o documentazione temporanea nella root. Usa `.gitignore` per escludere il database SQLite (`*.sqlite`, `*.db`) e file `.env`.
* **Design Pattern:** Privilegia la *Dependency Injection* (es. passare l'istanza del DB al manager di esecuzione) per facilitare i test isolati delle singole classi.