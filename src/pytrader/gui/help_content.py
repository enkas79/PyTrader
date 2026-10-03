"""Contenuto della guida: sezioni HTML usate dal dialogo Guida e dall'esportazione PDF.

L'HTML usa solo il sottoinsieme supportato da ``QTextDocument`` (titoli, paragrafi, elenchi,
tabelle semplici). Ogni sezione ha un'ancora per l'indice.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HelpSection:
    anchor: str
    title: str
    body: str


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    head = "".join(f"<th align='left'>{h}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return (
        "<table border='1' cellspacing='0' cellpadding='4' width='100%'>"
        f"<tr>{head}</tr>{body}</table>"
    )


SECTIONS: tuple[HelpSection, ...] = (
    HelpSection(
        "intro",
        "Introduzione",
        """
<p>PyTrader è un'applicazione di <b>analisi tecnica su dati OHLCV</b> (apertura, massimo,
minimo, chiusura, volume). Individua supporti e resistenze, riconosce pattern candlestick in
corrispondenza di questi livelli e propone <b>setup</b> con entry, stop loss e take profit. Poi
li verifica con un backtest e calcola le quantità in base al capitale e al rischio scelti.</p>
<p><b>Cosa fa</b>: analisi, backtest, ottimizzazione walk-forward, screener multi-simbolo,
monitoraggio live con notifiche.<br>
<b>Cosa non fa</b>: non invia ordini a broker o exchange, non gestisce conti, non dà consigli
finanziari. Ogni decisione operativa resta tua.</p>
<h3>Flusso di lavoro tipico</h3>
<ol>
<li>Scegli sorgente, simbolo e timeframe, poi premi <b>Carica dati</b> (Ctrl+L).</li>
<li>Opzionale: <b>Valori per famiglia di asset</b> per impostare commissione, leva e R:R
minimo adatti al mercato.</li>
<li>Premi <b>Analizza</b> (F5): compaiono livelli, setup e metriche del backtest.</li>
<li>Valuta i risultati con il <b>Walk-forward</b>, che stima quanto reggono fuori campione.</li>
<li>Aggiungi il mercato alla watchlist e avvia il <b>monitoraggio live</b>.</li>
<li>Per scegliere quali mercati seguire usa lo <b>Screener</b>.</li>
</ol>
""",
    ),
    HelpSection(
        "layout",
        "La finestra principale",
        """
<ul>
<li><b>Barra dei menu</b>: File (apri CSV, esporta JSON, esci), Visualizza (barra degli
strumenti, pannello parametri, ripristino disposizione, tema), Strumenti (carica, analizza,
walk-forward, screener, valori per famiglia), Aiuto (guida, aggiornamenti, informazioni).</li>
<li><b>Barra degli strumenti</b>: Carica dati, Analizza, Parametri, Walk-forward, Screener.
Quando cambi un parametro dopo un'analisi, <b>Analizza</b> si evidenzia: i risultati mostrati
non corrispondono più ai valori impostati.</li>
<li><b>Barra laterale</b> (sinistra): riquadro <i>Sorgente dati</i>, riquadro
<i>Impostazioni</i> con il riepilogo di capitale e parametri (un clic apre il pannello per
modificarli) e riquadro <i>Backtest</i> con le dieci metriche principali.</li>
<li><b>Grafico</b> (in alto a destra): candele, volumi, fasce di supporto e resistenza,
setup.</li>
<li><b>Schede</b> (in basso a destra): <i>Setup</i> (tabella dei segnali con esito),
<i>Dati</i> (report di validazione della serie), <i>Live</i> (watchlist e segnali
rilevati).</li>
<li><b>Pannello Parametri e rischio</b> (Ctrl+P): parametri di analisi, commissione,
capitale. Si aggancia a destra o a sinistra o si stacca come finestra separata, anche su un
secondo monitor.</li>
<li><b>Barra di stato</b> (in fondo): messaggi sull'ultima operazione.</li>
<li><b>Area di notifica</b>: con il monitoraggio attivo, chiudere la finestra la riduce
all'icona vicino all'orologio; il monitoraggio continua.</li>
</ul>
<p>I divisori tra le aree si trascinano. Posizioni, dimensioni e colonne visibili vengono
ricordate al riavvio.</p>
""",
    ),
    HelpSection(
        "data",
        "Caricare i dati",
        f"""
<h3>Sorgenti</h3>
{
            _table(
                ("Sorgente", "Uso", "Note"),
                [
                    (
                        "CSV locale",
                        "qualsiasi serie salvata su file",
                        "il timeframe è dedotto dai dati; il file è caricato per intero",
                    ),
                    (
                        "Exchange crypto (ccxt)",
                        "coppie degli exchange crypto (es. BTC/USDT)",
                        "nel campo Exchange va l'identificativo ccxt: binance, kraken, "
                        "coinbase, bybit, okx…",
                    ),
                    (
                        "Yahoo Finance",
                        "azioni, ETF, indici, forex, futures, crypto in valuta",
                        "nessuna chiave richiesta; dati gratuiti con limiti di storico",
                    ),
                ],
            )
        }
