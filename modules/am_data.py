"""After Market data: the facts of one market session, from free sources with no key, as one JSON.

    python -m modules.am_data                    # the slot that fits the clock, printed
    python -m modules.am_data --slot close --out output/aftermarket/facts.json

Slots (AM_SLOTS in .env, Jerusalem time; the bot picks the one closest to now):
  premarket   before the New York open: futures, yesterday's close, pre-market movers, today's earnings
              (before the bell), last night's earnings reactions, today's macro events;
  close       at the closing bell: the session's indices and movers, after-hours earnings due, tomorrow;
  weekend     Saturday and Sunday: the week's moves and the week ahead (crypto never closes).

Sources (every one is optional: a source that fails leaves a note in "problems" and the rest goes on):
  Yahoo Finance chart API   quotes with pre/post market prices and 5-minute / daily candles (indices,
                            futures, stocks, crypto). Unofficial but keyless.
  Nasdaq earnings calendar  who reports on a date, before or after the bell, consensus EPS, market cap.
  ForexFactory calendar     this week's macro events (CPI, FOMC, jobs...) with impact and forecast.
  CoinGecko + alternative.me  crypto prices with 24 h change, the crypto fear & greed index.
  CNBC RSS, Google News RSS   headlines: the market as a whole and one search per mover ("why").
The script writer (modules.am_script) may only use numbers that are in this JSON.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import requests

from modules.scene_images import setting

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output" / "aftermarket"
NY = ZoneInfo("America/New_York")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) TrendClip-AfterMarket/1.0"
TIMEOUT = 20

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_TRENDING = "https://query1.finance.yahoo.com/v1/finance/trending/US?count=20"
NASDAQ_EARNINGS = "https://api.nasdaq.com/api/calendar/earnings?date={date}"
FOREX_FACTORY = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
COINGECKO = ("https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
             "&include_24hr_change=true&include_market_cap=true")
FEAR_GREED = "https://api.alternative.me/fng/?limit=1"
CNBC_RSS = "https://www.cnbc.com/id/100003114/device/rss/rss.html"  # Top News
GOOGLE_NEWS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

INDICES = [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^DJI", "Dow Jones"), ("^RUT", "Russell 2000")]
FUTURES = [("ES=F", "S&P 500 futures"), ("NQ=F", "Nasdaq futures"), ("YM=F", "Dow futures")]
VIX = ("^VIX", "VIX")
COINS = [("bitcoin", "BTC", "Bitcoin"), ("ethereum", "ETH", "Ethereum"), ("solana", "SOL", "Solana")]
DEFAULT_WATCHLIST = "TSLA,NVDA,AAPL,MSFT,AMZN,META,GOOGL,AMD,NFLX,PLTR"
MAX_MOVERS = 5
MAX_NEWS = 8
MAX_WHY = 4  # headlines per mover (the catalyst, when the press has one)
SLOTS = ("premarket", "close", "weekend")
# Market holidays (NYSE): the weekend slot covers them too.
HOLIDAYS_2026 = {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
                 "2026-09-07", "2026-11-26", "2026-12-25"}
HOLIDAYS_2027 = {"2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
                 "2027-09-06", "2027-11-25", "2027-12-24"}
HOLIDAYS = HOLIDAYS_2026 | HOLIDAYS_2027
Progress = Callable[[str], None]


class DataError(RuntimeError):
    pass


# --------------------------------------------------------------------------- helpers


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/xml, */*", "Accept-Language": "en-US"})
    return s


def get_json(session: requests.Session, url: str, **kw) -> Any:
    r = session.get(url, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r.json()


def pct(now: float | None, before: float | None) -> float | None:
    if now is None or not before:
        return None
    return round((now / before - 1) * 100, 2)


def r2(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def watchlist() -> list[str]:
    return [s.strip().upper() for s in setting("AM_WATCHLIST", DEFAULT_WATCHLIST).split(",") if s.strip()]


def is_trading_day(day: dt.date) -> bool:
    return day.weekday() < 5 and day.isoformat() not in HOLIDAYS


def next_trading_day(day: dt.date) -> dt.date:
    day += dt.timedelta(days=1)
    while not is_trading_day(day):
        day += dt.timedelta(days=1)
    return day


def previous_trading_day(day: dt.date) -> dt.date:
    day -= dt.timedelta(days=1)
    while not is_trading_day(day):
        day -= dt.timedelta(days=1)
    return day


def slot_for(now: dt.datetime) -> str:
    """The slot that fits the clock: weekend on a day with no session; premarket before the New York
    open (9:30 ET) and in the first hour after it; close from 15:00 ET on, and until the next morning."""
    ny = now.astimezone(NY)
    if not is_trading_day(ny.date()):
        return "weekend"
    hour = ny.hour + ny.minute / 60
    return "premarket" if hour < 10.5 else "close" if hour >= 15 else "premarket"


# --------------------------------------------------------------------------- Yahoo


def parse_chart(data: dict) -> dict | None:
    """One Yahoo chart answer -> {symbol, name, price, previous_close, change_pct, extended_price,
    extended_change_pct, session, series: [[ts, close]...], day_high, day_low, volume}. The extended
    price is the last trade outside regular hours (pre or post market) when the chart has one."""
    try:
        result = data["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return None
    meta = result.get("meta") or {}
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    series = [[t, round(c, 4)] for t, c in zip(stamps, closes) if c is not None]
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") if meta.get("previousClose") is None else meta.get("previousClose")
    out = {
        "symbol": meta.get("symbol", ""), "name": meta.get("shortName") or meta.get("longName") or meta.get("symbol", ""),
        "price": r2(price), "previous_close": r2(prev), "change_pct": pct(price, prev),
        "day_high": r2(meta.get("regularMarketDayHigh")), "day_low": r2(meta.get("regularMarketDayLow")),
        "volume": meta.get("regularMarketVolume"), "currency": meta.get("currency", "USD"),
        "extended_price": None, "extended_change_pct": None, "session": "regular", "series": series,
    }
    regular_time = meta.get("regularMarketTime")
    periods = meta.get("currentTradingPeriod") or {}
    regular = periods.get("regular") or {}
    if series and regular_time and meta.get("hasPrePostMarketData"):
        last_ts, last = series[-1]
        start, end = regular.get("start"), regular.get("end")
        if start and end and (last_ts < start or last_ts >= end) and last_ts > regular_time and price:
            out["extended_price"] = r2(last)
            out["extended_change_pct"] = pct(last, price)
            out["session"] = "pre" if last_ts < start else "post"
    return out


def quote(session: requests.Session, symbol: str, range_: str = "1d", interval: str = "5m", prepost: bool = True) -> dict | None:
    params = {"range": range_, "interval": interval, "includePrePost": "true" if prepost else "false"}
    try:
        return parse_chart(get_json(session, YAHOO_CHART.format(symbol=requests.utils.quote(symbol, safe="")), params=params))
    except Exception as err:  # noqa: BLE001
        logger.warning("Yahoo %s: %s", symbol, err)
        return None


def week_closes(session: requests.Session, symbol: str, days: int = 5) -> list[float]:
    q = quote(session, symbol, range_="1mo", interval="1d", prepost=False)
    return [c for _, c in (q or {}).get("series", [])][-days:]


def trending(session: requests.Session) -> list[str]:
    try:
        data = get_json(session, YAHOO_TRENDING)
        symbols = [q["symbol"] for q in data["finance"]["result"][0]["quotes"]]
    except Exception as err:  # noqa: BLE001
        logger.warning("Yahoo trending: %s", err)
        return []
    return [s for s in symbols if re.fullmatch(r"[A-Z]{1,5}", s)]


# --------------------------------------------------------------------------- Nasdaq earnings


def parse_earnings(data: dict) -> list[dict]:
    rows = ((data or {}).get("data") or {}).get("rows") or []
    out = []
    for row in rows:
        time_ = row.get("time", "")
        when = "pre" if "pre" in time_ else "post" if "after" in time_ else "unknown"
        cap = row.get("marketCap", "") or ""
        cap_num = int(re.sub(r"[^\d]", "", cap) or 0)
        out.append({"symbol": row.get("symbol", ""), "name": row.get("name", ""), "time": when,
                    "eps_forecast": row.get("epsForecast", "") or "", "last_year_eps": row.get("lastYearEPS", "") or "",
                    "market_cap": cap, "market_cap_num": cap_num})
    out.sort(key=lambda e: -e["market_cap_num"])
    return out


def earnings_on(session: requests.Session, day: dt.date, limit: int = 8) -> list[dict]:
    try:
        data = get_json(session, NASDAQ_EARNINGS.format(date=day.isoformat()))
    except Exception as err:  # noqa: BLE001
        logger.warning("Nasdaq earnings %s: %s", day, err)
        raise DataError(f"Nasdaq earnings calendar: {err}") from err
    return [{k: v for k, v in e.items() if k != "market_cap_num"} for e in parse_earnings(data)[:limit]]


# --------------------------------------------------------------------------- ForexFactory


def parse_macro(events: list[dict], day: dt.date, countries: tuple[str, ...] = ("USD",),
                impacts: tuple[str, ...] = ("High", "Medium")) -> list[dict]:
    out = []
    for ev in events or []:
        try:
            when = dt.datetime.fromisoformat(ev["date"])
        except (KeyError, ValueError):
            continue
        if when.astimezone(NY).date() != day or ev.get("country") not in countries or ev.get("impact") not in impacts:
            continue
        out.append({"title": ev.get("title", ""), "time_et": when.astimezone(NY).strftime("%H:%M"),
                    "impact": ev.get("impact", ""), "forecast": ev.get("forecast", "") or "",
                    "previous": ev.get("previous", "") or ""})
    return out


def macro_on(session: requests.Session, day: dt.date) -> list[dict]:
    try:
        return parse_macro(get_json(session, FOREX_FACTORY), day)
    except Exception as err:  # noqa: BLE001
        raise DataError(f"ForexFactory calendar: {err}") from err


# --------------------------------------------------------------------------- crypto


def crypto(session: requests.Session) -> list[dict]:
    data = get_json(session, COINGECKO.format(ids=",".join(c[0] for c in COINS)))
    out = []
    for cid, sym, name in COINS:
        row = data.get(cid) or {}
        out.append({"symbol": sym, "name": name, "price": r2(row.get("usd")), "change_24h_pct": r2(row.get("usd_24h_change")),
                    "market_cap": row.get("usd_market_cap")})
    return out


def fear_greed(session: requests.Session) -> dict | None:
    data = get_json(session, FEAR_GREED)
    row = (data.get("data") or [{}])[0]
    return {"value": int(row["value"]), "label": row.get("value_classification", "")} if row.get("value") else None


# --------------------------------------------------------------------------- news


def parse_rss(xml_text: str, limit: int = 10) -> list[dict]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        source = (item.findtext("source") or "").strip()
        if not source and " - " in title:
            title, source = title.rsplit(" - ", 1)
        snippet = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        snippet = re.sub(r"\s+", " ", snippet).strip()[:240]
        items.append({"title": title.strip(), "source": source, "url": (item.findtext("link") or "").strip(), "snippet": snippet})
        if len(items) >= limit:
            break
    return items


def rss(session: requests.Session, url: str, limit: int = 10) -> list[dict]:
    try:
        r = session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        return parse_rss(r.text, limit)
    except Exception as err:  # noqa: BLE001
        logger.warning("RSS %s: %s", url[:60], err)
        return []


def google_news(session: requests.Session, query: str, limit: int = 5) -> list[dict]:
    return rss(session, GOOGLE_NEWS.format(query=requests.utils.quote(query)), limit)


# --------------------------------------------------------------------------- the facts


def _note(problems: list[str], source: str, err: Exception) -> None:
    logger.warning("%s: %s", source, err)
    problems.append(f"{source}: {str(err)[:120]}")


def movers_for(session: requests.Session, slot: str, candidates: list[str], limit: int = MAX_MOVERS,
               progress: Progress = lambda m: None) -> list[dict]:
    """Quotes for every candidate, ranked by the move that matters for the slot: the extended-hours move
    before the open, the session's move at the close. Symbols Yahoo does not know are skipped."""
    quotes = []
    seen = set()
    for symbol in candidates:
        if symbol in seen:
            continue
        seen.add(symbol)
        progress(f"Quote {symbol}")
        q = quote(session, symbol)
        if q and q.get("price"):
            quotes.append(q)
    for q in quotes:
        ext = q["extended_change_pct"]
        q["move_pct"] = ext if slot == "premarket" and ext is not None else q["change_pct"]
    quotes = [q for q in quotes if q.get("move_pct") is not None]
    quotes.sort(key=lambda q: -abs(q["move_pct"]))
    return quotes[:limit]


