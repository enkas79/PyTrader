"""Temi: sostituzione dei token nel foglio di stile, contrasto WCAG AA, persistenza."""

from __future__ import annotations

import re

import pytest

from pytrader.gui.theme import (
    DARK,
    LIGHT,
    Palette,
    ThemeMode,
    contrast_ratio,
    load_template,
    render_stylesheet,
    resolve_palette,
)
from pytrader.settings import AppSettings

PALETTES = [DARK, LIGHT]


def test_template_senza_token_residui() -> None:
    template = load_template()
    for palette in PALETTES:
        qss = render_stylesheet(template, palette)
        assert "@" not in qss
        assert palette.window in qss and palette.accent in qss


def test_token_sconosciuto() -> None:
    with pytest.raises(KeyError):
        render_stylesheet("QWidget { color: @inesistente; }", DARK)


def test_tutti_i_token_del_template_esistono() -> None:
    tokens = set(re.findall(r"@([a-z_]+)", load_template()))
    assert tokens <= set(Palette.__dataclass_fields__)


@pytest.mark.parametrize("palette", PALETTES, ids=["scuro", "chiaro"])
def test_contrasto_wcag_aa(palette: Palette) -> None:
    pairs = [
        (palette.text, palette.window),
        (palette.text, palette.surface),
        (palette.text, palette.input),
        (palette.text, palette.table_alt),
        (palette.text_muted, palette.window),
        (palette.text_muted, palette.surface),
        (palette.accent_text, palette.accent),
        (palette.accent_text, palette.accent_hover),
        (palette.up, palette.window),
        (palette.down, palette.window),
        (palette.up, palette.table_alt),
        (palette.down, palette.table_alt),
        (palette.chart_fg, palette.chart_bg),
    ]
    for fg, bg in pairs:
        assert contrast_ratio(fg, bg) >= 4.5, (fg, bg)


def test_risoluzione_tema() -> None:
    assert resolve_palette(ThemeMode.DARK, system_dark=False) is DARK
    assert resolve_palette(ThemeMode.LIGHT, system_dark=True) is LIGHT
    assert resolve_palette(ThemeMode.SYSTEM, system_dark=True) is DARK
    assert resolve_palette(ThemeMode.SYSTEM, system_dark=False) is LIGHT


def test_impostazioni_persistenti(tmp_path) -> None:
    path = tmp_path / "settings.json"
    settings = AppSettings(path=path)
    assert settings.theme is ThemeMode.DARK  # default: tema scuro storico
    settings.theme = ThemeMode.LIGHT
    settings.save()
    assert AppSettings.load(path).theme is ThemeMode.LIGHT
    path.write_text('{"theme": "fucsia"}')
    assert AppSettings.load(path).theme is ThemeMode.DARK