<h3>Simboli Yahoo</h3>
<ul>
<li>Azioni USA: ticker semplice (AAPL, MSFT). Classi di azioni con trattino (BRK-B).</li>
<li>Borse europee: suffisso della piazza: <i>.MI</i> Milano (ENI.MI), <i>.DE</i> Xetra
(SAP.DE), <i>.PA</i> Parigi, <i>.AS</i> Amsterdam, <i>.L</i> Londra, <i>.SW</i> Svizzera.</li>
<li>Indici: prefisso ^ (^GSPC S&amp;P 500, ^NDX Nasdaq 100, ^STOXX50E). FTSE MIB:
FTSEMIB.MI.</li>
<li>Forex: coppia seguita da =X (EURUSD=X, USDJPY=X).</li>
<li>Futures/materie prime: suffisso =F (GC=F oro, CL=F petrolio WTI, SI=F argento).</li>
<li>Crypto in valuta: BTC-USD, ETH-EUR.</li>
</ul>
<p><b>Ricerca</b>: se non ricordi il ticker scrivi il nome (es. <i>Vanguard world</i>): dopo un
istante compare una lista di suggerimenti con ticker, nome, borsa e tipo. Scegliendone uno il
campo viene compilato. Con ccxt la ricerca filtra le coppie dell'exchange (es. <i>BTC</i>).</p>
<h3>Timeframe e candele</h3>
<p>Timeframe disponibili: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w. Il campo <b>Candele</b> indica
quante candele scaricare (100-50.000). Più storia significa backtest e statistiche più
affidabili, ma download più lenti.</p>
<p><b>Limiti di Yahoo</b>: 1m solo ultimi 7 giorni; 5m, 15m e 30m ultimi 60 giorni; 1h ultimi
730 giorni; 4h non disponibile; 1d e 1w tutta la storia. Il volume di forex e di alcuni indici è
nullo. Le serie di analisi usano prezzi <i>non</i> rettificati: uno split appare come un salto
di prezzo; lo screener invece usa prezzi rettificati.</p>
<h3>Formato CSV</h3>
<p>Colonne obbligatorie: <i>timestamp, open, high, low, close</i>; facoltativa
<i>volume</i>. I nomi non distinguono maiuscole e accettano alternative: time, date, datetime,
open_time, ts per il tempo; o, h, l, c; adj close; vol o v. Il tempo può essere una data ISO o un
epoch in secondi o millisecondi; tutto è convertito in UTC.</p>
<h3>Validazione (scheda Dati)</h3>
<ul>
<li>Le righe sono ordinate per tempo; i <b>duplicati</b> sono rimossi tenendo l'ultima
occorrenza.</li>
<li>Rimosse le righe con prezzi mancanti e le candele <b>incoerenti</b> (massimo sotto apertura
o chiusura, minimo sopra, prezzi non positivi, volume negativo).</li>
<li>I <b>buchi</b> (distanza oltre 1,5 volte il timeframe) sono elencati ma <u>non</u> riempiti:
weekend e chiusure di borsa sono normali per azioni ed ETF.</li>
<li>Per le sorgenti remote la <b>candela ancora in formazione</b> viene esclusa: ogni analisi
usa solo candele chiuse.</li>
</ul>
""",
    ),
    HelpSection(
        "chart",
        "Il grafico",
        """
<ul>
<li><b>Candele</b> verdi (rialzo) e rosse (ribasso); sotto, il <b>volume</b>.</li>
<li><b>Fasce orizzontali</b>: supporti e resistenze validi all'ultima candela. L'altezza della
fascia è la dispersione dei pivot che la compongono.</li>
<li><b>Marcatori</b>: un triangolo per ogni setup, in corrispondenza della candela del
segnale e del livello interessato (verso l'alto e verde per i long sul supporto, verso il basso e
rosso per gli short sulla resistenza).</li>
<li><b>Linee del setup</b>: entry, stop loss e take profit dei setup, disegnate per alcune
candele dopo il segnale.</li>
<li><b>Trade selezionato</b>: selezionando una riga della tabella Setup il grafico mostra il
trade dal segnale fino alla chiusura. Le linee di entry, stop e target arrivano alla candela di
uscita e una <b>✕ gialla</b> segna il punto di chiusura: se cade sulla linea del target il
trade è vinto, se cade sullo stop è perso. Per un trade
ancora in corso le linee arrivano all'ultima candela.</li>
<li><b>Mouse</b>: rotella per lo zoom, trascinamento per spostarsi, tasto destro per il menu
del grafico (es. <i>View All</i> per vedere tutta la serie). L'asse orizzontale mostra
data e ora delle candele.</li>
</ul>
""",
    ),
    HelpSection(
        "signals",
        "Come nascono i setup",
        """
