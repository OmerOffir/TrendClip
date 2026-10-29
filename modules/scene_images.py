"""Pictures for a Dark Outliers Short: one 9:16 picture per beat (the hook and every scene).

Where they come from (DA_IMAGE_BACKEND in .env):
  auto         (default) `local` when an NVIDIA GPU and diffusers are there, else `gemini` when the key can
               make pictures, else `placeholder`. A backend that fails mid-way falls back to the next one.
  local        Stable Diffusion XL on the GPU through diffusers. DA_IMAGE_MODEL (default RealVisXL V4.0
               Lightning: photoreal, 6 steps, about 10-20 s a picture on an 8 GB card). The weights
               (about 7 GB) download from Hugging Face on the first picture.
  gemini       A Gemini image model (DA_GEMINI_IMAGE_MODEL, default gemini-2.5-flash-image). The free
               tier has no image quota: this needs a key with billing.
  placeholder  Dark gradient cards: tests, and machines without a GPU or a paid key.

Wikipedia stories also get real photos: `wikipedia_photos()` takes the article's own pictures from
Wikimedia Commons (with their author and licence for the credits) and crops them to fill the 9:16
frame. A photo too wide to crop (MAX_PHOTO_ASPECT) or made long before the story (an artist who died
centuries earlier) is left out and the scene keeps its drawn picture.
"""

from __future__ import annotations

import html
import io
import logging
import os
import random
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

SIZE = (1080, 1920)
# SDXL draws best around one megapixel; 832x1480 is 9:16 and divisible by 8.
SD_SIZE = (832, 1480)
DEFAULT_SD_MODEL = "SG161222/RealVisXL_V4.0_Lightning"
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"
SD_STEPS, SD_GUIDANCE = 6, 1.5  # the Lightning finetune: 4-6 steps at CFG 1-2
NEGATIVE_PROMPT = ("text, letters, caption, watermark, logo, signature, blurry, deformed, distorted, cartoon, anime, "
                   "illustration, painting, extra limbs, extra fingers, bad anatomy, celebrity, real person's face")
STYLE_SUFFIX = ", vertical 9:16 composition, cinematic, dark and moody, muted colours, film grain, photorealistic"
# Wikimedia wants a descriptive agent; a generic one ("python-requests") is answered with HTTP 429.
USER_AGENT = "TrendClip-DarkArchives/1.0 (+https://github.com/trendclip; dashboard)"
WIKI_API = "https://{lang}.wikipedia.org/w/api.php"
# Commons file names that are never a photo of the story: maps, flags, icons, logos, signatures, charts.
SKIP_FILE = re.compile(r"(icon|logo|flag|map|seal|coat[ _]of[ _]arms|emblem|symbol|wiki|commons|edit|ambox|question"
                       r"|crystal|nuvola|disambig|star|padlock|signature|chart|graph|diagram|locator|arms\b|stub)", re.I)
# ...and files whose Commons categories say so (a hurricane-tracks map is named after the hurricanes).
# ("trackmaps", "road maps"; not "photographs", "charter", "flagship")
SKIP_CATEGORY = re.compile(r"(\w*maps?\b|diagrams?|charts?\b|\btracks?\b|logos?\b|\bflags?\b|infographics?|drawings?\b|screenshots?|svg)", re.I)
SKIP_DESCRIPTION = re.compile(r"^\W*(this |a |an )?(map|diagram|chart|graph|drawing|illustration|plot)\b", re.I)
MIN_PHOTO_WIDTH = 500
MAX_PHOTO_ASPECT = 1.4  # width / height: a 4:3 photo cropped to 9:16 keeps 42% of its width; a wider one loses the subject
ERA_GAP = 60  # years: a picture whose artist's last year is this long before the story's is another era
Progress = Callable[[str], None]


class ImageError(RuntimeError):
    pass


@dataclass
class Picture:
    path: Path
    source: str  # local / gemini / placeholder / wikipedia
    credit: str = ""  # photos: "Photo: <author> (<licence>), Wikimedia Commons"
    prompt: str = ""


