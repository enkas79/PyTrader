"""Dialoghi: guida all'uso."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser, QVBoxLayout, QWidget

_HELP_HTML = """
<h2>Guida a PyTrader</h2>
<h3>1. Caricare i dati</h3>
<p>Scegli la sorgente (<b>CSV</b>, <b>ccxt</b> per gli exchange crypto, <b>yfinance</b> per
azioni/indici/forex), indica simbolo e timeframe e premi <b>Carica</b>. Se non ricordi il
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
<h3>3. Capitale e rischio</h3>
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
<h3>4. Leggere i risultati</h3>
<p>La tabella <b>Setup</b> elenca tutti i segnali con esito simulato (win/loss/open/pending).
Seleziona una riga per centrare il grafico. La scheda <b>Backtest</b> riporta win rate,
expectancy in R, profit factor e drawdown massimo.</p>
<p><b>Attenzione</b>: i risultati passati non garantiscono quelli futuri. Valuta sempre
l'expectancy su un campione ampio prima di usare un setup.</p>
<h3>5. Esportazione</h3>
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
