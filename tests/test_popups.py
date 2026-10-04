import io
import subprocess

import pytest
from PIL import Image

from trendclip import popups, video_assembler as va
from trendclip.config import Settings
from trendclip.video_assembler import Word


def make_settings(tmp_path):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output")


WORDS = [Word("My", 0.0, 0.2), Word("cat", 0.2, 0.5), Word("hid", 0.5, 0.8), Word("in", 0.8, 0.9),
         Word("the", 0.9, 1.0), Word("laundry", 1.0, 1.5), Word("basket.", 1.5, 2.0), Word("Two", 3.0, 3.2),
         Word("cats", 3.2, 3.6), Word("then", 3.6, 3.8), Word("ate", 3.8, 4.0), Word("pizza!", 4.0, 4.6)]


def test_schedule_times_popups_to_words_and_spaces_them():
    wanted = [popups.Popup(word="cat"), popups.Popup(word="laundry basket"), popups.Popup(word="cats"),
              popups.Popup(word="pizza"), popups.Popup(word="dragon")]
    timed = popups.schedule(wanted, WORDS, total=5.0)
    # laundry (1.0s) is too close after cat (0.15s); "cats" at 3.15s; pizza at 3.95s too close to cats
    assert [(t.popup.word, t.start) for t in timed] == [("cat", 0.15), ("cats", 3.15)]
    assert timed[0].end == pytest.approx(2.65) and timed[1].end == 5.0


def test_clean_popups_keeps_script_words_in_order():
    script = "My cat hid in the laundry basket. Then ate pizza!"
    raw = [popups.Popup(word="pizza", emoji="🍕"), popups.Popup(word="dragon"), popups.Popup(word="Cat", emoji="🐈"),
           popups.Popup(word="cat", emoji="🐈")]
    assert [p.word for p in popups.clean_popups(raw, script)] == ["Cat", "pizza"]


def test_emoji_codes_with_and_without_variation_selector():
    assert popups.emoji_codes("🐈") == ["1f408"]
    assert popups.emoji_codes("❤️") == ["2764-fe0f", "2764"]
    assert popups.emoji_codes("") == []


def test_sticker_has_outline_and_transparent_corners():
    img = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    img.paste((200, 30, 30, 255), (100, 50, 200, 150))
    sticker = popups.make_sticker(img, size=200)
    corners, clear = popups.transparency(sticker)
    assert corners == 4 and clear > 0.15
    assert max(sticker.size) == 200 + 80
    r, g, b, a = sticker.getpixel((40 - 6, sticker.height // 2))  # just outside the shape: white outline
    assert a > 200 and r > 240 and g > 240


def _png(color=(255, 0, 0, 255), transparent=True):
    img = Image.new("RGBA", (120, 120), (0, 0, 0, 0) if transparent else (255, 255, 255, 255))
    img.paste(color, (30, 30, 90, 90))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class FakeSession:
    def __init__(self, pages, images):
        self.pages, self.images, self.calls = pages, images, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if url == popups.COMMONS_API:
            key = params.get("titles") or params["gsrsearch"]
            data = {"query": {"pages": self.pages.get(key, {})}}
            return FakeResp(json_data=data)
        return FakeResp(content=self.images[url])


class FakeResp:
    def __init__(self, json_data=None, content=b""):
        self._json, self.content = json_data, content

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def test_fetch_asset_prefers_emoji_and_caches(tmp_path):
    settings = make_settings(tmp_path)
    page = {"1": {"title": "File:Fluent Emoji Color 1f408.svg", "index": 1,
                  "imageinfo": [{"url": "u", "thumburl": "thumb-cat", "descriptionurl": "https://c/cat"}]}}
    titles = "|".join(f"File:{p.format('1f408')}" for p, _ in popups.EMOJI_SETS)
    session = FakeSession({titles: page}, {"thumb-cat": _png()})
    asset = popups.fetch_asset(settings, popups.Popup(word="cat", emoji="🐈", query="cat"), session)
    assert asset.source == "emoji" and "Microsoft" in asset.credit
    assert (settings.assets_dir / "popups" / asset.filename).is_file()

    again = popups.fetch_asset(settings, popups.Popup(word="cats", emoji="🐈"), FakeSession({}, {}))
    assert again == asset  # cached by emoji, no network


def test_fetch_asset_search_skips_opaque_images(tmp_path):
    settings = make_settings(tmp_path)
    svg = {"1": {"title": "File:Basket photo.svg", "index": 1, "imageinfo": [{"url": "a", "thumburl": "opaque"}]},
           "2": {"title": "File:Company logo.svg", "index": 2, "imageinfo": [{"url": "b", "thumburl": "logo"}]},
           "3": {"title": "File:Basket clipart.svg", "index": 3,
                 "imageinfo": [{"url": "c", "thumburl": "cutout", "descriptionurl": "https://c/basket"}]}}
    session = FakeSession({"basket filemime:image/svg+xml": svg},
                          {"opaque": _png(transparent=False), "logo": _png(), "cutout": _png()})
    asset = popups.fetch_asset(settings, popups.Popup(word="basket", query="basket"), session)
    assert asset.title == "Basket clipart.svg" and asset.source == "commons"
    assert "Wikimedia Commons https://c/basket" in asset.credit
    assert not any(url == "logo" for url, _ in session.calls)


def test_title_card_in_ass(tmp_path):
    path = va.create_karaoke_ass_file([{"word": "hi", "start": 0, "end": 1}], tmp_path / "s.ass",
                                      title_card="cat logic one oh one today")
    text = path.read_text()
    assert "Style: Title," in text
    title = next(line for line in text.splitlines() if ",Title," in line)
    assert title.startswith("Dialogue: 1,0:00:00.00,0:00:03.00,Title")
    assert title.endswith(r"CAT LOGIC ONE\NOH ONE TODAY")
    assert ",Title," not in va.create_karaoke_ass_file([{"word": "hi", "start": 0, "end": 1}], tmp_path / "n.ass").read_text()


def test_assemble_with_popup_and_title_for_real(tmp_path):
    try:
        ffmpeg = va.ffmpeg_with_libass()
    except va.AssemblyError:
        pytest.skip("no ffmpeg with libass")
    gen = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i"]
    subprocess.run(gen + ["color=c=black:size=1080x1920:rate=30:duration=3", "-pix_fmt", "yuv420p",
                          str(tmp_path / "bg.mp4")], check=True)
    subprocess.run(gen + ["sine=frequency=300:duration=3", "-ac", "1", str(tmp_path / "voice.mp3")], check=True)
    sticker = tmp_path / "pop.png"
    img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    img.paste((255, 0, 0, 255), (20, 20, 180, 180))
    img.save(sticker)

    out = va.assemble_video(tmp_path / "bg.mp4", tmp_path / "voice.mp3", [{"word": "hello", "start": 0, "end": 2.5}],
                            tmp_path / "out.mp4", overlays=[va.Overlay(sticker, 1.0, 2.5)],
                            title_card="BIG NEWS", preset="ultrafast")

    def pixel(t, y):
        raw = subprocess.run([ffmpeg, "-loglevel", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1",
                              "-vf", f"crop=2:2:540:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        return tuple(raw[:3])

    assert pixel(0.5, va.POPUP_CENTER_Y)[0] < 40      # before the pop-up: black background
    assert pixel(1.8, va.POPUP_CENTER_Y)[0] > 200     # pop-up visible (red)
    assert pixel(0.9, 184) > (200, 200, 200)          # title card box (white, above the text) at the top    assert pixel(2.8, va.POPUP_CENTER_Y)[0] < 40      # gone after its end
