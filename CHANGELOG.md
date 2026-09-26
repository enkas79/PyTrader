# Changelog

Formato basato su [Keep a Changelog](https://keepachangelog.com/it/1.1.0/),
versionamento [SemVer](https://semver.org/lang/it/).

## [0.1.0] - 2026-09-26

### Aggiunto
- Strategia breakout Donchian(55) su 15m con filtro VWAP giornaliero ± 2σ
  (ancorato 00:00 UTC), SL 2×ATR(14), TP 5×ATR (R:R 1:2.5), long e short.
- Position sizing a rischio fisso (1%) con tetto al nozionale, troncamento alla
  precisione dell'exchange e verifica di quantità/costo minimi.
- Esecuzione asincrona `ccxt` con SL/TP reduce-only lato exchange; uscite
  software per paper trading e spot.
- Persistenza SQLite (WAL) del trade aperto e dell'ultima candela processata;
  riconciliazione con l'exchange al riavvio.
- Loop sincronizzato con la chiusura delle candele, controllo dello scarto
  d'orologio, logging su file con rotazione, unit systemd.