<p>Tutte le regole sono valutate alla <b>chiusura</b> della candela del segnale e usano solo
candele già chiuse (nessun look-ahead).</p>
<ol>
<li><b>ATR</b> (Average True Range di Wilder): misura la volatilità media. Tutte le distanze
(tolleranza, prossimità, buffer) sono multipli di ATR, quindi si adattano da sole a mercati e
timeframe diversi.</li>
<li><b>Pivot</b>: un massimo (minimo) è un pivot se è il più alto (basso) delle <i>N</i> candele
a sinistra e a destra (finestra pivot). Può essere usato solo dopo <i>N</i> candele, quando è
confermato.</li>
<li><b>Livelli</b>: i pivot delle ultime candele (storico livelli) vengono raggruppati: pivot
entro <i>tolleranza × ATR</i> formano una fascia. Una fascia diventa supporto/resistenza se ha
almeno i <i>tocchi minimi</i>.</li>
<li><b>Pattern candlestick</b> riconosciuti:
{patterns}</li>
<li><b>Confluenza</b>: un pattern rialzista vale solo se il suo minimo è entro <i>prossimità ×
ATR</i> da un supporto sotto la chiusura; un pattern ribassista se il suo massimo è vicino a
una resistenza sopra la chiusura. Il doji, essendo neutro, non genera setup.</li>
<li><b>Entry</b>: apertura della candela successiva al pattern. Sull'ultima candela, quando la
successiva non esiste ancora, l'entry è stimata con la chiusura.</li>
<li><b>Stop loss</b>: oltre la fascia e oltre l'estremo del pattern, di <i>buffer × ATR</i>.</li>
<li><b>Take profit</b>: in modalità <i>Livello strutturale</i> sul livello successivo nella
direzione del trade; se non esiste (prezzo su nuovi massimi o minimi) a <i>R:R minimo ×
rischio</i>. In modalità <i>R:R fisso</i> sempre a R:R minimo × rischio.</li>
<li><b>Filtro R:R</b>: se il rapporto tra distanza del target e distanza dello stop è sotto
l'R:R minimo il setup è scartato.</li>
<li>Su una candela nasce al massimo un setup: con più pattern vince quello formato da più
candele (stella &gt; engulfing &gt; pin bar).</li>
</ol>
""".replace(
            "{patterns}",
            _table(
                ("Pattern", "Direzione", "Definizione"),
                [
                    (
                        "Hammer (pin bar)",
                        "rialzista",
                        "ombra inferiore ≥ 60 % del range, ombra superiore ≤ 15 %",
                    ),
                    (
                        "Shooting star",
                        "ribassista",
                        "ombra superiore ≥ 60 % del range, ombra inferiore ≤ 15 %",
                    ),
                    (
                        "Engulfing rialzista",
                        "rialzista",
                        "candela rossa seguita da una verde il cui corpo la ingloba",
                    ),
                    (
                        "Engulfing ribassista",
                        "ribassista",
                        "candela verde seguita da una rossa il cui corpo la ingloba",
                    ),
                    (
                        "Morning star",
                        "rialzista",
                        "rossa ampia (corpo ≥ 50 % del range), piccola candela (corpo ≤ 30 % "
                        "del primo), verde che chiude oltre metà del primo corpo",
                    ),
                    ("Evening star", "ribassista", "speculare della morning star"),
                    ("Doji", "neutro", "corpo ≤ 10 % del range: indecisione, nessun setup"),
                ],
            ),
        ),
    ),
    HelpSection(
        "params",
        "Parametri di analisi",
        f"""
<p>Si modificano nel pannello <b>Parametri e rischio</b> (Ctrl+P) e valgono dalla prossima
analisi (F5). <b>Predefiniti</b> ripristina i valori iniziali (commissione esclusa).</p>
{
            _table(
                ("Parametro", "Predefinito", "Significato", "Effetto se aumenti"),
                [
                    (
                        "Periodo ATR",
                        "14",
                        "candele della media della volatilità",
                        "ATR più stabile e lento a reagire",
                    ),
                    (
                        "Finestra pivot",
                        "5",
                        "candele per lato che confermano un massimo/minimo",
                        "meno pivot, più significativi, ma confermati più tardi",
                    ),
                    (
                        "Tolleranza (×ATR)",
                        "0,5",
                        "ampiezza massima di una fascia di livello",
                        "fasce più larghe, meno livelli distinti",
                    ),
                    (
                        "Tocchi minimi",
                        "2",
                        "pivot necessari per validare un livello",
                        "livelli più robusti ma meno numerosi",
                    ),
                    (
                        "Storico livelli",
                        "300",
                        "candele passate in cui cercare i pivot",
                        "considera anche livelli più vecchi",
                    ),
                    (
                        "Prossimità (×ATR)",
                        "0,5",
                        "distanza massima tra pattern e livello",
                        "più setup, confluenza meno stretta",
                    ),
                    (
                        "Buffer SL (×ATR)",
                        "1,5",
                        "margine dello stop oltre la fascia",
                        "stop più lontano: meno uscite per rumore, R:R più basso",
                    ),
                    (
                        "R:R minimo",
                        "2",
                        "rapporto minimo target/rischio per accettare un setup",
                        "meno setup, ciascuno con potenziale maggiore",
                    ),
                    (
                        "Target",
                        "Livello strutturale",
                        "dove porre il take profit",
                        "—",
                    ),
                    (
                        "Commissione/lato",
                        "0 %",
                        "costo per entrata e per uscita, in % del controvalore",
                        "R netto di ogni trade più basso",
                    ),
                ],
            )
        }
