"""Encore Story's channel picture (800x800) and banner (2560x1440), drawn with the videos' own look."""
import math, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
from modules import band_world as bw, pw_world
from modules.pw_world import font, put

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
pw_world.ensure_fonts()
GOLD = (255, 200, 61); INK = (30, 26, 36)

def stage_bg(w, h):
    import numpy as np
    yy = np.linspace(0, 1, h)[:, None, None]
    g = np.array((14, 16, 52), np.float32) * (1 - yy) + np.array((78, 24, 92), np.float32) * yy
    img = Image.fromarray(np.repeat(g, w, axis=1).clip(0, 255).astype("uint8")).convert("RGBA")
    over = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(over)
    cols = [(255, 80, 180), (80, 200, 255), (255, 220, 90), (140, 255, 140)]
    for k in range(4):
        x0 = w * (0.15 + k * 0.23); x1 = x0 + (k - 1.5) * w * 0.12
        d.polygon([(x0 - 8, h * 0.62), (x0 + 8, h * 0.62), (x1 + w * 0.09, 0), (x1 - w * 0.09, 0)], fill=(*cols[k], 46))
    img.alpha_composite(over.filter(ImageFilter.GaussianBlur(6)))
    return img

# the picture: a gold guitar pick with a star, under a spotlight
S = 800
icon = stage_bg(S, S)
pick = Image.new("RGBA", (S * 2, S * 2), (0, 0, 0, 0)); d = ImageDraw.Draw(pick)
cx, cy = S, S * 0.88
pts = []
for k in range(240):  # a guitar pick: a wide rounded top, narrowing to a rounded point at the bottom
    a = 2 * math.pi * k / 240
    x, y = math.cos(a), math.sin(a)
    down = max(0.0, y)
    pts.append((cx + 560 * x * (1 - 0.55 * down ** 1.5), cy + 520 * y * (1 + 0.42 * down)))
d.polygon(pts, fill=GOLD, outline=INK, width=34)
star = [(cx + (200 if k % 2 == 0 else 84) * math.sin(math.pi * k / 5), cy - 40 - (200 if k % 2 == 0 else 84) * math.cos(math.pi * k / 5)) for k in range(10)]
d.polygon(star, fill=(255, 255, 255), outline=INK, width=22)
d.text((cx, cy + 250), "ENCORE", font=font(170, "Black"), fill=INK, anchor="mm")
icon.alpha_composite(pick.resize((S, S), Image.LANCZOS))
icon.convert("RGB").save(out / "channel-image.png")

# the banner: the stadium, a band on stage, the name (YouTube's safe area is the middle 1546x423)
W, H = 2560, 1440
ban = stage_bg(W, H)
d = ImageDraw.Draw(ban)
d.rectangle([0, 1000, W, H], fill=(16, 10, 30))
d.rectangle([0, 960, W, 1000], fill=(30, 30, 38))
looks = [({"hair": "long", "hair_color": "blond", "top": "tee", "top_color": "red", "play": "guitar", "guitar": "black"}, "guitar"),
         ({"hair": "mop", "hair_color": "brown", "glasses": "round", "top": "parka", "top_color": "khaki"}, "mic"),
         ({"hair": "spiky", "hair_color": "black", "top": "jacket", "top_color": "black", "pants": "black"}, "bass"),
         ({"hair": "curly", "hair_color": "dark", "beard": "full", "top": "shirt", "top_color": "white"}, "guitar")]
xs = [330, 640, 1920, 2230]  # around the words, which stay in the middle that every screen shows
for (look, play), x in zip(looks, xs):
    spr = bw.member(look, 0.6 + x / 1000, play, "grin" if play != "mic" else "smile", seed=x % 7)
    put(ban, spr.convert("RGBA"), (x, 960 - bw.SH * 0.78 / 2), scale=0.78)
t = Image.new("RGBA", (W * 2, 400), (0, 0, 0, 0)); td = ImageDraw.Draw(t)
td.text((W, 200), "ENCORE STORY", font=font(260, "Black"), fill=(255, 255, 255), anchor="mm", stroke_width=16, stroke_fill=INK)
put(ban, t.resize((W, 200), Image.LANCZOS), (W / 2, 640))
s = Image.new("RGBA", (W * 2, 160), (0, 0, 0, 0)); sd = ImageDraw.Draw(s)
sd.text((W, 80), "A BAND'S WHOLE STORY IN UNDER A MINUTE", font=font(96, "ExtraBold"), fill=GOLD, anchor="mm", stroke_width=8, stroke_fill=INK)
put(ban, s.resize((W, 80), Image.LANCZOS), (W / 2, 790))
ban.convert("RGB").save(out / "banner.png")
print("ok")
