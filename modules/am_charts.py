"""After Market chart cards: 9:16 pictures drawn with Pillow from the facts JSON (modules.am_data), one per
beat of the video, with the Short's captions in mind: the header sits in the top fifth, the chart in the
middle, and the band the karaoke captions cover (about y 1200-1340) stays empty.

    python -m modules.am_charts output/aftermarket/2026-10-27_close/facts.json out/   # every card

Cards: board (the indices, futures before the open, VIX), ticker (one mover: price, move, intraday line),
crypto (Bitcoin's day, the coins, fear & greed), calendar (earnings due and macro events).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

SIZE = (1080, 1920)
W, H = SIZE
BG = (11, 15, 23)
PANEL = (18, 24, 36)
GRID = (36, 44, 60)
TEXT = (240, 243, 248)
MUTED = (140, 150, 170)
GREEN = (34, 197, 94)
RED = (239, 68, 68)
AMBER = (245, 158, 11)
BLUE = (96, 165, 250)
CHART_BOX = (90, 660, 990, 1180)  # the line chart (x0, y0, x1, y1)
CAPTION_BAND = (1190, 1350)  # the assembler's karaoke captions sit here: nothing important in it
FLASH_BAND = (400, 520)  # each scene's overlay text flashes in here (am_video.FLASH_Y): the cards keep it free
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FONT_FILES = [
    PROJECT_ROOT / "assets" / "fonts" / "Inter-Bold.ttf",
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/Library/Fonts/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
]
REGULAR_FILES = [
    PROJECT_ROOT / "assets" / "fonts" / "Inter-Regular.ttf",
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/Library/Fonts/Arial.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
]


# --------------------------------------------------------------------------- drawing helpers


def font(size: int, bold: bool = True):
    for path in FONT_FILES if bold else REGULAR_FILES:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def colour(change: float | None) -> tuple[int, int, int]:
    return MUTED if change is None else GREEN if change >= 0 else RED


def money(value: float | None, decimals: int | None = None) -> str:
    if value is None:
        return "—"
    if decimals is None:
        decimals = 0 if value >= 10000 else 2
    return f"{value:,.{decimals}f}"


def signed(change: float | None, suffix: str = "%") -> str:
    return "—" if change is None else f"{change:+.2f}{suffix}"


def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", SIZE, BG)
    glow = Image.new("RGB", SIZE, BG)
    g = ImageDraw.Draw(glow)
    g.ellipse((-300, -500, 900, 500), fill=(20, 30, 52))
    g.ellipse((300, 1500, 1500, 2500), fill=(24, 22, 40))
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(220)), 0.9)
    return img, ImageDraw.Draw(img)


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], s: str, size: int, fill=TEXT, bold: bool = True,
         anchor: str = "la") -> None:
    draw.text(xy, s, font=font(size, bold), fill=fill, anchor=anchor)


def pill(draw: ImageDraw.ImageDraw, xy: tuple[int, int], s: str, size: int, fill, anchor_right: bool = False,
         fg=(10, 12, 18)) -> None:
    f = font(size)
    w = draw.textlength(s, font=f)
    pad = size * 0.5
    x, y = xy
    if anchor_right:
        x -= w + 2 * pad
    draw.rounded_rectangle((x, y, x + w + 2 * pad, y + size * 1.5), radius=size * 0.75, fill=fill)
    draw.text((x + pad, y + size * 0.72), s, font=f, fill=fg, anchor="lm")


def header(draw: ImageDraw.ImageDraw, kicker: str, title: str, sub: str = "", accent=AMBER) -> None:
    pill(draw, (80, 120), kicker, 34, accent)
    text(draw, (80, 200), title, 96)
    if sub:
        text(draw, (80, 318), sub, 38, MUTED, bold=False)


def line_chart(draw: ImageDraw.ImageDraw, img: Image.Image, values: list[float], box=CHART_BOX, reference: float | None = None,
               up: bool | None = None, labels: bool = True, line_width: int = 7) -> None:
    """A filled line over `box`; `reference` draws a dashed level (the previous close); the colour follows
    `up` (None: the last value against the first or the reference)."""
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=28, fill=PANEL)
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        text(draw, ((x0 + x1) // 2, (y0 + y1) // 2), "no chart data", 40, MUTED, anchor="mm")
        return
    lo, hi = min(vals + ([reference] if reference else [])), max(vals + ([reference] if reference else []))
    if hi - lo < 1e-9:
        lo, hi = lo * 0.995, hi * 1.005
    pad = (hi - lo) * 0.12
    lo, hi = lo - pad, hi + pad
    inner = (x0 + 30, y0 + 40, x1 - (150 if labels else 30), y1 - 40)
    ix0, iy0, ix1, iy1 = inner

    def X(i: int) -> float:
        return ix0 + (ix1 - ix0) * i / (len(vals) - 1)

    def Y(v: float) -> float:
        return iy1 - (iy1 - iy0) * (v - lo) / (hi - lo)

    for k in range(1, 4):  # grid
        y = iy0 + (iy1 - iy0) * k / 4
        draw.line((ix0, y, ix1, y), fill=GRID, width=2)
    if up is None:
        base = reference if reference else vals[0]
        up = vals[-1] >= base
    col = GREEN if up else RED
    points = [(X(i), Y(v)) for i, v in enumerate(vals)]
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.polygon([(ix0, iy1), *points, (ix1, iy1)], fill=(*col, 48))
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))
    draw = ImageDraw.Draw(img)
    ref_y = Y(reference) if reference else None
    if ref_y is not None:
        for x in range(int(ix0), int(ix1), 24):
            draw.line((x, ref_y, x + 12, ref_y), fill=MUTED, width=3)
        if labels:
            text(draw, (ix1 + 14, ref_y), money(reference), 30, MUTED, anchor="lm")
    draw.line(points, fill=col, width=line_width, joint="curve")
    lx, ly = points[-1]
    draw.ellipse((lx - 14, ly - 14, lx + 14, ly + 14), fill=col)
    draw.ellipse((lx - 7, ly - 7, lx + 7, ly + 7), fill=TEXT)
    if labels:  # the high and low, unless the reference label already sits there
        for y, value in ((iy0 + 10, max(vals)), (iy1 - 10, min(vals))):
            if ref_y is None or abs(ref_y - y) > 36:
                text(draw, (ix1 + 14, y), money(value), 30, MUTED, anchor="lm")


def sparkline(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], values: list[float]) -> None:
    x0, y0, x1, y1 = box
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        hi = lo + 1
    pts = [(x0 + (x1 - x0) * i / (len(vals) - 1), y1 - (y1 - y0) * (v - lo) / (hi - lo)) for i, v in enumerate(vals)]
    draw.line(pts, fill=colour(vals[-1] - vals[0]), width=5, joint="curve")


def footer(draw: ImageDraw.ImageDraw, s: str) -> None:
    text(draw, (W // 2, 1860), s, 28, MUTED, bold=False, anchor="mm")


def save(img: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, quality=94)
    return path


def nice_date(facts: dict) -> str:
    try:
        return dt.date.fromisoformat(facts["date"]).strftime("%A, %B %d").replace(" 0", " ")
    except (KeyError, ValueError):
        return facts.get("date", "")


KICKER = {"premarket": "BEFORE THE OPEN", "close": "AT THE CLOSE", "weekend": "THE WEEK"}


# --------------------------------------------------------------------------- the cards


def board(facts: dict, path: Path) -> Path:
    """The indices (and futures before the open): name, level, move, a 5-day sparkline each."""
    img, draw = canvas()
    slot = facts.get("slot", "close")
    header(draw, KICKER.get(slot, "MARKETS"), "Markets", nice_date(facts))
    rows = list(facts.get("indices") or [])[:4]
    if slot != "close":
        rows = list(facts.get("futures") or [])[:3] + rows[:2]
    y = 540
    for q in rows[:4]:
        draw.rounded_rectangle((80, y, 1000, y + 136), radius=24, fill=PANEL)
        text(draw, (110, y + 30), q["name"], 40)
        text(draw, (110, y + 84), money(q.get("price")), 36, MUTED, bold=False)
        pill(draw, (1000 - 30, y + 40), signed(q.get("change_pct")), 36, colour(q.get("change_pct")), anchor_right=True)
        if q.get("week"):
            sparkline(draw, (560, y + 30, 760, y + 106), q["week"])
        y += 152
    vix = facts.get("vix")
    if vix and vix.get("price") is not None and y < CAPTION_BAND[0] - 70:
        text(draw, (80, y + 10), f"VIX {money(vix['price'])}  ({signed(vix.get('change_pct'))})", 34, MUTED, bold=False)
    footer(draw, "Data: Yahoo Finance · not financial advice")
    return save(img, path)


def session_label(mover: dict, slot: str) -> tuple[str, float | None, float | None]:
    """(label, price shown, move shown): the extended-hours trade when there is one before the open or
    after the close, else the regular session."""
    ext, ext_pct = mover.get("extended_price"), mover.get("extended_change_pct")
    if ext is not None and ext_pct is not None and mover.get("session") in ("pre", "post"):
        return ("PRE-MARKET" if mover["session"] == "pre" else "AFTER HOURS"), ext, ext_pct
    return ("TODAY" if slot != "weekend" else "THIS WEEK"), mover.get("price"), mover.get("change_pct")


def ticker(mover: dict, slot: str, path: Path, facts: dict | None = None) -> Path:
    """One stock: symbol, name, the price and move that matter now, the intraday line (or the week)."""
    img, draw = canvas()
    label, price, move = session_label(mover, slot)
    pill(draw, (80, 120), label, 34, colour(move))
    text(draw, (80, 200), mover.get("symbol", ""), 120)
    text(draw, (80, 340), (mover.get("name") or "")[:34], 38, MUTED, bold=False)
    text(draw, (80, 530), f"${money(price)}", 96)
    pill(draw, (1000, 548), signed(move), 44, colour(move), anchor_right=True)
    series = [c for _, c in mover.get("series") or []]
    if len(series) >= 10:
        line_chart(draw, img, series, reference=mover.get("previous_close"), up=(move or 0) >= 0)
        draw = ImageDraw.Draw(img)
        text(draw, (CHART_BOX[0] + 30, CHART_BOX[1] + 22), "today, 5-minute", 28, MUTED, bold=False, anchor="lm")
    elif mover.get("week"):
        line_chart(draw, img, mover["week"], up=(move or 0) >= 0)
        draw = ImageDraw.Draw(img)
        text(draw, (CHART_BOX[0] + 30, CHART_BOX[1] + 22), "last 5 sessions", 28, MUTED, bold=False, anchor="lm")
    else:
        line_chart(draw, img, [])
        draw = ImageDraw.Draw(img)
    y = 1400
    stats = [("prev close", money(mover.get("previous_close"))), ("day low", money(mover.get("day_low"))),
             ("day high", money(mover.get("day_high")))]
    for i, (k, v) in enumerate(stats):
        x = 80 + i * 310
        draw.rounded_rectangle((x, y, x + 290, y + 130), radius=22, fill=PANEL)
        text(draw, (x + 24, y + 22), k.upper(), 26, MUTED)
        text(draw, (x + 24, y + 62), v, 40)
    if mover.get("week"):
        text(draw, (80, 1580), "5 days", 26, MUTED)
        sparkline(draw, (80, 1620, 1000, 1760), mover["week"])
    footer(draw, "Data: Yahoo Finance · not financial advice")
    return save(img, path)


def crypto(facts: dict, path: Path) -> Path:
    """Bitcoin's day as the line, the coins as rows, the fear & greed index as a bar."""
    img, draw = canvas()
    coins = facts.get("crypto") or []
    btc = next((c for c in coins if c.get("symbol") == "BTC"), None)
    header(draw, "CRYPTO · 24H", "Bitcoin" if btc else "Crypto",
           f"${money(btc['price'])}  {signed(btc.get('change_24h_pct'))}" if btc else nice_date(facts), accent=AMBER)
    series = [c for _, c in facts.get("btc_series") or []]
    line_chart(draw, img, series, up=(btc or {}).get("change_24h_pct", 0) >= 0 if btc else None, labels=True)
    draw = ImageDraw.Draw(img)
    text(draw, (CHART_BOX[0] + 30, CHART_BOX[1] + 22), "BTC-USD today, 15-minute", 28, MUTED, bold=False, anchor="lm")
    y = 1370
    for c in coins[:3]:
        draw.rounded_rectangle((80, y, 1000, y + 104), radius=22, fill=PANEL)
        text(draw, (110, y + 30), c.get("symbol", ""), 44)
        text(draw, (260, y + 34), c.get("name", ""), 34, MUTED, bold=False)
        text(draw, (700, y + 34), f"${money(c.get('price'))}", 38, anchor="ra")
        pill(draw, (980, y + 28), signed(c.get("change_24h_pct")), 30, colour(c.get("change_24h_pct")), anchor_right=True)
        y += 116
    fg = facts.get("crypto_fear_greed")
    if fg:
        text(draw, (80, y + 8), f"Fear & Greed  {fg['value']} · {fg['label']}", 32, MUTED)
        draw.rounded_rectangle((80, y + 56, 1000, y + 78), radius=11, fill=GRID)
        x = 80 + int(920 * fg["value"] / 100)
        draw.rounded_rectangle((80, y + 56, max(x, 102), y + 78), radius=11, fill=AMBER if 40 <= fg["value"] <= 60 else GREEN if fg["value"] > 60 else RED)
    footer(draw, "Data: CoinGecko, Yahoo Finance, alternative.me · not financial advice")
    return save(img, path)