<p><b>Attenzione</b>: modificare i parametri finché il backtest migliora porta a
<i>overfitting</i>. Usa il walk-forward per verificare la scelta.</p>
""",
    ),
    HelpSection(
        "family",
        "Valori per famiglia di asset",
        f"""
<p>Pulsante <b>Valori per famiglia di asset…</b> nel pannello Parametri, voce omonima nel menu
Strumenti e pulsante nello Screener. Si apre un'anteprima con ogni valore proposto e il motivo;
nulla cambia finché non premi <b>Applica</b>.</p>
<p>La famiglia è proposta dal simbolo: ccxt o ticker come BTC-USD → Crypto; suffisso =X →
Forex; =F → Materie prime; prefisso ^ → ETF e indici; altrimenti Azioni. Gli ETF non si
distinguono dalle azioni dal ticker: sceglili a mano.</p>
<p>I valori <b>non</b> sono ottimizzati sui rendimenti passati: derivano da costi, calendario
di contrattazione e limiti di leva.</p>
{
            _table(
                ("Famiglia", "Commissione/lato", "Leva", "Calendario", "Volume"),
                [
                    ("Crypto", "0,10 %", "1× (2× derivati UE)", "24 h, 7 giorni", "sì"),
                    ("Azioni", "0,10 %", "1× (5× CFD)", "8 h, 5 giorni", "sì"),
                    ("ETF e indici", "0,05 %", "1× (20× CFD indici)", "8 h, 5 giorni", "sì"),
                    ("Forex", "0,01 %", "30×", "24 h, 5 giorni", "no"),
                    ("Materie prime", "0,03 %", "10× (oro 20×)", "23 h, 5 giorni", "sì"),
                ],
            )
        }
<ul>
<li><b>Commissione</b>: commissione tipica più metà spread, perché lo slippage non è simulato.
Se conosci i costi del tuo broker, usa quelli.</li>
<li><b>Leva</b>: limiti ESMA per i clienti al dettaglio sui CFD. Per azioni, ETF e crypto il
valore proposto è 1×, cioè acquisto a pronti senza leva.</li>
<li><b>Parametri tecnici</b>: tornano ai predefiniti, perché sono già in multipli di ATR.</li>
<li><b>R:R minimo</b> (solo con una serie caricata): si stima l'ATR tipico in % del prezzo
(mediana delle ultime 500 candele) e il costo di un trade in R:
<i>c = 2 × commissione / ((buffer + 0,5) × ATR%)</i>. Per mantenere un rapporto netto di
almeno 2:1 serve <i>R:R ≥ 2 + 3c</i>, arrotondato al quarto superiore (massimo 6). Su
timeframe brevi i costi pesano di più e l'R:R richiesto sale: se arriva a 6 conviene un
timeframe più lungo.</li>
<li><b>Screener</b>: 1 mese, 6 mesi e 1 settimana vengono convertiti in candele secondo calendario
e timeframe (es. azioni 1d: 21, 126, 5; crypto 1d: 30, 182, 7). Per il forex il peso del
volume è 0. Con timeframe intraday la storia necessaria supererebbe 5.000 candele, quindi i
periodi sono ridotti in proporzione: per lo screener preferisci 1d.</li>
</ul>
""",
    ),
    HelpSection(
        "backtest",
        "Backtest e metriche",
        f"""