def setting(name: str, default: str = "") -> str:
    """DA_* settings: the environment first, then .env (like the fetcher's REDDIT_* keys)."""
    value = os.getenv(name)
    if value is None:
        from dotenv import dotenv_values

        from trendclip.config import PROJECT_ROOT

        value = dotenv_values(PROJECT_ROOT / ".env").get(name)
    return (value or "").strip() or default


# --------------------------------------------------------------------------- backends


def fit_cover(img, size: tuple[int, int] = SIZE):
    """Scale and centre-crop a PIL image to `size` (RGB)."""
    from PIL import ImageOps

    return ImageOps.fit(img.convert("RGB"), size, method=3, centering=(0.5, 0.45))


class Placeholder:
    """Dark gradient cards with a little texture; no network, no GPU."""

    name = "placeholder"

    def make(self, prompt: str, path: Path, seed: int, size: tuple[int, int] = SIZE) -> None:
        from PIL import Image, ImageDraw, ImageFilter

        rnd = random.Random(seed)
        hue = rnd.choice([(52, 46, 84), (30, 60, 84), (76, 34, 42), (40, 68, 56), (56, 56, 60)])
        w, h = size
        img = Image.new("RGB", (w, h), hue)
        draw = ImageDraw.Draw(img)
        for y in range(h):
            k = 1 - abs(y / h - 0.45) * 1.3
            draw.line([(0, y), (w, y)], fill=tuple(max(0, int(c * (0.55 + 0.9 * k))) for c in hue))
        for _ in range(6):  # soft lighter blobs, like fog in a torch beam
            x, y, r = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(w // 4, w // 2)
            glow = Image.new("RGB", (w, h), (0, 0, 0))
            ImageDraw.Draw(glow).ellipse([x - r, y - r, x + r, y + r], fill=tuple(min(255, c + 60) for c in hue))
            img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(r / 2)), 0.25)
        img.save(path)


class LocalSDXL:
    """Stable Diffusion XL (a Lightning finetune by default) through diffusers on the CUDA GPU."""

    name = "local"

    def __init__(self, model: str | None = None, steps: int = SD_STEPS, guidance: float = SD_GUIDANCE):
        self.model = model or setting("DA_IMAGE_MODEL", DEFAULT_SD_MODEL)
        self.steps, self.guidance = steps, guidance
        self._pipe = None

    @staticmethod
    def available() -> bool:
        try:
            import diffusers  # noqa: F401
            import torch
        except ImportError:
            return False
        return torch.cuda.is_available()

    def _load(self):
        if self._pipe is not None:
            return self._pipe
        import torch
        from diffusers import DPMSolverSinglestepScheduler, StableDiffusionXLPipeline

        logger.warning("Loading %s (the first run downloads about 7 GB)", self.model)
        kwargs = dict(torch_dtype=torch.float16, use_safetensors=True)
        try:
            pipe = StableDiffusionXLPipeline.from_pretrained(self.model, variant="fp16", **kwargs)
        except (OSError, ValueError, EnvironmentError):
            pipe = StableDiffusionXLPipeline.from_pretrained(self.model, **kwargs)
        try:  # DPM++ SDE Karras, what the Lightning finetunes are tuned for
            pipe.scheduler = DPMSolverSinglestepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)
        except Exception as err:  # noqa: BLE001 - keep the model's own scheduler
            logger.info("Keeping the model's scheduler: %s", err)
        vram = torch.cuda.get_device_properties(0).total_memory
        if vram < 12e9:  # an 8 GB card: keep only the working part of the model on the GPU
            pipe.enable_model_cpu_offload()
        else:
            pipe.to("cuda")
        vae = getattr(pipe, "vae", None)  # newer diffusers: pipe.vae.enable_slicing(); older: pipe.enable_vae_slicing()
        if hasattr(vae, "enable_slicing"):
            vae.enable_slicing()
        elif hasattr(pipe, "enable_vae_slicing"):
            pipe.enable_vae_slicing()
        pipe.set_progress_bar_config(disable=True)
        self._pipe = pipe
        return pipe

    def make(self, prompt: str, path: Path, seed: int, size: tuple[int, int] = SIZE) -> None:
        import torch

        pipe = self._load()
        generator = torch.Generator("cpu").manual_seed(seed)
        image = pipe(prompt=prompt + STYLE_SUFFIX, negative_prompt=NEGATIVE_PROMPT, width=SD_SIZE[0], height=SD_SIZE[1],
                     num_inference_steps=self.steps, guidance_scale=self.guidance, generator=generator).images[0]
        fit_cover(image, size).save(path)

    def close(self) -> None:
        if self._pipe is not None:
            import torch

            self._pipe = None
            torch.cuda.empty_cache()


