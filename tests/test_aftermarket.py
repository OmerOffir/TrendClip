"""After Market: the facts parsers (Yahoo, Nasdaq, ForexFactory, RSS), the slot clock, the chart cards, the
script checks (every number from the facts) and the template, and the whole video (silent voice, placeholder
broll)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from modules import am_charts, am_data, am_script, am_video, video_gen
from trendclip.video_assembler import estimate_word_timings, media_duration

NY = ZoneInfo("America/New_York")


def yahoo_answer(symbol="TSLA", price=394.63, prev=372.22, closes=(380.0, 390.0, 394.63), extended=None, start=1793021400):
    """A Yahoo chart answer: the regular session 9:30-16:00 ET; `extended` adds a later (post-market) trade."""
    end = start + 6 * 3600 + 1800
    stamps = [start + i * 300 for i in range(len(closes))]
    closes = list(closes)
    if extended is not None:
        stamps.append(end + 600)
        closes.append(extended)
    return {"chart": {"result": [{
        "meta": {"symbol": symbol, "shortName": "Tesla, Inc.", "regularMarketPrice": price, "previousClose": prev,
                 "chartPreviousClose": prev, "regularMarketTime": end - 1, "hasPrePostMarketData": True,
                 "regularMarketDayHigh": 397.27, "regularMarketDayLow": 383.84, "regularMarketVolume": 40244002,
                 "currency": "USD", "currentTradingPeriod": {"regular": {"start": start, "end": end}}},
        "timestamp": stamps, "indicators": {"quote": [{"close": closes}]}}]}}


def facts_fixture(slot="close"):
    tsla = am_data.parse_chart(yahoo_answer())
    tsla.update(move_pct=6.02, week=[372.0, 380.0, 375.0, 372.22, 394.63],
                why=[{"title": "Tesla jumps after a record delivery report - CNBC", "source": "CNBC", "url": ""}], reported=True)
    nvda = am_data.parse_chart(yahoo_answer("NVDA", 180.5, 185.0, (184.0, 182.0, 180.5)))
    nvda.update(name="NVIDIA", move_pct=-2.43, week=[190.0, 188.0, 186.0, 185.0, 180.5], why=[], reported=False)
    return {
        "slot": slot, "date": "2026-10-26", "session_day": "2026-10-26", "next_session": "2026-10-27",
        "made_at": "2026-10-26T20:00:00+00:00", "ny_time": "Mon Oct 26, 16:00 ET",
        "indices": [{"symbol": "^GSPC", "name": "S&P 500", "price": 7678.78, "previous_close": 7624.0, "change_pct": 0.72,
                     "week": [7600.0, 7650.0, 7610.0, 7624.0, 7678.78]},
                    {"symbol": "^IXIC", "name": "Nasdaq", "price": 26198.1, "previous_close": 25954.0, "change_pct": 0.94,
                     "week": [25800.0, 26000.0, 25900.0, 25954.0, 26198.1]}],
        "futures": [{"symbol": "ES=F", "name": "S&P 500 futures", "price": 7715.5, "previous_close": 7659.75, "change_pct": 0.73}],
        "vix": {"symbol": "^VIX", "name": "VIX", "price": 18.38, "change_pct": -5.5},
        "movers": [tsla, nvda],
        "earnings": {"today": [{"symbol": "V", "name": "Visa Inc.", "time": "post", "eps_forecast": "$3.43"}],
                     "next": [{"symbol": "KO", "name": "Coca-Cola", "time": "pre", "eps_forecast": "$0.87"}]},
        "macro": [{"title": "CB Consumer Confidence", "time_et": "10:00", "impact": "Medium", "forecast": "80.7", "previous": "82.1"}],
        "crypto": [{"symbol": "BTC", "name": "Bitcoin", "price": 80064.0, "change_24h_pct": -0.68},
                   {"symbol": "ETH", "name": "Ethereum", "price": 2317.88, "change_24h_pct": -1.56}],
        "crypto_fear_greed": {"value": 50, "label": "Neutral"},
        "btc_series": [[1793030000 + i * 900, 80000 + (i % 5) * 120.0] for i in range(40)],
        "news": [{"title": "Wall Street closes higher", "source": "Reuters", "url": ""}],
        "problems": [],
    }


# --------------------------------------------------------------------------- data


def test_parse_chart_regular_and_extended():
    q = am_data.parse_chart(yahoo_answer())
    assert q["symbol"] == "TSLA" and q["price"] == 394.63 and q["previous_close"] == 372.22 and q["change_pct"] == 6.02
    assert q["session"] == "regular" and q["extended_price"] is None and len(q["series"]) == 3

    q = am_data.parse_chart(yahoo_answer(extended=400.0))
    assert q["session"] == "post" and q["extended_price"] == 400.0 and q["extended_change_pct"] == 1.36
    assert am_data.parse_chart({"chart": {"result": None}}) is None


def test_parse_earnings_macro_and_rss():
    rows = am_data.parse_earnings({"data": {"rows": [
        {"symbol": "KO", "name": "Coca-Cola", "time": "time-pre-market", "marketCap": "$300,000,000", "epsForecast": "$0.87"},
        {"symbol": "V", "name": "Visa Inc.", "time": "time-after-hours", "marketCap": "$728,853,180,000", "epsForecast": "$3.43"},
        {"symbol": "X", "name": "Tiny", "time": "time-not-supplied", "marketCap": ""}]}})
    assert [r["symbol"] for r in rows] == ["V", "KO", "X"] and rows[0]["time"] == "post" and rows[2]["time"] == "unknown"

    events = [{"title": "CPI m/m", "country": "USD", "date": "2026-10-27T08:30:00-04:00", "impact": "High", "forecast": "0.3%", "previous": "0.4%"},
              {"title": "Bank Holiday", "country": "NZD", "date": "2026-10-27T15:00:00-04:00", "impact": "Holiday"},
              {"title": "Speech", "country": "USD", "date": "2026-10-28T10:00:00-04:00", "impact": "Low"}]
    macro = am_data.parse_macro(events, dt.date(2026, 10, 27))
    assert macro == [{"title": "CPI m/m", "time_et": "08:30", "impact": "High", "forecast": "0.3%", "previous": "0.4%"}]

    xml = ("<rss><channel><item><title>Tesla jumps - CNBC</title><link>https://x</link></item>"
           "<item><title>Second</title><source url='u'>Reuters</source></item></channel></rss>")
    assert am_data.parse_rss(xml) == [{"title": "Tesla jumps", "source": "CNBC", "url": "https://x", "snippet": ""},
                                      {"title": "Second", "source": "Reuters", "url": "", "snippet": ""}]
    xml = "<rss><channel><item><title>T</title><description>&lt;ol&gt;&lt;li&gt;Why Tesla jumped&lt;/li&gt;&lt;/ol&gt;</description></item></channel></rss>"
    assert am_data.parse_rss(xml)[0]["snippet"] == "Why Tesla jumped"
    assert am_data.parse_rss("<not xml") == []


def test_slot_for_the_clock_and_trading_days():
    def at(day, hour, minute=0):
        return dt.datetime(2026, 10, day, hour, minute, tzinfo=NY)

    assert am_data.slot_for(at(26, 8, 0)) == "premarket"  # Monday morning
    assert am_data.slot_for(at(26, 16, 5)) == "close"
    assert am_data.slot_for(at(26, 12, 0)) == "premarket"  # midday: the next video is still the morning kind
    assert am_data.slot_for(at(24, 10, 0)) == "weekend"  # Saturday
    assert am_data.slot_for(dt.datetime(2026, 11, 26, 10, tzinfo=NY)) == "weekend"  # Thanksgiving
    assert am_data.next_trading_day(dt.date(2026, 10, 23)) == dt.date(2026, 10, 26)
    assert am_data.previous_trading_day(dt.date(2026, 10, 26)) == dt.date(2026, 10, 23)
    assert am_data.pct(110, 100) == 10.0 and am_data.pct(None, 100) is None and am_data.pct(5, 0) is None


def test_movers_ranked_by_the_move_that_matters(monkeypatch):
    answers = {"TSLA": yahoo_answer("TSLA", 394.63, 372.22, extended=380.0),  # +6% today, -3.7% after hours
               "NVDA": yahoo_answer("NVDA", 180.5, 185.0, (184.0, 180.5)), "NOPE": {"chart": {"result": None}}}
    monkeypatch.setattr(am_data, "get_json", lambda session, url, **kw: answers[url.rsplit("/", 1)[1].split("?")[0]])
    close = am_data.movers_for(None, "close", ["TSLA", "NVDA", "NOPE", "TSLA"])
    assert [m["symbol"] for m in close] == ["TSLA", "NVDA"] and close[0]["move_pct"] == 6.02
    pre = am_data.movers_for(None, "premarket", ["TSLA", "NVDA"])
    assert [m["symbol"] for m in pre] == ["TSLA", "NVDA"] and pre[0]["move_pct"] == -3.71


def test_build_survives_a_dead_source(monkeypatch):
    def get_json(session, url, **kw):
        if "nasdaq" in url or "faireconomy" in url or "coingecko" in url or "alternative" in url or "trending" in url:
            raise RuntimeError("down")
        return yahoo_answer(url.rsplit("/", 1)[1].split("?")[0])

    monkeypatch.setattr(am_data, "get_json", get_json)
    monkeypatch.setattr(am_data, "rss", lambda session, url, limit=10: [])
    facts = am_data.build("close", now=dt.datetime(2026, 10, 26, 16, 5, tzinfo=NY), session=object(), news=False)
    assert facts["slot"] == "close" and facts["date"] == "2026-10-26" and facts["next_session"] == "2026-10-27"
    assert [q["name"] for q in facts["indices"]] == ["S&P 500", "Nasdaq", "Dow Jones", "Russell 2000"] and facts["futures"] == []
    assert facts["movers"] and facts["crypto"] == [] and facts["earnings"] == {}
    assert {p.split(":")[0] for p in facts["problems"]} == {"earnings", "macro", "coingecko", "fear & greed"}
    assert "weekend" in am_data.summary({**facts, "slot": "weekend"})


# --------------------------------------------------------------------------- charts


def test_chart_cards(tmp_path, monkeypatch):
    from modules import am_logos

    monkeypatch.setattr(am_logos, "fetch", lambda symbol, cache_dir=None, session=None: None)
    cards = am_charts.render(facts_fixture(), tmp_path)
    assert set(cards) == {"board", "crypto", "calendar", "ticker:TSLA", "ticker:NVDA"}
    from PIL import Image

    for path in cards.values():
        assert Image.open(path).size == (1080, 1920)
    assert am_charts.session_label({"session": "pre", "extended_price": 400.0, "extended_change_pct": 1.4, "price": 394.0}, "premarket") == ("PRE-MARKET", 400.0, 1.4)
    assert am_charts.session_label({"session": "regular", "price": 394.0, "change_pct": 6.0}, "close") == ("TODAY", 394.0, 6.0)
    assert am_charts.money(80064.0) == "80,064" and am_charts.money(2.86) == "2.86" and am_charts.signed(-0.68) == "-0.68%"
    # before the open the board leads with the futures; a mover with no candles gets its week
    pre = facts_fixture("premarket")
    pre["movers"][0]["series"] = []
    am_charts.render(pre, tmp_path / "pre")
    am_charts.ticker({"symbol": "Z", "name": "Z", "price": None, "change_pct": None}, "close", tmp_path / "empty.jpg")
    frames = am_charts.frames(facts_fixture(), "ticker:TSLA", tmp_path / "frames", count=5)
    assert [f.name for f in frames] == [f"frame_{i:03d}.jpg" for i in range(1, 6)]
    assert am_charts.frames(facts_fixture(), "board", tmp_path / "frames2") == [] and am_charts.frames(facts_fixture(), "ticker:ZZZ", tmp_path / "f3") == []


def test_logo_cache_and_badge(tmp_path, monkeypatch):
    from PIL import Image

    from modules import am_logos

    class Answer:
        def __init__(self, content, status=200):
            self.content, self.status_code = content, status

    buf = __import__("io").BytesIO()
    Image.new("RGBA", (120, 120), (255, 255, 255, 255)).save(buf, format="PNG")
    answers = {"TSLA": Answer(buf.getvalue()), "NOPE": Answer(b"", 404), "TINY": Answer(b"not an image")}

    class Session:
        def get(self, url, timeout=0, headers=None):
            return answers[url.split("/")[-1].split("?")[0].replace(".png", "")]

    assert am_logos.fetch("TSLA", tmp_path, Session()) == tmp_path / "TSLA.png" and (tmp_path / "TSLA.png").is_file()
    assert am_logos.fetch("tsla", tmp_path, None) == tmp_path / "TSLA.png"  # cached: no session needed
    assert am_logos.fetch("NOPE", tmp_path, Session()) is None and am_logos.fetch("TINY", tmp_path, Session()) is None
    assert am_logos.fetch("^GSPC", tmp_path, Session()) is None
    monkeypatch.setattr(am_logos, "CACHE_DIR", tmp_path)
    img = Image.new("RGB", (400, 400), (0, 0, 0))
    am_charts.logo_tile(img, "TSLA", (10, 10), 100)  # a white logo lands on the dark tile
    assert img.getpixel((60, 15)) == (30, 38, 56)
    am_charts.logo_tile(img, "ZZ", (200, 200), 100)  # no logo: the badge
    assert img.getpixel((250, 205)) != (0, 0, 0)


# --------------------------------------------------------------------------- script


def test_numbers_are_checked_against_the_facts():
    facts = facts_fixture()
    allowed = am_script.fact_numbers(facts)
    assert am_script.numbers_in("Tesla at $394.63, up 6.02%, 7,678 on the S&P, 3 names") == [394.63, 6.02, 7678.0, 3.0]
    assert am_script.unknown_numbers("Tesla closed at 395 dollars, up 6 percent; the S&P at 7,679 and 7,680", allowed) == []
    assert am_script.unknown_numbers("Bitcoin at 80,000 dollars, 80,100, and 80,064", allowed) == []
    assert am_script.unknown_numbers("Tesla jumped 12 percent to 410 dollars in 2024", allowed) == ["410"]  # 12 is a free count

    ok = am_script.template_script(facts)
    assert am_script.check(am_script.ScriptDraft(title=ok.title, hook_text=ok.hook_text, scenes=[
        am_script.SceneDraft(narration=s.narration, visual=s.visual, overlay_text=s.overlay_text, image_prompt=s.image_prompt)
        for s in ok.scenes]), facts) == []
    bad = am_script.ScriptDraft(title="t", hook_text="Here is why Tesla soared 9 percent to 450 dollars.", scenes=[
        am_script.SceneDraft(narration="x " * 20 + ".", visual="ticker:AMZN", overlay_text="AMZN"),
        am_script.SceneDraft(narration="A trading floor.", visual="broll", overlay_text="NEXT")])
    problems = am_script.check(bad, facts)
    assert any("450" in p for p in problems) and any("ticker:AMZN" in p for p in problems) and any("newsreader" in p for p in problems)
    assert any("broll without" in p for p in problems) and any("scenes" in p for p in problems) and any("over 14" in p for p in problems)


def test_template_script_tells_the_session():
    facts = facts_fixture()
    script = am_script.template_script(facts)
    assert script.writer == "template" and script.slot == "close"
    assert script.hook_text == "The market just closed, and TSLA just moved 6.0 percent. That decides the next session."
    assert [s.visual for s in script.scenes] == ["board", "ticker:TSLA", "ticker:NVDA", "calendar", "crypto", "broll"]
    assert script.scenes[0].narration == ("Monday, October 26th. Your closing report. A broad rally. The S&P 500 up 0.7 percent "
                                          "and the Nasdaq up 0.9 percent. Volatility eased.")
    assert script.scenes[1].narration == "TSLA up 6 percent. The press says: Tesla jumps after a record delivery report."
    assert script.scenes[2].narration == "NVDA down 2.4 percent. No news behind it yet."
    assert script.scenes[4].narration == "Bitcoin is down 0.7 percent over the day. Ethereum down 1.6 percent. Crypto fear and greed reads neutral."
    assert script.scenes[3].narration == ("On deck. V report after the bell tonight. KO report before the bell tomorrow. "
                                          "CB Consumer Confidence lands at 10:00 Eastern.")
    assert [e["symbol"] for e in am_script.due_earnings(facts)] == ["V", "KO"] and am_script.due_earnings(facts)[0]["when"] == "tonight"
    assert [e["symbol"] for e in am_script.due_earnings(facts_fixture("premarket"))] == ["V"]
    assert script.thumbnail_text == "TSLA +6.0%" and am_script.MIN_WORDS <= script.total_word_count <= am_script.MAX_WORDS
    pre = am_script.template_script(facts_fixture("premarket"))
    assert pre.hook_text == "The bell rings in 90 minutes, and TSLA just moved 6.0 percent. That decides the next session."
    assert pre.scenes[0].narration.startswith("Monday, October 26th. Your pre-market report. A broad rally. The S&P 500 futures up 0.7 percent")
    assert "Follow" not in am_script.spoken(pre)
    compact = am_script.compact_facts(facts)
    assert "series" not in compact["movers"][0] and compact["movers"][0]["why"] == ["Tesla jumps after a record delivery report - CNBC"]
    assert "FACTS (JSON)" in am_script.build_prompt(facts) and "closing bell" in am_script.build_prompt(facts)
    assert "Monday, October 26th. Your closing report." in am_script.build_prompt(facts)


def test_hooks_with_a_stake():
    assert am_script.hook_problems("XP lost half its value in one session.") == []
    assert am_script.hook_problems("Visa reports in 3 hours. 728 billion dollars on the line.") == []
    assert am_script.hook_problems("XP Inc. plunged 49 percent. Here is your close report.") == [
        "the hook opens like a newsreader (\"Here is\"): lead with the shock, no framing"]
    assert "no number" in am_script.hook_problems("Tesla had a wild day on Wall Street.")[0]
    assert am_script.hook_problems("The market just closed, and one stock completely crashed by 50 percent today!") == []
    assert any("words" in p for p in am_script.hook_problems(
        "The S&P 500 index closed up zero point seven percent on a very quiet day for stocks everywhere."))
    assert am_script.hook_problems("") == ["no hook"]
    spx = {"change_pct": 0.72}
    assert am_script.template_hook({"symbol": "XP"}, -49.3, spx, "close") == "The market just closed, and one stock lost half its value today!"
    assert am_script.template_hook({"symbol": "SGRY"}, 47.2, spx, "close") == "The market just closed, and one stock gained 47 percent today!"
    assert am_script.template_hook({"symbol": "NVDA"}, -18.0, spx, "premarket") == "The bell rings in 90 minutes, and one stock crashed 18 percent today!"
    assert am_script.template_hook({"symbol": "NVDA"}, -2.4, spx, "close") == "The market just closed, and NVDA is fighting the tape, down 2.4 percent!"
    assert am_script.template_hook({"symbol": "NVDA"}, None, spx, "weekend") == "The week is over, and NVDA is the name every desk is watching."
    for hook in (am_script.template_hook({"symbol": "XP"}, -49.3, spx, "close"), am_script.template_hook({"symbol": "NVDA"}, -2.4, spx, "close")):
        assert am_script.hook_problems(hook) == []
    many = am_script.ScriptDraft(hook_text="x", scenes=[am_script.SceneDraft(narration="Up 1.5 percent to 394 dollars from 372.", visual="board", overlay_text="X")])
    assert any("reads 3 numbers" in p for p in am_script.check(many, facts_fixture()))
    assert am_script.slate({"date": "2026-10-23", "slot": "premarket"}) == "Friday, October 23rd. Your pre-market report."
    assert am_script.slate({"date": "2026-11-11", "slot": "weekend"}) == "Wednesday, November 11th. Your weekend review."
    assert am_script.slate({"date": "bad", "slot": "close"}) == "Your closing report."


def test_write_script_retries_then_gives_up(monkeypatch):
    facts = facts_fixture()
    good = am_script.template_script(facts)
    good_draft = am_script.ScriptDraft(title=good.title, hook_text=good.hook_text, thumbnail_text=good.thumbnail_text, scenes=[
        am_script.SceneDraft(narration=s.narration, visual=s.visual, overlay_text=s.overlay_text, image_prompt=s.image_prompt) for s in good.scenes])
    bad_draft = good_draft.model_copy(update={"hook_text": "Tesla soared to 999 dollars in one session."})
    drafts = [bad_draft, good_draft]
    prompts = []

    def ask(client, model, prompt):
        prompts.append(prompt)
        return drafts.pop(0)

    monkeypatch.setattr(am_script, "_gemini", lambda client=None: (object(), "m"))
    monkeypatch.setattr(am_script, "_ask", ask)
    script = am_script.write_script(facts)
    assert script.writer == "gemini" and len(prompts) == 2 and "numbers not in the facts: 999" in prompts[1]

    drafts[:] = [bad_draft] * am_script.ATTEMPTS
    with pytest.raises(am_script.ScriptError, match="999"):
        am_script.write_script(facts)


# --------------------------------------------------------------------------- video


def _silent_voice(text, output_mp3, voice, rate, **kwargs):
    from trendclip.video_assembler import ffmpeg_with_libass
    import subprocess

    words = estimate_word_timings(text, 24.0)
    subprocess.run([ffmpeg_with_libass(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t",
                    f"{words[-1].end + 0.3:.2f}", "-q:a", "9", str(output_mp3)], check=True)
    Path(str(output_mp3).replace(".mp3", ".words.json")).write_text(json.dumps([{"word": w.text, "start": w.start, "end": w.end} for w in words]))
    return words


def test_make_video_end_to_end(tmp_path, monkeypatch):
    from trendclip import voiceover
    from trendclip.config import get_settings

    from modules import am_logos

    monkeypatch.setattr(am_logos, "fetch", lambda symbol, cache_dir=None, session=None: None)  # no network: the badges
    monkeypatch.setattr(voiceover, "synthesize", _silent_voice)
    monkeypatch.setenv("AM_TTS_ENGINE", "edge")
    monkeypatch.setattr(am_video, "SFX_DIR", tmp_path / "sfx")
    monkeypatch.setenv("AM_MUSIC", "none")
    monkeypatch.setattr(video_gen, "FPS", 12)
    facts = facts_fixture()
    script = am_script.template_script(facts)
    messages = []
    info = am_video.make_video(facts, script, lambda f, m: messages.append(m), images="placeholder", out_dir=tmp_path,
                               settings=get_settings())
    work = tmp_path / "2026-10-26_close"
    assert (work / "video.mp4").is_file() and info["filename"] == "video.mp4" and 23.0 <= media_duration(work / "video.mp4") <= 27.0
    assert [b["visual"] for b in info["beats"]] == ["board", "board", "ticker:TSLA", "ticker:NVDA", "calendar", "crypto", "broll"]
    assert [b["picture"] for b in info["beats"]][:4] == ["board.jpg", "board.jpg", "ticker_TSLA.mp4", "ticker_NVDA.mp4"]
    assert [b["picture_source"] for b in info["beats"]][2:6] == ["chart-animated", "chart-animated", "chart", "chart-animated"]
    assert info["animated"] == ["crypto", "ticker:NVDA", "ticker:TSLA"] and info["sfx"] is True
    assert (work / "voice_fx.mp3").is_file() and (work / "pictures" / "frames_crypto" / "frame_030.jpg").is_file()
    assert (tmp_path / "sfx" / "whoosh.wav").is_file() and (tmp_path / "sfx" / "tick.wav").is_file()
    assert 3.5 <= media_duration(work / "pictures" / "crypto.mp4") <= 6.0
    assert info["beats"][-1]["picture"].startswith("graded_beat_") and info["beats"][-1]["picture_source"] == "placeholder"
    assert info["pictures"] == "cards + placeholder" and info["writer"] == "template" and info["music"] is None
    assert info["beats"][-1]["text"].endswith("Follow for tomorrow's open.") and info["cover_text"] == "TSLA +6.0%"
    assert info["beats"][1]["text"].startswith("Monday, October 26th. Your closing report.")
    assert info["end_card"] == "Follow for the next session" and info["stickers"] == ["built-in subscribe button"]
    assert "Not financial advice" in info["description"] and "TSLA +6.02%" in info["description"] and "#premarket" in info["description"]
    assert json.loads((work / "script.json").read_text())["writer"] == "template"
    assert json.loads((work / "info.json").read_text())["title"] == info["title"]
    ass = (work / "video.ass").read_text()
    assert "S&P 500 +0.7%" in ass and ",EndCard," in ass and "TSLA +6.0%" in ass
    assert messages[-1] == "Done"


def test_produce_falls_back_to_the_template(monkeypatch, tmp_path):
    facts = facts_fixture("premarket")
    monkeypatch.setattr(am_script, "write_script", lambda f: (_ for _ in ()).throw(am_script.ScriptError("quota")))
    made = {}
    monkeypatch.setattr(am_video, "make_video", lambda f, s, p, **kw: made.update(slot=f["slot"], writer=s.writer) or {"ok": True})
    assert am_video.produce("premarket", facts=facts, out_dir=tmp_path) == {"ok": True}
    assert made == {"slot": "premarket", "writer": "template"}