<h3>Regole della simulazione</h3>
<ul>
<li>Ingresso all'apertura della candela successiva al segnale.</li>
<li><b>Una posizione alla volta</b>: i setup che nascono mentre un trade è aperto sono
<i>Saltati</i>.</li>
<li>Se nella stessa candela vengono toccati sia lo stop sia il target si assume lo
<b>stop</b> (ipotesi prudente).</li>
<li>Se una candela apre già oltre lo stop o il target (gap), l'uscita avviene all'apertura.</li>
<li>Le commissioni sono applicate in entrata e in uscita e incluse nel risultato in R.</li>
<li>Lo <b>slippage non è simulato</b>: i risultati reali saranno peggiori.</li>
</ul>
<h3>Esiti</h3>
<p><b>Vinto</b> (R netto &gt; 0), <b>Perso</b>, <b>In corso</b> (né stop né target ancora
raggiunti), <b>In attesa</b> (segnale sull'ultima candela, ingresso non ancora avvenuto),
<b>Saltato</b>: il setup è nato mentre il trade precedente era ancora aperto. Il backtest
tiene una sola posizione alla volta, quindi non lo esegue e non ne calcola quantità né
risultato; nel trading reale lo avresti ignorato o avresti dovuto aprire una seconda
posizione.</p>
<h3>Metriche (riquadro Backtest)</h3>
{
            _table(
                ("Metrica", "Significato"),
                [
                    ("Trade chiusi", "trade vinti più persi; aperti e saltati esclusi"),
                    ("Win rate", "quota di trade vinti"),
                    (
                        "Expectancy (R)",
                        "R medio per trade: guadagno atteso per ogni unità di rischio. Sopra 0 "
                        "la strategia ha avuto un vantaggio, al netto delle commissioni",
                    ),
                    ("Totale (R)", "somma degli R di tutti i trade"),
                    (
                        "Profit factor",
                        "somma degli R vinti / somma degli R persi; sopra 1 = profitto",
                    ),
                    (
                        "Max drawdown (R)",
                        "massima discesa dal picco della curva cumulata in R",
                    ),
                    ("Capitale finale", "capitale dopo tutti i trade con il sizing scelto"),
                    ("Profitto netto", "capitale finale meno capitale iniziale"),
                    ("Rendimento", "profitto netto in % del capitale iniziale"),
                    ("Max drawdown", "massima discesa dal picco della curva del capitale"),
                ],
            )
        }
<p><b>R</b> è il risultato di un trade in multipli del rischio iniziale: +2 R significa avere
guadagnato il doppio di quanto si sarebbe perso allo stop.</p>
<p>Con meno di 30 trade chiusi le metriche dipendono molto dal caso.</p>
""",
    ),
    HelpSection(
        "table",
        "Tabella Setup",
        f"""
<p>Una riga per ogni setup trovato nella serie. Clic destro sull'intestazione per scegliere le
colonne visibili (Livello e Target sono nascoste in partenza perché leggibili sul grafico).
Selezionando una riga il grafico si centra sul setup.</p>
{
            _table(
                ("Colonna", "Contenuto"),
                [
                    ("Segnale", "data e ora della candela del pattern"),
                    ("Direzione", "Long (acquisto) o Short (vendita)"),
                    ("Pattern", "pattern candlestick che ha generato il setup"),
                    ("Livello", "prezzo del supporto/resistenza e numero di tocchi"),
                    ("Entry", "prezzo d'ingresso (apertura della candela successiva)"),
                    ("Stop Loss / Take Profit", "livelli di uscita in perdita e in profitto"),
                    ("R:R", "rapporto tra distanza del target e distanza dello stop"),
                    ("Target", "strutturale (livello successivo) o R:R fisso"),
                    ("Esito", "Vinto, Perso, In corso, In attesa, Saltato"),
                    (
                        "Uscita",
                        "data e prezzo di chiusura del trade; «in corso» se è ancora aperto",
                    ),
                    ("R", "risultato netto in multipli del rischio"),
                    (
                        "Quantità",
                        "unità da acquistare/vendere con capitale e rischio impostati; ⚠ = "
                        "ridotta dal limite di leva",
                    ),
                    ("Rischio", "importo perso se scatta lo stop"),
                    ("P&amp;L", "profitto o perdita in valuta"),
                ],
            )
        }
""",
    ),
    HelpSection(
        "money",
        "Capitale e rischio",
        """
<ul>
<li><b>Capitale</b>: somma iniziale, nella valuta di quotazione dello strumento (USD per
AAPL, EUR per ENI.MI, USDT per BTC/USDT).</li>
<li><b>Rischio/trade</b>: percentuale del capitale persa se scatta lo stop. Valori tipici:
0,5-2 %.</li>
<li><b>Leva massima</b>: il controvalore della posizione non supera capitale × leva. È un
<b>limite, non un moltiplicatore</b>: la quantità nasce dal rischio % e dalla distanza dello
stop, e la leva interviene solo se quella quantità supererebbe il limite. Con stop molto vicini
la quantità viene ridotta (⚠ in tabella) e si rischia meno del previsto. Sotto il campo
un'indicazione riporta la leva effettivamente usata e, se il limite è stato raggiunto, quella
necessaria per non ridurre alcuna posizione. Se il limite non viene mai raggiunto, alzarlo non
cambia i risultati: per posizioni più grandi va aumentato il rischio %.</li>
<li><b>Reinvesti i profitti</b>: il rischio si calcola sul capitale corrente; disattivato, sul
capitale iniziale.</li>
</ul>
<p><b>Formula</b>: quantità = min(capitale × rischio% / |entry − stop|, capitale × leva /
entry). Il P&amp;L di un trade è quantità × rischio unitario × R.</p>
<p>Questi valori aggiornano subito tabella e metriche in valuta, senza rifare l'analisi. Sono
usati anche per la quantità suggerita nei segnali live.</p>
""",
    ),
    HelpSection(
        "live",
        "Segnali live",
        f"""
<h3>Watchlist</h3>
<ul>
<li>Scegli sorgente remota, simbolo e timeframe, poi nella scheda <b>Live</b> premi
<b>Aggiungi mercato corrente</b>. In alternativa aggiungi i simboli dallo Screener.</li>
<li><b>Rimuovi</b> elimina il mercato selezionato.</li>
<li>Colonne: Mercato, Controllo (intervallo), Ultima candela chiusa, Prezzo, Prossimo
controllo, Stato (<i>Controllo…</i>, <i>Nessun segnale</i>, <i>Segnale LONG/SHORT</i>,
<i>Errore: …</i>; dopo un errore si ritenta entro un minuto).</li>
</ul>
<h3>Intervallo di controllo</h3>
<p><i>Automatico</i>: controllo alla chiusura di ogni candela (per Yahoo almeno ogni 5 minuti,
perché le sessioni di borsa non coincidono con la griglia UTC). In alternativa un intervallo
fisso, con questi vincoli: minimo 1 minuto per ccxt e 2 per Yahoo, mai oltre la durata della
candela né oltre un giorno, e solo divisori esatti del timeframe, così nessuna chiusura viene
saltata. Un intervallo più lungo riduce il traffico ma ritarda la notifica.</p>
<h3>Monitoraggio</h3>
<ul>
<li><b>Avvia monitoraggio</b> / <b>Ferma monitoraggio</b>; <b>Controlla ora</b> forza un
controllo di tutti i mercati.</li>
<li>Si analizza solo l'ultima candela chiusa, con gli stessi parametri dell'analisi
corrente.</li>
<li>Un nuovo setup produce un segnale acustico e una <b>notifica desktop</b> con direzione,
pattern, livello, entry stimata, stop, target e R:R. Lo stesso segnale non viene notificato due
volte.</li>
<li>Se alla chiusura il monitoraggio era attivo, riparte da solo al successivo avvio.</li>
<li>Chiudendo la finestra con il monitoraggio attivo PyTrader resta nell'area di notifica: per
uscire usa <b>Esci</b> dal menu dell'icona o da File.</li>
</ul>
<h3>Segnali rilevati</h3>
{
            _table(
                ("Colonna", "Contenuto"),
                [
                    ("Rilevato", "momento della rilevazione"),
                    ("Mercato / Candela", "mercato e candela del segnale"),
                    ("Direzione / Pattern", "come nella tabella Setup"),
                    (
                        "Entry ~",
                        "stima: chiusura della candela del segnale; l'ingresso reale è "
                        "all'apertura della successiva",
                    ),
                    ("Stop Loss / Take Profit / R:R", "livelli del setup"),
                    ("Quantità / Rischio", "calcolati con capitale e rischio attuali"),
                ],
            )
        }
<p>Doppio clic su un segnale per aprire il mercato nel grafico. <b>Svuota storico</b> cancella
l'elenco.</p>
""",
    ),
    HelpSection(
        "walkforward",
        "Ottimizzazione walk-forward",
        """
<p><b>Strumenti → Walk-forward</b> (dati caricati necessari). Prova combinazioni di parametri e
ne misura la tenuta su dati mai usati per sceglierle.</p>
<h3>Configurazione</h3>
<ul>
<li><b>Valori da provare</b>: per prossimità, buffer SL, R:R minimo, tolleranza e finestra pivot
indica uno o più valori separati da <i>;</i>. Il numero di combinazioni è il prodotto dei valori
(massimo 400). Più combinazioni significano più rischio di trovarne una buona per caso.</li>
<li><b>Fold</b>: numero di finestre fuori campione consecutive.</li>
<li><b>Rapporto IS/OOS</b>: lunghezza della finestra di ottimizzazione (in-sample) rispetto a
quella di verifica (out-of-sample).</li>
<li><b>In-sample ancorato</b>: ogni finestra di ottimizzazione parte dalla prima candela e si
allunga; altrimenti scorre con lunghezza fissa.</li>
<li><b>Obiettivo</b>: criterio di scelta in-sample. <i>SQN</i> = √n × R medio / deviazione
standard (n fino a 100), premia risultati costanti; <i>Expectancy</i> = R medio;
<i>Totale R</i> = somma.</li>
<li><b>Trade IS minimi</b>: le combinazioni con meno trade in-sample sono scartate.</li>
</ul>
<h3>Risultati</h3>
<ul>
<li><b>Risultato fuori campione</b>: trade, win rate, expectancy, totale R, profit factor e
drawdown di tutti i periodi OOS uniti. È la stima più onesta del comportamento futuro.</li>
<li><b>Efficienza walk-forward</b> = expectancy OOS / expectancy IS media. Sotto il 50 % gran
parte del vantaggio visto in-sample era overfitting.</li>
<li><b>Verdetto</b>: meno di 30 trade OOS → troppo pochi per giudicare; expectancy OOS ≤ 0 →
nessun vantaggio; efficienza &lt; 50 % → overfitting marcato; altrimenti vantaggio
confermato.</li>
<li><b>Stabilità</b>: quante volte ogni valore è stato scelto. Se la scelta cambia a ogni fold,
segue il rumore.</li>
<li><b>Tabella dei fold</b>: periodi IS e OOS, parametri scelti, score e risultati di ciascuna
finestra.</li>
<li><b>Applica parametri consigliati</b>: imposta i valori scelti sulla finestra più recente e
rianalizza.</li>
</ul>
""",
    ),
    HelpSection(
        "screener",
        "Screener multi-simbolo",
        """
<p><b>Strumenti → Screener</b>. Confronta un elenco di simboli e li ordina con un punteggio
0-100 calcolato solo da dati OHLCV. La finestra resta aperta mentre usi il resto
dell'applicazione e conserva i risultati.</p>
<h3>Universo</h3>
<ul>
<li><b>Sorgente</b> (Yahoo o ccxt con exchange), <b>Timeframe</b> (1h, 4h, 1d, 1w),
<b>Candele</b> per simbolo (100-5.000).</li>
<li><b>Universo predefinito</b>: crypto principali (Binance), azioni USA large cap, FTSE MIB.
Sono elenchi modificabili.</li>
<li><b>Simboli</b>: separati da spazio, virgola, punto e virgola o a capo; da 2 a 200.</li>
</ul>
<h3>Punteggio</h3>
<ul>
<li><b>Volume relativo</b>: volume dell'ultima candela chiusa / media delle <i>finestra
volume</i> candele precedenti. 2× = il doppio del solito.</li>
<li><b>Momentum</b>: variazione % del prezzo da <i>periodo momentum</i> candele fa fino a
<i>candele escluse</i> candele fa. Le candele più recenti sono escluse perché sul brevissimo i
prezzi tendono a invertire.</li>
<li>Per ogni fattore si calcola il <b>percentile</b> del simbolo nell'elenco; il punteggio è la
media pesata dei percentili: 50 = nella media, 100 = il migliore in tutti i fattori. Un
<b>peso negativo</b> inverte l'ordine del fattore, <b>0</b> lo esclude.</li>
<li>Prezzi Yahoo rettificati per split e dividendi; date giornaliere di borse diverse
allineate allo stesso giorno.</li>
</ul>
<h3>Classifica</h3>
<p>Colonne: posizione, simbolo, punteggio, volume relativo, momentum %, ATR % (volatilità,
solo informativa), ultima chiusura, ultima candela. ⚠ indica dati meno recenti del resto
dell'universo. Clic sull'intestazione per ordinare. Seleziona una o più righe e premi
<b>Aggiungi alla watchlist</b>, oppure doppio clic / <b>Apri nel grafico</b>. I simboli non
caricati sono elencati in basso (passa il mouse per l'errore). Se i primi 3 simboli falliscono
tutti la scansione si interrompe: il problema è la sorgente o la rete.</p>
<h3>Verifica storica</h3>
<p>A date distanziate di <i>orizzonte verifica</i> candele (periodi che non si sovrappongono)
il punteggio di allora viene confrontato con il rendimento delle candele successive (da
apertura della candela dopo il segnale a chiusura dopo l'orizzonte).</p>
<ul>
<li><b>Periodi indipendenti</b>: date confrontate (servono almeno 20).</li>
<li><b>Simboli per periodo</b>: media dei simboli con dati completi.</li>
<li><b>IC medio</b>: correlazione di rango (Spearman) tra punteggio e rendimento futuro. 0 =
nessuna relazione; valori di 0,03-0,05 sono già considerati utili nella pratica.</li>
<li><b>t-stat</b>: IC medio diviso il suo errore standard. Sotto ±2 la relazione non si
distingue dal caso.</li>
<li><b>Periodi con IC &gt; 0</b>: quota di periodi in cui l'ordine è stato giusto.</li>
<li><b>Quantile alto − basso</b>: differenza media di rendimento tra il gruppo con punteggio
più alto e quello più basso, per periodo.</li>
<li><b>Singoli fattori</b>: IC e t-stat di volume e momentum presi da soli.</li>
<li><b>Quantili Q1…Q5</b> (Q1…Q3 con meno di 15 simboli): rendimento medio rispetto alla media
dell'universo, dal punteggio più basso al più alto.</li>
</ul>
<p><b>Limiti</b>: un elenco scelto oggi esclude i titoli falliti, delistati o usciti dagli
indici (<i>survivorship bias</i>) e i costi non sono inclusi: la verifica è ottimistica. Il
punteggio ordina cosa approfondire; non è un segnale di acquisto. Non cambiare i pesi finché il
risultato migliora: decidili prima. L'ultima configurazione viene ricordata.</p>
""",
    ),
    HelpSection(
        "export",
        "Esportazione JSON",
        """
<p><b>File → Esporta JSON</b> (Ctrl+E) salva l'ultima analisi in un file con:</p>
<ul>
<li><i>app_version</i> e <i>meta</i> (sorgente, simbolo, timeframe);</li>
<li><i>range</i>: prima e ultima candela, numero di candele;</li>
<li><i>levels</i>: supporti e resistenze correnti;</li>
<li><i>metrics</i>: le metriche del backtest; <i>money</i>: parametri e risultati in
valuta;</li>
<li><i>trades</i>: ogni setup con entry, stop, target, R:R, ATR, esito, uscita, R e
quantità.</li>
</ul>
<p>Il formato è adatto ad analisi successive in Python, Excel o altri strumenti.</p>
""",
    ),
    HelpSection(
        "appearance",
        "Aspetto, disposizione e aggiornamenti",
        """
<ul>
<li><b>Visualizza → Tema</b>: scuro, chiaro o come il sistema operativo (segue i cambi del
sistema). La scelta viene ricordata.</li>
<li><b>Visualizza → Ripristina disposizione</b>: riporta pannelli, divisori e barre alla
posizione iniziale.</li>
<li><b>Colonne</b>: clic destro sull'intestazione delle tabelle Setup e Segnali.</li>
<li><b>Aggiornamenti</b>: all'avvio PyTrader verifica in background se su GitHub esiste una
versione più recente; puoi farlo anche da <b>Aiuto → Controlla aggiornamenti</b>. Se accetti,
l'installer viene scaricato e avviato e l'applicazione si chiude; se l'installer non è
disponibile per il tuo sistema si apre la pagina della release.</li>
</ul>
""",
    ),
    HelpSection(
        "shortcuts",
        "Scorciatoie da tastiera",
        _table(
            ("Tasti", "Azione"),
            [
                ("Ctrl+L", "Carica dati"),
                ("F5", "Analizza"),
                ("Ctrl+P", "Mostra/nascondi il pannello Parametri e rischio"),
                ("Ctrl+O", "Apri CSV"),
                ("Ctrl+E", "Esporta JSON"),
                ("F1", "Guida"),
                ("Ctrl+Q", "Esci (macOS e Linux; su Windows usa File → Esci)"),
            ],
        ),
    ),
    HelpSection(
        "files",
        "File salvati",
        """
<p>Tutti nella cartella <b>.pytrader</b> della cartella utente
(es. <i>C:\\Users\\nome\\.pytrader</i> o <i>~/.pytrader</i>):</p>
<ul>
<li><i>settings.json</i>: tema;</li>
<li><i>ui.ini</i>: posizione e dimensioni di finestra, pannelli, divisori e colonne;</li>
<li><i>watchlist.json</i>: mercati monitorati, intervalli e stato del monitoraggio;</li>
<li><i>signals.json</i>: storico dei segnali live;</li>
<li><i>screener.json</i>: ultima configurazione dello screener;</li>
<li><i>app.log</i> (con copie di rotazione): registro degli eventi e degli errori, utile per
segnalare un problema.</li>
</ul>
<p>Cancellare un file riporta la relativa funzione ai valori iniziali.</p>
""",
    ),
    HelpSection(
        "glossary",
        "Glossario",
        _table(
            ("Termine", "Significato"),
            [
                ("ATR", "Average True Range: escursione media delle candele, misura di volatilità"),
                ("Pivot", "massimo o minimo locale confermato da N candele per lato"),
                ("Supporto / resistenza", "fascia di prezzo dove i pivot si concentrano"),
                ("Setup", "proposta di trade con entry, stop loss e take profit"),
                ("R", "unità di rischio: distanza tra entry e stop"),
                ("R:R", "rapporto rendimento/rischio: distanza del target / distanza dello stop"),
                ("Expectancy", "R medio per trade"),
                ("Drawdown", "discesa dal massimo precedente della curva dei risultati"),
                ("Look-ahead", "uso involontario di dati futuri; PyTrader lo evita"),
                (
                    "Overfitting",
                    "parametri adattati al rumore del passato che non reggono sul futuro",
                ),
                ("In-sample / Out-of-sample", "dati usati per scegliere / per verificare"),
                ("IC", "information coefficient: correlazione tra punteggio e rendimento futuro"),
                ("t-stat", "quanto un risultato si distingue dal caso; |t| ≥ 2 come soglia"),
                (
                    "Survivorship bias",
                    "distorsione dovuta a un universo che contiene solo chi è sopravvissuto",
                ),
                ("Slippage", "differenza tra prezzo atteso ed eseguito"),
                ("Spread", "differenza tra prezzo di acquisto e di vendita"),
            ],
        ),
    ),
    HelpSection(
        "faq",
        "Problemi frequenti",
        """
<ul>
<li><b>Nessun dato / errore di rete</b>: controlla simbolo e connessione. Alcuni exchange sono
bloccati in certi paesi: prova un altro exchange ccxt (es. kraken). Yahoo limita lo storico
intraday (vedi Caricare i dati).</li>
<li><b>Nessun setup</b>: serie troppo corta o parametri troppo restrittivi. Aumenta le candele,
la prossimità o riduci l'R:R minimo e i tocchi minimi.</li>
<li><b>Quantità con ⚠</b>: il limite di leva ha ridotto la posizione; aumenta la leva o
accetta un rischio minore.</li>
<li><b>Il walk-forward dice "troppo pochi trade"</b>: carica più candele o allarga la griglia
dei parametri.</li>
<li><b>Lo screener dà "campione insufficiente"</b>: aumenta le candele o riduci l'orizzonte e
il periodo del momentum, oppure usa il pulsante Valori per famiglia di asset.</li>
<li><b>Le notifiche non compaiono</b>: verifica che le notifiche del sistema operativo siano
attive per PyTrader.</li>
</ul>
""",
    ),
    HelpSection(
        "disclaimer",
        "Avvertenze",
        """
<p>PyTrader è uno strumento di analisi e ricerca. I risultati passati e le simulazioni non
garantiscono risultati futuri. I backtest ignorano lo slippage e assumono esecuzioni ai prezzi
indicati. Nessuna funzione costituisce consulenza finanziaria. Verifica sempre i dati alla
fonte e opera solo con capitale che puoi permetterti di perdere.</p>
""",
    ),
)


def full_html(version: str) -> str:
    """Guida completa con indice, per il browser integrato e per l'esportazione PDF."""
    toc = "<br>".join(
        f"<a href='#{s.anchor}'>{n}. {s.title}</a>" for n, s in enumerate(SECTIONS, 1)
    )
    body = "".join(
        f"<a name='{s.anchor}'></a><h2>{n}. {s.title}</h2>{s.body}"
        for n, s in enumerate(SECTIONS, 1)
    )
    return f"<h1>Guida a PyTrader</h1><p>Versione {version}</p><h3>Indice</h3><p>{toc}</p>{body}"