def calendar(facts: dict, path: Path) -> Path:
    """What is due: earnings (before/after the bell) and the macro events of the session that matters."""
    img, draw = canvas()
    slot = facts.get("slot", "close")
    earnings = facts.get("earnings") or {}
    from modules.am_script import due_earnings

    when = "TODAY" if slot == "premarket" else "TONIGHT & NEXT" if slot == "close" else "NEXT SESSION"
    rows = due_earnings(facts)
    header(draw, f"EARNINGS · {when}", "On deck", nice_date(facts) if slot == "premarket" else facts.get("next_session", ""), accent=BLUE)
    y = 540
    for e in rows[:5]:
        draw.rounded_rectangle((80, y, 1000, y + 116), radius=22, fill=PANEL)
        text(draw, (110, y + 24), e.get("symbol", ""), 44)
        text(draw, (110, y + 78), (e.get("name") or "")[:30], 28, MUTED, bold=False)
        tag = "TONIGHT" if e.get("when") == "tonight" else {"pre": "BEFORE THE BELL", "post": "AFTER THE BELL"}.get(e.get("time"), "TIME TBA")
        pill(draw, (980, y + 20), tag, 24, AMBER if e.get("time") == "pre" else RED if e.get("when") == "tonight" else BLUE, anchor_right=True)
        if e.get("eps_forecast"):
            text(draw, (980, y + 78), f"EPS est. {e['eps_forecast']}", 26, MUTED, bold=False, anchor="ra")
        y += 128
    if not rows:
        text(draw, (80, y), "No big names reporting.", 36, MUTED, bold=False)
        y += 80
    macro = facts.get("macro") or []
    y = max(y + 20, 1400)
    if macro:
        text(draw, (80, y), "MACRO", 30, MUTED)
        y += 50
        for ev in macro[:4]:
            if y > 1800:
                break
            draw.rounded_rectangle((80, y, 1000, y + 84), radius=18, fill=PANEL)
            text(draw, (110, y + 24), f"{ev.get('time_et', '')} ET", 30, AMBER)
            text(draw, (300, y + 24), (ev.get("title") or "")[:34], 32)
            if ev.get("forecast"):
                text(draw, (980, y + 26), f"est. {ev['forecast']}", 26, MUTED, bold=False, anchor="ra")
            y += 96
    footer(draw, "Data: Nasdaq, ForexFactory · not financial advice")
    return save(img, path)


def render(facts: dict, out_dir: Path) -> dict[str, Path]:
    """Every card the facts allow, keyed like the script's visuals: board, crypto, calendar, ticker:<SYM>."""
    out = {"board": board(facts, out_dir / "board.jpg"), "crypto": crypto(facts, out_dir / "crypto.jpg"),
           "calendar": calendar(facts, out_dir / "calendar.jpg")}
    for m in facts.get("movers") or []:
        out[f"ticker:{m['symbol']}"] = ticker(m, facts.get("slot", "close"), out_dir / f"ticker_{m['symbol']}.jpg", facts)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("facts", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    facts = json.loads(args.facts.read_text(encoding="utf-8"))
    for key, path in render(facts, args.out).items():
        print(f"{key:<16} {path}")


if __name__ == "__main__":
    main()