class GeminiImages:
    """A Gemini image model (needs a key with billing: the free tier has no image quota)."""

    name = "gemini"

    def __init__(self, model: str | None = None):
        self.model = model or setting("DA_GEMINI_IMAGE_MODEL", DEFAULT_GEMINI_IMAGE_MODEL)
        self._client = None

    @staticmethod
    def available() -> bool:
        from trendclip import script_writer

        return bool(script_writer.gemini_api_key())

    def make(self, prompt: str, path: Path, seed: int, size: tuple[int, int] = SIZE) -> None:
        from google import genai
        from google.genai import errors, types
        from PIL import Image

        from trendclip import script_writer

        if self._client is None:
            self._client = genai.Client(api_key=script_writer.gemini_api_key())
        config = types.GenerateContentConfig(response_modalities=["IMAGE"],
                                             image_config=types.ImageConfig(aspect_ratio="9:16"))
        try:
            response = self._client.models.generate_content(model=self.model, contents=prompt + STYLE_SUFFIX + ". No text.",
                                                            config=config)
        except errors.APIError as err:
            raise ImageError(f"Gemini {self.model}: {str(err)[:200]}") from err
        parts = (response.candidates or [{}])[0].content.parts if response.candidates else []
        data = next((p.inline_data.data for p in parts or [] if getattr(p, "inline_data", None)), None)
        if not data:
            raise ImageError(f"Gemini {self.model} returned no picture")
        fit_cover(Image.open(io.BytesIO(data)), size).save(path)


BACKENDS = {"local": LocalSDXL, "gemini": GeminiImages, "placeholder": Placeholder}


def backend_order(choice: str | None = None) -> list[str]:
    """The backends to try, best first. `choice`: a name, or "auto" for what this machine can do."""
    choice = (choice or setting("DA_IMAGE_BACKEND", "auto")).lower()
    if choice in BACKENDS:
        return [choice] if choice == "placeholder" else [choice, "placeholder"]
    if choice != "auto":
        raise ImageError(f"DA_IMAGE_BACKEND must be auto, local, gemini or placeholder, not '{choice}'")
    order = []
    if LocalSDXL.available():
        order.append("local")
    if GeminiImages.available():
        order.append("gemini")
    return order + ["placeholder"]


