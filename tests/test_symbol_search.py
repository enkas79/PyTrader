from pytrader.data.symbol_search import SymbolMatch, parse_yahoo_quotes, rank_markets
from pytrader.services import SourceKind, SymbolSearchService

YAHOO_QUOTES = [
    {"symbol": "AAPL", "longname": "Apple Inc.", "exchDisp": "NASDAQ", "quoteType": "EQUITY"},
    {"symbol": "AAPL", "shortname": "Apple dup", "quoteType": "EQUITY"},
    {"symbol": "APLE", "shortname": "Apple Hospitality", "exchange": "NYQ", "quoteType": "EQUITY"},
    {
        "symbol": "VWCE.DE",
        "shortname": "Vanguard FTSE All-World",
        "exchDisp": "XETRA",
        "quoteType": "ETF",
    },
    {"shortname": "senza simbolo"},
    {"symbol": "XYZ", "quoteType": "WEIRD", "typeDisp": "Altro"},
]


def test_parse_yahoo_quotes() -> None:
    matches = parse_yahoo_quotes(YAHOO_QUOTES)
    assert [m.symbol for m in matches] == ["AAPL", "APLE", "VWCE.DE", "XYZ"]
    assert matches[0] == SymbolMatch("AAPL", "Apple Inc.", "NASDAQ", "Azione")
    assert matches[1].exchange == "NYQ"
    assert matches[2].kind == "ETF"
    assert matches[3].kind == "Altro"
    assert matches[0].label == "AAPL — Apple Inc. (NASDAQ · Azione)"


def test_label_minima() -> None:
    assert SymbolMatch("BTC-USD").label == "BTC-USD"


MARKETS = [
    {"symbol": "ETH/BTC", "base": "ETH", "type": "spot"},
    {"symbol": "BTC/USDT:USDT", "base": "BTC", "type": "swap"},
    {"symbol": "BTC/USDT", "base": "BTC", "type": "spot"},
    {"symbol": "BTCDOM/USDT", "base": "BTCDOM", "type": "spot"},
    {"symbol": "OLD/USDT", "base": "OLD", "type": "spot", "active": False},
]


def test_rank_markets() -> None:
    ranked = [m.symbol for m in rank_markets(MARKETS, "btc")]
    # base esatta (spot prima dello swap), poi prefisso, poi sottostringa
    assert ranked == ["BTC/USDT", "BTC/USDT:USDT", "BTCDOM/USDT", "ETH/BTC"]
    assert rank_markets(MARKETS, "old") == []
    assert [m.symbol for m in rank_markets(MARKETS, "btc-usdt")][0] == "BTC/USDT"


def test_service_csv_vuoto() -> None:
    assert SymbolSearchService().search(SourceKind.CSV, "apple") == []