def why_for(session: requests.Session, mover: dict, limit: int = MAX_WHY) -> list[dict]:
    """Headlines that may hold the catalyst: by ticker, then by the company's name ("Tesla, Inc." -> "Tesla")."""
    name = re.split(r",| Inc\b| Corp\b| Ltd\b| plc\b| Company\b| Holdings\b", mover.get("name") or "")[0].strip()
    items = google_news(session, f"{mover['symbol']} stock", limit)
    if name and name.upper() != mover["symbol"]:
        items += google_news(session, f"{name} shares why", limit)
    seen, out = set(), []
    for it in items:
        key = it["title"].lower()[:60]
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out[:limit]


def build(slot: str | None = None, now: dt.datetime | None = None, session: requests.Session | None = None,
          progress: Progress = lambda m: None, news: bool = True) -> dict[str, Any]:
    """Every fact of the session as one dict (see the module doc). Never raises for one source."""
    session = session or _session()
    now = now or dt.datetime.now(dt.timezone.utc)
    slot = slot or slot_for(now)
    if slot not in SLOTS:
        raise DataError(f"slot must be one of {', '.join(SLOTS)}")
    ny = now.astimezone(NY)
    today = ny.date()
    session_day = today if is_trading_day(today) else previous_trading_day(today)
    next_day = next_trading_day(session_day)
    problems: list[str] = []
    facts: dict[str, Any] = {
        "slot": slot, "date": today.isoformat(), "session_day": session_day.isoformat(), "next_session": next_day.isoformat(),
        "made_at": now.isoformat(timespec="seconds"), "ny_time": ny.strftime("%a %b %d, %H:%M ET"),
        "indices": [], "futures": [], "vix": None, "movers": [], "earnings": {}, "macro": [], "crypto": [],
        "crypto_fear_greed": None, "news": [], "problems": problems,
    }

    progress("Indices")
    for symbol, name in INDICES:
        q = quote(session, symbol, prepost=False)
        if q:
            q["name"] = name
            q["week"] = week_closes(session, symbol)
            q.pop("series", None)
            facts["indices"].append(q)
    if not facts["indices"]:
        problems.append("Yahoo: no index quotes")
    if slot != "close":
        progress("Futures")
        for symbol, name in FUTURES:
            q = quote(session, symbol, range_="1d", interval="15m", prepost=False)
            if q:
                q["name"] = name
                q.pop("series", None)
                facts["futures"].append(q)
    vix = quote(session, VIX[0], prepost=False)
    if vix:
        vix["name"] = VIX[1]
        vix.pop("series", None)
        facts["vix"] = vix

    progress("Earnings calendar")
    earnings: dict[str, list[dict]] = {}
    try:
        earnings["today"] = earnings_on(session, session_day)
        earnings["next"] = earnings_on(session, next_day)
        if slot == "premarket":
            earnings["last_night"] = [e for e in earnings_on(session, previous_trading_day(session_day)) if e["time"] == "post"]
    except DataError as err:
        _note(problems, "earnings", err)
    facts["earnings"] = earnings

    progress("Macro calendar")
    try:
        facts["macro"] = macro_on(session, session_day if slot != "close" else next_day)
    except DataError as err:
        _note(problems, "macro", err)

    progress("Movers")
    candidates = list(watchlist())
    reported = [e["symbol"] for e in earnings.get("last_night", []) + [e for e in earnings.get("today", []) if e["time"] == "pre"]]
    candidates = reported[:6] + candidates + trending(session)
    facts["movers"] = movers_for(session, slot, candidates, progress=progress)
    for m in facts["movers"]:
        m["week"] = week_closes(session, m["symbol"])
        m["why"] = why_for(session, m) if news else []
        m["reported"] = m["symbol"] in reported

    progress("Crypto")
    try:
        facts["crypto"] = crypto(session)
    except Exception as err:  # noqa: BLE001
        _note(problems, "coingecko", err)
    try:
        facts["crypto_fear_greed"] = fear_greed(session)
    except Exception as err:  # noqa: BLE001
        _note(problems, "fear & greed", err)
    btc = quote(session, "BTC-USD", range_="1d", interval="15m", prepost=False)
    if btc:
        facts["btc_series"] = btc["series"]

    if news:
        progress("Headlines")
        query = {"premarket": "stock market premarket", "close": "stocks close Wall Street",
                 "weekend": "stock market week ahead"}[slot]
        facts["news"] = (google_news(session, query, MAX_NEWS) + rss(session, CNBC_RSS, MAX_NEWS))[:MAX_NEWS * 2]
    return facts


