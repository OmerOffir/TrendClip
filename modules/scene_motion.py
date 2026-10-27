"""The depth camera: every still becomes a short clip the camera moves through.

A depth model (Depth Anything V2, small: 25M parameters, under a second per picture on the GPU) says how
far every pixel is; the camera then pushes in or pulls out and drifts sideways, and the near pixels move
more than the far ones (parallax), so a flat picture reads as a scene with depth. Over it: fog that
drifts and hangs in the far parts of the picture, and a little dust floating up through the light.
Each clip is rendered with torch (the GPU when there is one) and written by ffmpeg; modules.video_gen
plays the clips instead of its zoom-and-pan over the stills.

Settings (.env): DA_MOTION=depth (default) or off; DA_PARALLAX=0.05 (how far the near pixels move, as a
share of the width); DA_FOG=0.12 (0 = none); DA_DUST=1 (0 = none).
"""

from __future__ import annotations

import logging
import math
import subprocess
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"
DEPTH_SIZE = 518  # the model's input; the map is scaled up to the picture
PARALLAX = 0.05
FOG = 0.12
ZOOM = (1.14, 1.26)  # the camera's window: never below 1.14 so the drift and the parallax never show an edge
PAN = 0.035  # sideways drift over a clip (share of the width): a slow dolly, right on one clip, left on the next
BATCH = 8  # frames rendered at once
DUST_COUNT = 45
FOG_COLOR = (0.62, 0.68, 0.76)  # a cold grey-blue

_model: Any = None
_processor: Any = None


def enabled() -> bool:
    from modules.scene_images import setting

    return setting("DA_MOTION", "depth").strip().lower() not in ("0", "off", "none", "false", "still")


def _setting_float(name: str, default: float) -> float:
    from modules.scene_images import setting

    try:
        return float(setting(name, str(default)))
    except ValueError:
        return default


def available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


def _device():
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _load():
    global _model, _processor
    if _model is None:
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        logger.info("Loading the depth model (%s)", DEPTH_MODEL)
        _processor = AutoImageProcessor.from_pretrained(DEPTH_MODEL)
        _model = AutoModelForDepthEstimation.from_pretrained(DEPTH_MODEL).to(_device()).eval()
    return _processor, _model


def unload() -> None:
    global _model, _processor
    _model = _processor = None
    try:
        import torch

        torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


def depth_map(image) -> "Any":
    """Depth of every pixel of a PIL image as a float tensor (H, W) in 0..1, 1 = nearest."""
    import torch

    processor, model = _load()
    inputs = processor(images=image.convert("RGB"), return_tensors="pt").to(model.device)
    with torch.no_grad():
        predicted = model(**inputs).predicted_depth  # (1, h, w), larger = nearer
    depth = torch.nn.functional.interpolate(predicted.unsqueeze(1).float(), size=(image.height, image.width),
                                            mode="bicubic", align_corners=False)[0, 0]
    return normalize_depth(depth)


def normalize_depth(depth) -> "Any":
    """0..1 between the 2nd and 98th percentile (a stray far or near pixel never flattens the rest)."""
    import torch

    flat = depth.flatten()
    k = max(int(flat.numel() * 0.02), 1)
    lo, hi = torch.kthvalue(flat, k).values, torch.kthvalue(flat, flat.numel() - k + 1).values
    return ((depth - lo) / torch.clamp(hi - lo, min=1e-6)).clamp(0, 1)


def smooth(depth, radius: int = 9):
    """A blurred depth map: tearing at a hard edge (hair against sky) is uglier than a soft one."""
    import torch

    k = radius * 2 + 1
    return torch.nn.functional.avg_pool2d(depth[None, None], k, stride=1, padding=radius, count_include_pad=False)[0, 0]


def ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def camera(index: int, frames: int, parallax: float, pan: float = PAN, zoom: tuple[float, float] = ZOOM):
    """Per-frame (zoom, pan_x, pan_y, parallax_x, parallax_y): every clip pushes in (the owner's choice,
    2026-10-27: no pull-outs) and drifts sideways, right on even clips and left on odd ones; the parallax
    follows the drift, with a slow vertical sway."""
    out = []
    side = 1.0 if index % 2 == 0 else -1.0
    for f in range(frames):
        t = f / max(frames - 1, 1)
        e = ease(t)
        z = zoom[0] + (zoom[1] - zoom[0]) * e
        px = side * pan * (e - 0.5) * 2
        py = 0.3 * pan * math.sin(math.pi * t)
        out.append((z, px, py, side * parallax * (e - 0.5) * 2, parallax * 0.35 * math.sin(math.pi * t)))
    return out


