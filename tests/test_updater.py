import pytest

from pytrader.updater import ReleaseAsset, ReleaseInfo, is_newer, parse_version, select_asset
from pytrader.updater.checker import _parse_release
from pytrader.version import get_version


def test_parse_version() -> None:
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("2.0") == (2, 0, 0)
    assert parse_version("1.4.0-beta") == (1, 4, 0)
    with pytest.raises(ValueError):
        parse_version("latest")


def test_is_newer() -> None:
    assert is_newer("v0.2.0", "0.1.9")
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("0.10.0", "0.10.1")
    assert not is_newer("garbage", "0.1.0")


def test_parse_release_e_asset() -> None:
    info = _parse_release(
        {
            "tag_name": "v1.0.0",
            "body": "note",
            "html_url": "https://example/r",
            "assets": [
                {"name": "PyTrader-1.0.0-setup.exe", "browser_download_url": "u1", "size": 1},
                {"name": "PyTrader-1.0.0-linux.tar.gz", "browser_download_url": "u2", "size": 2},
                {"name": "PyTrader-1.0.0-macos.zip", "browser_download_url": "u3", "size": 3},
            ],
        }
    )
    assert info.version == "1.0.0" and info.notes == "note"
    assert select_asset(info, "win32").url == "u1"
    assert select_asset(info, "linux").url == "u2"
    assert select_asset(info, "darwin").url == "u3"
    empty = ReleaseInfo("1.0.0", "v1.0.0", "", "", [ReleaseAsset("src.zip", "x", 0)])
    assert select_asset(empty, "win32") is None


def test_versione_da_file() -> None:
    assert parse_version(get_version()) >= (0, 1, 0)
