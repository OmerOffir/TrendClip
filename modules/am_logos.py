"""Company logos for the After Market cards, by ticker, from free keyless sources, cached in assets/logos/.

    python -m modules.am_logos TSLA V KO        # fetch (or show the cache) and print the paths

Sources, in order: Financial Modeling Prep's image-stock PNGs, then Parqet's logo API. A logo that
cannot be had (an unknown symbol, no network) gives None: the cards draw a lettered badge instead.
Tests monkeypatch `fetch` so they never touch the network.
"""

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "assets" / "logos"
SOURCES = [
    "https://financialmodelingprep.com/image-stock/{symbol}.png",
    "https://assets.parqet.com/logos/symbol/{symbol}?format=png",
]
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) TrendClip-AfterMarket/1.0"
TIMEOUT = 12
MIN_SIDE = 48  # a smaller picture is a placeholder, not a logo


def cache_path(symbol: str, cache_dir: Path | None = None) -> Path:
    return (cache_dir or CACHE_DIR) / f"{symbol.upper()}.png"


def _image(data: bytes):
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:  # noqa: BLE001 - not an image
        return None
    if min(img.size) < MIN_SIDE:
        return None
    return img.convert("RGBA")


def fetch(symbol: str, cache_dir: Path | None = None, session: requests.Session | None = None) -> Path | None:
    """The logo as a PNG on disk, or None. A miss is remembered for the run only (a .missing marker
    would hide a symbol that gets a logo later)."""
    symbol = symbol.upper().strip()
    if not symbol.isalnum():
        return None
    path = cache_path(symbol, cache_dir)
    if path.is_file():
        return path
    session = session or requests.Session()
    for url in SOURCES:
        try:
            r = session.get(url.format(symbol=symbol), timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
            if r.status_code != 200 or not r.content:
                continue
            img = _image(r.content)
            if img is None:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            img.save(path)
            return path
        except requests.RequestException as err:
            logger.info("Logo %s from %s: %s", symbol, url.split("/")[2], err)
    return None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for symbol in sys.argv[1:]:
        print(symbol, fetch(symbol) or "no logo")


if __name__ == "__main__":
    main()