def facts_path(day: dt.date | str, slot: str, out_dir: Path | None = None) -> Path:
    day = day if isinstance(day, str) else day.isoformat()
    return (out_dir or OUTPUT_DIR) / f"{day}_{slot}" / "facts.json"


def save(facts: dict[str, Any], out_dir: Path | None = None) -> Path:
    path = facts_path(facts["date"], facts["slot"], out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def summary(facts: dict[str, Any]) -> str:
    """A few lines for the terminal or Discord."""
    lines = [f"{facts['slot']} · {facts['ny_time']}"]
    for q in facts["indices"]:
        lines.append(f"  {q['name']:<14} {q['price']:>10,.2f}  {q['change_pct']:+.2f}%")
    for q in facts["futures"]:
        lines.append(f"  {q['name']:<14} {q['price']:>10,.2f}  {q['change_pct']:+.2f}%")
    for m in facts["movers"]:
        ext = f" ({m['session']} {m['extended_price']:,.2f} {m['extended_change_pct']:+.2f}%)" if m["extended_price"] else ""
        lines.append(f"  {m['symbol']:<6} {m['price']:>10,.2f}  {m['change_pct']:+.2f}%{ext}")
    for c in facts["crypto"]:
        lines.append(f"  {c['symbol']:<6} {c['price']:>10,.2f}  {c['change_24h_pct']:+.2f}%")
    today = facts["earnings"].get("today") or []
    if today:
        lines.append("  earnings: " + ", ".join(f"{e['symbol']} ({e['time']})" for e in today[:6]))
    if facts["macro"]:
        lines.append("  macro: " + "; ".join(f"{e['time_et']} {e['title']}" for e in facts["macro"][:4]))
    if facts["problems"]:
        lines.append("  problems: " + " | ".join(facts["problems"]))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slot", choices=SLOTS)
    parser.add_argument("--out", type=Path, help="write the JSON here (default: output/aftermarket/<date>_<slot>/facts.json)")
    parser.add_argument("--no-news", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    started = time.time()
    facts = build(args.slot, progress=lambda m: print("..", m), news=not args.no_news)
    path = args.out or facts_path(facts["date"], facts["slot"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(summary(facts))
    print(f"-> {path} ({time.time() - started:.0f}s)")


if __name__ == "__main__":
    main()