def warp(image, depth, moves: list[tuple[float, float, float, float, float]]):
    """`image` (3, H, W) float 0..1 and `depth` (H, W) 0..1 → (n, 3, H, W): the camera's view for every
    move. Each output pixel samples the picture at its place scaled by the zoom, shifted by the pan and
    pushed by its depth (near pixels move more)."""
    import torch

    _, h, w = image.shape
    n = len(moves)
    ys, xs = torch.meshgrid(torch.linspace(-1, 1, h, device=image.device), torch.linspace(-1, 1, w, device=image.device),
                            indexing="ij")
    centred = depth - 0.5  # -0.5 far … +0.5 near
    grids = []
    for z, px, py, dx, dy in moves:
        gx = xs / z + px * 2 + centred * dx * 2
        gy = ys / z + py * 2 * (w / h) + centred * dy * 2 * (w / h)
        grids.append(torch.stack((gx, gy), dim=-1))
    grid = torch.stack(grids)
    return torch.nn.functional.grid_sample(image.expand(n, -1, -1, -1), grid, mode="bilinear", padding_mode="border",
                                           align_corners=True)


class Fog:
    """Two layers of soft noise, each a little wider and taller than the frame, that slide across it at
    different speeds; the density is thicker where the picture is far away."""

    def __init__(self, h: int, w: int, depth, device, seed: int):
        import torch

        gen = torch.Generator(device="cpu").manual_seed(seed)
        self.h, self.w = h, w
        self.far = (1 - depth) ** 1.5
        self.a = self._field(torch.rand(1, 1, 14, 12, generator=gen), h, w + w // 2, device)
        self.b = self._field(torch.rand(1, 1, 36, 30, generator=gen), h + h // 2, w + w // 2, device)

    @staticmethod
    def _field(noise, h: int, w: int, device):
        import torch

        field = torch.nn.functional.interpolate(noise.to(device), size=(h, w), mode="bicubic", align_corners=False)[0, 0]
        return field.clamp(0, 1)

    def density(self, t: float):
        """(H, W) fog density at time t in 0..1 of the clip."""
        ax = int(t * (self.w // 2))
        bx, by = int(t * (self.w // 2) * 0.55), int(t * (self.h // 2) * 0.8)
        a = self.a[:, ax:ax + self.w]
        b = self.b[by:by + self.h, bx:bx + self.w]
        return ((0.65 * a + 0.35 * b) * self.far).clamp(0, 1)


def fog_layer(frames: list[int], total: int, fog: "Fog"):
    """(n, 1, H, W) fog density for the frame numbers."""
    import torch

    return torch.stack([fog.density(f / max(total, 1)) for f in frames])[:, None]


def dust_layer(frames: list[int], fps: int, h: int, w: int, device, seed: int, count: int = DUST_COUNT):
    """(n, 1, H, W) brightness of little motes drifting up and sideways, drawn at quarter size."""
    import torch

    gen = torch.Generator(device="cpu").manual_seed(seed + 1)
    sh, sw = h // 4, w // 4
    x0 = torch.rand(count, generator=gen) * sw
    y0 = torch.rand(count, generator=gen) * sh
    vx = (torch.rand(count, generator=gen) - 0.5) * 6 / fps  # pixels per frame at quarter size
    vy = -(torch.rand(count, generator=gen) * 5 + 2) / fps
    size = (torch.rand(count, generator=gen) * 1.3 + 0.7)
    bright = (torch.rand(count, generator=gen) * 0.5 + 0.3)
    phase = torch.rand(count, generator=gen) * 6.28
    x0, y0, vx, vy, size, bright, phase = (t.to(device) for t in (x0, y0, vx, vy, size, bright, phase))
    ys, xs = torch.meshgrid(torch.arange(sh, device=device, dtype=torch.float32),
                            torch.arange(sw, device=device, dtype=torch.float32), indexing="ij")
    out = []
    for f in frames:
        px = (x0 + vx * f + 3 * torch.sin(phase + f / fps * 0.7)) % sw
        py = (y0 + vy * f) % sh
        twinkle = 0.6 + 0.4 * torch.sin(phase * 2 + f / fps * 2.1)
        d2 = (xs[None] - px[:, None, None]) ** 2 + (ys[None] - py[:, None, None]) ** 2
        motes = (bright * twinkle)[:, None, None] * torch.exp(-d2 / (2 * size[:, None, None] ** 2))
        layer = motes.sum(0).clamp(0, 1)[None, None]
        out.append(torch.nn.functional.interpolate(layer, size=(h, w), mode="bilinear", align_corners=False)[0])
    return torch.stack(out)


def render_frames(image, depth, index: int, frames: int, fps: int, parallax: float, fog: float, dust: bool, seed: int,
                  batch: int = BATCH):
    """Yields uint8 (n, H, W, 3) numpy batches of the finished clip."""
    import torch

    _, h, w = image.shape
    moves = camera(index, frames, parallax)
    fog_rgb = torch.tensor(FOG_COLOR, device=image.device).view(1, 3, 1, 1)
    field = Fog(h, w, depth, image.device, seed) if fog > 0 else None
    for start in range(0, frames, batch):
        chunk = list(range(start, min(start + batch, frames)))
        out = warp(image, depth, [moves[f] for f in chunk])
        if field is not None:
            density = fog_layer(chunk, frames, field) * fog
            out = out * (1 - density) + fog_rgb * density  # the fog hangs in front of the far parts
        if dust:
            motes = dust_layer(chunk, fps, h, w, image.device, seed) * 0.4
            out = out + motes * (1 - out) * 0.9  # screen-like: the motes glow, they never go grey
        yield (out.clamp(0, 1).permute(0, 2, 3, 1) * 255).round().to(torch.uint8).cpu().numpy()


def animate(picture: Path, seconds: float, output: Path, index: int = 0, fps: int = 30, size: tuple[int, int] | None = None,
            parallax: float | None = None, fog: float | None = None, dust: bool | None = None, seed: int = 0,
            depth=None, crf: int = 16, preset: str = "veryfast") -> Path:
    """`picture` → `output` (.mp4, `seconds` long, no audio) with the depth camera over it. `size`: the
    clip's (w, h); the picture is scaled and cropped to it (default: the picture's size). `depth`: a ready
    map (tests); otherwise the model's."""
    import numpy as np
    import torch
    from PIL import Image

    from trendclip.video_assembler import ffmpeg_with_libass

    parallax = _setting_float("DA_PARALLAX", PARALLAX) if parallax is None else parallax
    fog = _setting_float("DA_FOG", FOG) if fog is None else fog
    dust = _setting_float("DA_DUST", 1) > 0 if dust is None else dust
    img = Image.open(picture).convert("RGB")
    if size and img.size != tuple(size):
        from PIL import ImageOps

        img = ImageOps.fit(img, size, Image.LANCZOS)
    w, h = img.size
    device = _device()
    if depth is None:
        depth = depth_map(img)
    depth = smooth(torch.as_tensor(depth, dtype=torch.float32).to(device))
    image = torch.from_numpy(np.asarray(img, dtype=np.float32) / 255).permute(2, 0, 1).to(device)
    frames = max(int(round(seconds * fps)), 2)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.stem + ".rendering.mp4")
    cmd = [ffmpeg_with_libass(), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp.resolve())]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for chunk in render_frames(image, depth, index, frames, fps, parallax, fog, dust, seed):
            proc.stdin.write(chunk.tobytes())
    except BrokenPipeError:
        pass
    finally:
        proc.stdin.close()
    err = proc.stderr.read().decode(errors="replace")
    proc.wait()
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed on the moving picture: {err.strip()[-300:]}")
    tmp.replace(output)
    return output


def animate_all(pictures: list[Path], seconds: list[float], out_dir: Path, fps: int = 30, size: tuple[int, int] | None = None,
                seed: int = 0, progress: Callable[[float, str], None] = lambda f, m: None) -> list[Path]:
    """A clip per picture (clip_<n>.mp4 in out_dir), each `seconds[i]` long; the depth model is loaded
    once and freed at the end."""
    import torch

    from PIL import Image

    out: list[Path] = []
    try:
        maps = []
        for i, pic in enumerate(pictures):
            progress(i / max(len(pictures), 1) * 0.2, f"Measuring the depth of picture {i + 1} of {len(pictures)}")
            img = Image.open(pic).convert("RGB")
            if size and img.size != tuple(size):
                from PIL import ImageOps

                img = ImageOps.fit(img, size, Image.LANCZOS)
            maps.append(depth_map(img).cpu())
        unload()  # the warp needs the memory more than the model
        for i, (pic, secs, depth) in enumerate(zip(pictures, seconds, maps)):
            progress(0.2 + 0.8 * i / max(len(pictures), 1), f"Moving the camera through picture {i + 1} of {len(pictures)}")
            out.append(animate(pic, secs, out_dir / f"clip_{i + 1}.mp4", index=i, fps=fps, size=size, seed=seed + i, depth=depth))
    finally:
        unload()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    return out


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="The depth camera over one picture")
    parser.add_argument("picture", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--index", type=int, default=0, help="even: push in, odd: pull out")
    parser.add_argument("--size", default="1080x1920")
    parser.add_argument("--parallax", type=float)
    parser.add_argument("--fog", type=float)
    parser.add_argument("--no-dust", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    w, h = (int(v) for v in args.size.lower().split("x"))
    animate(args.picture, args.seconds, args.output, index=args.index, size=(w, h), parallax=args.parallax, fog=args.fog,
            dust=False if args.no_dust else None)
    print(args.output)


if __name__ == "__main__":
    main()
