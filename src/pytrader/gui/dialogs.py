"""Dialoghi: guida all'uso."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout, QWidget

_HELP_HTML = """
<h2>Guida a PyTrader</h2>
<h3>Disposizione della finestra</h3>
<ul>
<li><b>Barra degli strumenti</b>: Carica dati (Ctrl+L), Analizza (F5), Parametri (Ctrl+P),
Walk-forward. Quando cambi un parametro dopo l'analisi, <b>Analizza</b> si evidenzia: i
risultati mostrati non corrispondono più ai valori impostati.</li>
<li><b>Barra laterale</b>: sorgente dati, riepilogo delle impostazioni (clic per modificarle) e
metriche del backtest sempre visibili.</li>
<li><b>Pannello Parametri e rischio</b>: parametri di analisi e capitale. Si aggancia a destra
o a sinistra, oppure si stacca come finestra separata (pulsante in alto a destra del pannello o
trascinandone il titolo), anche su un secondo monitor.</li>
<li><b>Colonne</b>: clic destro sull'intestazione delle tabelle Setup e Segnali per scegliere
le colonne visibili.</li>
<li>Posizione dei pannelli, divisori e colonne vengono ricordati;
<b>Visualizza → Ripristina disposizione</b> torna a quella iniziale.</li>
</ul>
<h3>1. Caricare i dati</h3>
<p>Scegli la sorgente (<b>CSV</b>, <b>ccxt</b> per gli exchange crypto, <b>yfinance</b> per
azioni/indici/forex), indica simbolo e timeframe e premi <b>Carica dati</b>. Se non ricordi il
ticker, scrivi il nome dell'azienda, del fondo o dell'ETF (es. <i>Vanguard world</i>): dopo un
istante compare una lista di suggerimenti da cui scegliere; il campo viene compilato con il
ticker corretto. Con ccxt la ricerca filtra le coppie dell'exchange (es. <i>BTC</i>). Il CSV
deve contenere
le colonne <i>timestamp, open, high, low, close</i> (volume facoltativo). Le candele incoerenti o
duplicate vengono rimosse; i buchi temporali sono segnalati nella scheda <b>Dati</b> ma
<u>non</u> vengono riempiti.</p>
<h3>2. Come nascono i segnali</h3>
<ul>
<li><b>Pivot</b>: un massimo/minimo è confermato solo dopo <i>N</i> candele (finestra pivot).</li>
<li><b>Livelli</b>: pivot vicini (entro tolleranza × ATR) formano una fascia; servono almeno
i tocchi minimi indicati.</li>
<li><b>Confluenza</b>: un pattern rialzista vicino a un supporto (o ribassista vicino a una
resistenza), entro la prossimità × ATR.</li>
<li><b>Entry</b> all'apertura della candela successiva al pattern. <b>SL</b> oltre la fascia
di buffer × ATR. <b>TP</b> sul livello strutturale successivo: se l'R:R è inferiore al minimo
il setup viene scartato.</li>
</ul>
<h3>3. Capitale e rischio (pannello Parametri e rischio)</h3>
<ul>
<li><b>Capitale</b>: somma iniziale, nella valuta in cui è quotato lo strumento.</li>
<li><b>Rischio/trade</b>: percentuale del capitale persa se scatta lo stop loss. La quantità
è calcolata come <i>capitale × rischio% / distanza entry-stop</i>.</li>
<li><b>Leva massima</b>: il controvalore della posizione non supera capitale × leva. Con 1×
e stop molto vicini la quantità viene ridotta (simbolo ⚠) e si rischia meno del previsto.</li>
<li><b>Reinvesti i profitti</b>: il rischio si calcola sul capitale corrente anziché su quello
iniziale.</li>
</ul>
<p>Modificando questi valori tabella e metriche si aggiornano subito, senza rifare l'analisi.</p>
<h3>4. Segnali live</h3>
<ul>
<li>Scegli sorgente, simbolo e timeframe, poi nella scheda <b>Live</b> premi
<b>Aggiungi mercato corrente</b>. Ripeti per tutti i mercati da seguire.</li>
<li><b>Avvia monitoraggio</b>: ogni mercato viene controllato alla chiusura di ogni candela
(per Yahoo almeno ogni 5 minuti). Si analizzano solo candele chiuse.</li>
<li><b>Controllo</b>: nella watchlist puoi scegliere per ogni mercato un intervallo fisso
invece di <i>Automatico</i>. Le scelte dipendono dal mercato: minimo 1 minuto per gli exchange
crypto, 2 minuti per Yahoo (limita le richieste), mai oltre la durata della candela né oltre un
giorno, e solo valori che dividono esattamente il timeframe, così nessuna chiusura viene
saltata. Un intervallo più lungo riduce il traffico ma ritarda la notifica.</li>
<li>Quando l'ultima candela chiusa genera un setup ricevi una <b>notifica desktop</b> con
direzione, entry stimata, stop loss, take profit e R:R. Lo storico mostra anche la quantità
suggerita in base a capitale e rischio impostati.</li>
<li>Chiudendo la finestra con il monitoraggio attivo, PyTrader resta nell'area di notifica
(icona vicino all'orologio): per uscire usa <b>Esci</b> dal menu dell'icona o da File.</li>
<li>Doppio clic su un segnale per aprire quel mercato nel grafico.</li>
</ul>
<p><b>Nota</b>: l'entry è una stima (chiusura della candela del segnale); l'ingresso reale
avviene all'apertura della candela successiva. PyTrader non invia ordini all'exchange.</p>
<h3>5. Leggere i risultati</h3>
<p>La tabella <b>Setup</b> elenca tutti i segnali con esito simulato (win/loss/open/pending).
Seleziona una riga per centrare il grafico. Il riquadro <b>Backtest</b> della barra laterale
riporta win rate, expectancy in R, profit factor e drawdown massimo.</p>
<p><b>Attenzione</b>: i risultati passati non garantiscono quelli futuri. Valuta sempre
l'expectancy su un campione ampio prima di usare un setup.</p>
<h3>6. Ottimizzazione walk-forward</h3>
<p><b>Strumenti → Ottimizzazione walk-forward</b> prova tutte le combinazioni dei valori
indicati (separati da <i>;</i>). La serie è divisa in <b>fold</b>: in ognuno i parametri sono
scelti sulla finestra <b>in-sample</b> (IS) e verificati sulla finestra successiva
<b>out-of-sample</b> (OOS), mai vista durante la scelta.</p>
<ul>
<li>Conta solo il <b>risultato OOS</b>: è la stima più onesta del comportamento futuro.</li>
<li><b>Efficienza walk-forward</b> = expectancy OOS / expectancy IS. Sotto il 50% il vantaggio
visto nel backtest è in gran parte overfitting.</li>
<li><b>Stabilità</b>: se i parametri scelti cambiano a ogni fold, la scelta insegue il
rumore.</li>
<li>Servono almeno 30 trade OOS per giudicare. Più combinazioni provi, più è facile trovarne
una buona per caso: preferisci griglie piccole.</li>
<li><b>Applica parametri consigliati</b> imposta i valori scelti sulla finestra più recente e
ripete l'analisi.</li>
</ul>
<p>Lo slippage non è simulato: i risultati reali saranno peggiori.</p>
<h3>7. Tema</h3>
<p><b>Visualizza → Tema</b>: scuro, chiaro o come il sistema operativo. La scelta viene
ricordata al riavvio.</p>
<h3>8. Esportazione</h3>
<p><b>File → Esporta JSON</b> salva setup, esiti, livelli correnti e metriche.</p>
"""


class HelpDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Guida")
        self.resize(640, 560)
        browser = QTextBrowser(self)
        browser.setHtml(_HELP_HTML)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        layout.addWidget(browser)
        layout.addWidget(buttons)