def make_pictures(prompts: list[str], out_dir: Path, backend: str | None = None, seed: int = 7,
                  progress: Progress = lambda msg: None, size: tuple[int, int] = SIZE) -> tuple[list[Picture], str]:
    """One picture per prompt as out_dir/beat_<n>.jpg; returns (pictures, the backend that drew them).
    A backend that fails hands the remaining prompts to the next one (the note says so)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    order = backend_order(backend)
    pictures: list[Picture] = []
    notes: list[str] = []
    while order:
        name = order.pop(0)
        drawer = BACKENDS[name]()
        try:
            for i in range(len(pictures), len(prompts)):
                progress(f"Drawing picture {i + 1} of {len(prompts)} ({name})")
                path = out_dir / f"beat_{i + 1}.jpg"
                drawer.make(prompts[i], path, seed + i, size)
                pictures.append(Picture(path, name, prompt=prompts[i]))
            break
        except Exception as err:  # noqa: BLE001 - try the next backend
            if not order:
                raise ImageError(f"{name} could not draw the pictures: {err}") from err
            logger.warning("%s failed after %d pictures: %s; using %s", name, len(pictures), err, order[0])
            notes.append(f"{name}: {str(err)[:120]}")
        finally:
            if hasattr(drawer, "close"):
                drawer.close()
    used = sorted({p.source for p in pictures}, key=lambda s: ["local", "gemini", "placeholder"].index(s))
    return pictures, " + ".join(used) + (f" ({'; '.join(notes)})" if notes else "")


# --------------------------------------------------------------------------- the grade


def grade(img, saturation: float = 0.85, contrast: float = 1.14, brightness: float = 1.06, cold: float = 0.04,
          vignette: float = 0.38):
    """The Dark Outliers look on a still: dark but not a blur. The levels are stretched first (the darkest and
    brightest 1% become black and white, so a murky picture gets its bright focal point back), then less colour,
    more contrast, a cold tint and a light vignette. Done once per picture here (Pillow) instead of on every
    frame in ffmpeg, which is far slower."""
    from PIL import Image, ImageEnhance, ImageOps

    img = ImageOps.autocontrast(img.convert("RGB"), cutoff=1)
    img = ImageEnhance.Color(img).enhance(saturation)
    img = ImageEnhance.Contrast(img).enhance(contrast)
    img = ImageEnhance.Brightness(img).enhance(brightness)
    r, g, b = img.split()
    r = r.point(lambda v: int(v * (1 - cold)))
    b = b.point(lambda v: min(255, int(v * (1 + cold))))
    img = Image.merge("RGB", (r, g, b))
    mask = Image.radial_gradient("L").resize(img.size, Image.BILINEAR).point(lambda v: int(v * vignette))
    return Image.composite(Image.new("RGB", img.size, (0, 0, 0)), img, mask)


def graded_copy(path: Path, out: Path | None = None) -> Path:
    from PIL import Image

    out = out or path.with_name(f"graded_{path.name}")
    grade(Image.open(path)).save(out, quality=94)
    return out


# --------------------------------------------------------------------------- Wikipedia photos


@dataclass
class CommonsFile:
    title: str
    url: str  # a thumbnail wide enough for the frame
    width: int
    height: int
    artist: str = ""
    licence: str = ""
    page: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def credit(self) -> str:
        who = f" {self.artist}" if self.artist else ""
        lic = f" ({self.licence})" if self.licence else ""
        return f"Photo:{who}{lic}, Wikimedia Commons".replace("Photo:,", "Photo:")


def article_title(url: str) -> tuple[str, str] | None:
    """("en", "Mary Celeste") from a Wikipedia article URL, None for anything else."""
    m = re.match(r"https?://([a-z]{2,3})(?:\.m)?\.wikipedia\.org/wiki/([^#?]+)", url or "")
    if not m:
        return None
    return m.group(1), urllib.parse.unquote(m.group(2)).replace("_", " ")


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def wrong_era(artist: str, year: int | None) -> bool:
    """A picture by someone whose dates all end ERA_GAP years before the story ("Hartmann Schedel
    (1440-1514)" for a 1980s story). No dates or no story year: not judged."""
    years = [int(y) for y in re.findall(r"\b(1[0-9]{3}|20[0-9]{2})\b", artist or "")]
    return bool(year and years) and max(years) < year - ERA_GAP


def pick_commons_files(pages: list[dict], limit: int = 2, year: int | None = None) -> list[CommonsFile]:
    """The usable photos out of an imageinfo query: JPEG/PNG, big enough, not wider than MAX_PHOTO_ASPECT
    (it fills the 9:16 frame), not a map/flag/icon, not from another era than the story (`year`), biggest first."""
    out = []
    for page in pages:
        title = page.get("title", "")
        info = (page.get("imageinfo") or [{}])[0]
        if not info or SKIP_FILE.search(title.removeprefix("File:")):
            continue
        if info.get("mime") not in ("image/jpeg", "image/png", "image/tiff"):
            continue
        width, height = int(info.get("width") or 0), int(info.get("height") or 0)
        if width < MIN_PHOTO_WIDTH or height < 300 or width / height > MAX_PHOTO_ASPECT:
            continue
        meta = info.get("extmetadata") or {}
        if SKIP_CATEGORY.search(_strip_html((meta.get("Categories") or {}).get("value", ""))):
            continue
        if SKIP_DESCRIPTION.search(_strip_html((meta.get("ImageDescription") or {}).get("value", ""))):
            continue
        artist = _strip_html((meta.get("Artist") or {}).get("value", ""))[:80]
        if wrong_era(artist, year):
            continue
        out.append(CommonsFile(
            title=title, url=info.get("thumburl") or info.get("url", ""), width=width, height=height,
            artist=artist,
            licence=_strip_html((meta.get("LicenseShortName") or {}).get("value", ""))[:40],
            page=info.get("descriptionurl", ""),
        ))
    out.sort(key=lambda f: f.width * f.height, reverse=True)
    return out[:limit]


def commons_files(url: str, limit: int = 2, timeout: float = 20, year: int | None = None) -> list[CommonsFile]:
    """The best `limit` photos of the Wikipedia article at `url` (empty for other sources)."""
    import requests

    found = article_title(url)
    if not found:
        return []
    lang, title = found
    api = WIKI_API.format(lang=lang)
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(api, params={"action": "query", "prop": "images", "titles": title, "imlimit": 50,
                                  "redirects": 1, "format": "json"}, headers=headers, timeout=timeout)
    r.raise_for_status()
    names = [im["title"] for p in r.json().get("query", {}).get("pages", {}).values() for im in p.get("images", [])]
    names = [n for n in names if not SKIP_FILE.search(n.removeprefix("File:")) and not n.lower().endswith((".svg", ".gif"))]
    if not names:
        return []
    r = requests.get(api, params={"action": "query", "prop": "imageinfo", "titles": "|".join(names[:40]),
                                  "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 2600,
                                  "iiextmetadatafilter": "Artist|LicenseShortName|Categories|ImageDescription", "format": "json"},
                     headers=headers, timeout=timeout)
    r.raise_for_status()
    return pick_commons_files(list(r.json().get("query", {}).get("pages", {}).values()), limit, year)


def frame_photo(photo, size: tuple[int, int] = SIZE):
    """A photo cropped to fill the 9:16 frame edge to edge (no bars: a small photo on a blurred band looked
    like a slideshow), a little above the middle, where the subject usually is."""
    from PIL import ImageOps

    return ImageOps.fit(ImageOps.exif_transpose(photo).convert("RGB"), size, method=3, centering=(0.5, 0.42))


def wikipedia_photos(url: str, out_dir: Path, limit: int = 2, timeout: float = 20, year: int | None = None) -> list[Picture]:
    """Download the article's best photos, cropped 9:16, as out_dir/photo_<n>.jpg; `year`: when the story
    happened (pick_commons_files). Never raises: a story without usable photos (or no network) gives []."""
    import requests
    from PIL import Image

    try:
        files = commons_files(url, limit, timeout, year)
    except Exception as err:  # noqa: BLE001 - photos are a bonus
        logger.warning("Wikipedia photos for %s: %s", url, err)
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    pictures = []
    for i, f in enumerate(files, 1):
        try:
            r = requests.get(f.url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            r.raise_for_status()
            path = out_dir / f"photo_{i}.jpg"
            frame_photo(Image.open(io.BytesIO(r.content))).save(path, quality=92)
            pictures.append(Picture(path, "wikipedia", credit=f.credit, prompt=f.title))
        except Exception as err:  # noqa: BLE001
            logger.warning("Photo %s: %s", f.title, err)
    return pictures
