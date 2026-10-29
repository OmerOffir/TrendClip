"""Borderline Why's animation library: short 2D clips generated once on the hub's GPU and reused by the videos.

A clip belongs to a tag (war, treaty, trade, river, railway, ...), so the renderer can take any clip of the tag a
scene is about. Each clip is an mp4 next to a JSON sidecar with the model, tag, prompt, size, frame rate and how
long it took, in assets/bw_anim/<set>/. Two models, both open weights and small enough for the RTX 2060 Super's
8 GB: AnimateDiff on Stable Diffusion 1.5 (fast, classic 2D loop) and Wan 2.1 1.3B (slower, better motion).

The pilot (`python -m modules.bw_anim pilot`) makes one clip per tag per model into assets/bw_anim/pilot/ and
keeps its progress in pilot.json, which the dashboard's Animations tab shows while it runs. Torch and diffusers are
imported only when a clip is generated, so the module and the dashboard work on a machine without them.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from modules import bw_geo

logger = logging.getLogger(__name__)

ROOT = bw_geo.PROJECT_ROOT / "assets" / "bw_anim"
PILOT = "pilot"

# One style for the whole channel, so the clips look like one hand drew them; the renderer's palette is old paper
# and earthy inks, and the clips keep to it.
STYLE = ("flat 2D vector animation, paper cut-out style, simple clean shapes, muted earthy palette of ochre, "
         "rust red, deep teal and cream, soft paper texture, smooth looping motion, no text, no letters")
NEGATIVE = ("text, letters, words, watermark, logo, signature, blurry, photo, photorealistic, 3d render, "
            "realistic, noisy, deformed, extra limbs, low quality, jpeg artifacts")

# What each tag shows (the subject; STYLE is added). A scene about a war takes a "war" clip, and so on.
TAGS: dict[str, str] = {
    "war": "a column of toy-like soldiers marching across a hill with flags, side view",
    "treaty": "two hands shaking over an old parchment document with a quill and a wax seal, top view",
    "trade": "a small wooden sailing ship with cargo crossing calm waves, side view, seagulls",
    "river": "a winding river flowing through a green valley between hills, gentle current, top view",
    "railway": "a steam train crossing a stone bridge over a gorge, side view, puff of smoke",
}

MODELS: dict[str, dict[str, Any]] = {
    "animatediff": {"label": "AnimateDiff + SD 1.5 (DreamShaper 8)", "base": "Lykon/dreamshaper-8",
                    "adapter": "guoyww/animatediff-motion-adapter-v1-5-3", "frames": 16, "fps": 8, "out_fps": 24,
                    "width": 512, "height": 512, "steps": 25, "guidance": 7.5},
    "wan": {"label": "Wan 2.1 1.3B", "id": "Wan-AI/Wan2.1-T2V-1.3B-Diffusers", "frames": 33, "fps": 16, "out_fps": 16,
            "width": 832, "height": 480, "steps": 30, "guidance": 5.0, "flow_shift": 3.0},
}


@dataclass
class Clip:
    name: str  # file stem: <model>_<tag>_<seed>
    model: str
    tag: str
    prompt: str
    seed: int
    frames: int
    fps: int
    width: int
    height: int
    seconds: float  # the clip's length
    took: float  # generation time in seconds
    made_at: str
    device: str = ""
    note: str = ""


def prompt_for(tag: str) -> str:
    return f"{TAGS[tag]}, {STYLE}"


def set_dir(name: str = PILOT) -> Path:
    return ROOT / name


def clips(name: str = PILOT) -> list[dict[str, Any]]:
    """The clips of a set, with their sidecars, newest last; a clip without its mp4 is left out."""
    out = []
    folder = set_dir(name)
    if not folder.is_dir():
        return out
    for side in sorted(folder.glob("*.json")):
        if side.name == "pilot.json" or not side.with_suffix(".mp4").exists():
            continue
        try:
            out.append(json.loads(side.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return out


def clip_path(name: str, clip: str) -> Path | None:
    """The mp4 of a clip of a set, None when it is not one of the set's clips."""
    if "/" in clip or "\\" in clip or ".." in clip:
        return None
    path = set_dir(name) / f"{clip}.mp4"
    return path if path.exists() else None


# --------------------------------------------------------------------------- generation

def _device_name() -> str:
    import torch

    return torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"


def _save_video(frames, path: Path, fps: int, out_fps: int) -> None:
    """Frames (PIL images or float arrays 0..1 / uint8 arrays, F x H x W x 3) to an mp4 through ffmpeg; the clip is
    smoothed to out_fps with motion interpolation when its native rate is lower."""
    import numpy as np

    arrays = []
    for f in frames:
        a = np.asarray(f)
        if a.dtype != np.uint8:
            a = (np.clip(a, 0, 1) * 255).round().astype(np.uint8)
        arrays.append(a)
    h, w = arrays[0].shape[:2]
    filters = f"minterpolate=fps={out_fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1" if out_fps > fps else "null"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
           "-i", "-", "-vf", f"{filters},format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-movflags", "+faststart", str(path)]
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(cmd, input=b"".join(a.tobytes() for a in arrays), capture_output=True)
    if proc.returncode:
        raise RuntimeError(f"ffmpeg: {proc.stderr.decode(errors='replace')[-400:]}")


def _animatediff(prompt: str, seed: int, cfg: dict[str, Any], progress: Callable[[str], None]):
    import torch
    from diffusers import AnimateDiffPipeline, DDIMScheduler, MotionAdapter

    progress("loading AnimateDiff")
    adapter = MotionAdapter.from_pretrained(cfg["adapter"], torch_dtype=torch.float16, variant="fp16")
    pipe = AnimateDiffPipeline.from_pretrained(cfg["base"], motion_adapter=adapter, torch_dtype=torch.float16,
                                               variant="fp16", safety_checker=None)
    pipe.scheduler = DDIMScheduler.from_pretrained(cfg["base"], subfolder="scheduler", clip_sample=False,
                                                   timestep_spacing="linspace", beta_schedule="linear", steps_offset=1)
    pipe.enable_vae_slicing()
    pipe.enable_model_cpu_offload()
    progress("generating")
    out = pipe(prompt=prompt, negative_prompt=NEGATIVE, num_frames=cfg["frames"], width=cfg["width"], height=cfg["height"],
               guidance_scale=cfg["guidance"], num_inference_steps=cfg["steps"],
               generator=torch.Generator("cpu").manual_seed(seed))
    return out.frames[0]


def _wan(prompt: str, seed: int, cfg: dict[str, Any], progress: Callable[[str], None]):
    """Wan 2.1 1.3B on 8 GB: the text encoder (11 GB) runs on the CPU in bfloat16 before the pipeline is offloaded
    to the GPU, the transformer runs in float16 (the RTX 2060 Super has no bfloat16), the VAE in float32."""
    import torch
    from diffusers import AutoencoderKLWan, UniPCMultistepScheduler, WanPipeline

    progress("loading Wan 2.1 (text encoder on the CPU)")
    vae = AutoencoderKLWan.from_pretrained(cfg["id"], subfolder="vae", torch_dtype=torch.float32)
    pipe = WanPipeline.from_pretrained(cfg["id"], vae=vae, torch_dtype=torch.float16)
    pipe.text_encoder.to(torch.bfloat16)
    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=cfg["flow_shift"])
    progress("encoding the prompt on the CPU")
    with torch.no_grad():
        embeds, neg_embeds = pipe.encode_prompt(prompt=prompt, negative_prompt=NEGATIVE, do_classifier_free_guidance=True,
                                                device=torch.device("cpu"), dtype=torch.bfloat16)
    embeds, neg_embeds = embeds.to(torch.float16), neg_embeds.to(torch.float16)
    pipe.text_encoder = None  # never needed again; keeps the 11 GB off the GPU
    if hasattr(pipe.vae, "enable_tiling"):
        pipe.vae.enable_tiling()
    pipe.enable_model_cpu_offload()
    progress("generating")
    out = pipe(prompt_embeds=embeds.to("cuda"), negative_prompt_embeds=neg_embeds.to("cuda"), num_frames=cfg["frames"],
               width=cfg["width"], height=cfg["height"], guidance_scale=cfg["guidance"], num_inference_steps=cfg["steps"],
               generator=torch.Generator("cpu").manual_seed(seed), output_type="np")
    return out.frames[0]


GENERATORS = {"animatediff": _animatediff, "wan": _wan}


def generate(model: str, tag: str, seed: int = 1, name: str = PILOT, progress: Callable[[str], None] = lambda m: None,
             overrides: dict[str, Any] | None = None) -> Clip:
    """One clip of a tag with a model into the set's folder; its sidecar is written next to it. Takes minutes."""
    cfg = {**MODELS[model], **(overrides or {})}
    prompt = prompt_for(tag)
    stem = f"{model}_{tag}_{seed}"
    folder = set_dir(name)
    folder.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    frames = GENERATORS[model](prompt, seed, cfg, progress)
    progress("encoding the mp4")
    _save_video(frames, folder / f"{stem}.mp4", cfg["fps"], cfg["out_fps"])
    clip = Clip(name=stem, model=model, tag=tag, prompt=prompt, seed=seed, frames=len(frames), fps=cfg["out_fps"],
                width=cfg["width"], height=cfg["height"], seconds=round(len(frames) / cfg["fps"], 2),
                took=round(time.monotonic() - started, 1), made_at=datetime.now().isoformat(timespec="seconds"),
                device=_device_name(), note=f"{cfg['steps']} steps, native {cfg['fps']} fps")
    (folder / f"{stem}.json").write_text(json.dumps(asdict(clip), indent=2), encoding="utf-8")
    return clip


# --------------------------------------------------------------------------- the pilot

def pilot_path(name: str = PILOT) -> Path:
    return set_dir(name) / "pilot.json"


def pilot_status(name: str = PILOT) -> dict[str, Any]:
    """The pilot's progress: {running, started, finished, done, total, current, step, failed: [{clip, error}]}."""
    path = pilot_path(name)
    if not path.exists():
        return {"running": False, "started": "", "finished": "", "done": 0, "total": 0, "current": "", "step": "", "failed": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {"running": False, "started": "", "finished": "", "done": 0, "total": 0, "current": "", "step": "",
                "failed": [{"clip": "pilot.json", "error": "unreadable"}]}


def run_pilot(models: list[str] | None = None, tags: list[str] | None = None, seed: int = 1, name: str = PILOT,
              overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """One clip per tag per model, a model's clips together (its weights load once per clip but stay in the OS cache);
    a clip that fails is recorded and the rest go on. Progress in pilot.json."""
    models = models or list(MODELS)
    tags = tags or list(TAGS)
    todo = [(m, t) for m in models for t in tags]
    status = {"running": True, "started": datetime.now().isoformat(timespec="seconds"), "finished": "", "done": 0,
              "total": len(todo), "current": "", "step": "", "failed": []}
    path = pilot_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)

    def save():
        path.write_text(json.dumps(status, indent=2), encoding="utf-8")

    for model, tag in todo:
        status["current"] = f"{model} {tag}"
        if clip_path(name, f"{model}_{tag}_{seed}"):
            status["done"] += 1
            status["step"] = "already there"
            save()
            continue

        def progress(step, _m=model, _t=tag):
            status["step"] = step
            save()
            logger.info("pilot %s %s: %s", _m, _t, step)

        try:
            clip = generate(model, tag, seed, name, progress, overrides)
            logger.info("pilot %s %s: done in %.0f s", model, tag, clip.took)
        except Exception as err:  # noqa: BLE001 - one clip's trouble never stops the pilot
            logger.exception("pilot %s %s failed", model, tag)
            status["failed"].append({"clip": f"{model}_{tag}_{seed}", "error": str(err)[-300:]})
        finally:
            import gc

            gc.collect()
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
        status["done"] += 1
        save()
    status.update(running=False, current="", step="", finished=datetime.now().isoformat(timespec="seconds"))
    save()
    return status


def main(argv: list[str]) -> int:
    """python -m modules.bw_anim pilot [--models wan,animatediff] [--tags war,treaty] [--seed N] [--steps N]"""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pilot", help="one clip per tag per model into assets/bw_anim/pilot/")
    p.add_argument("--models", default=",".join(MODELS))
    p.add_argument("--tags", default=",".join(TAGS))
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--steps", type=int, default=None, help="override the inference steps (a quick smoke test)")
    p.add_argument("--frames", type=int, default=None)
    p.add_argument("--set", default=PILOT, dest="name")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    overrides = {k: v for k, v in (("steps", args.steps), ("frames", args.frames)) if v is not None}
    status = run_pilot(args.models.split(","), args.tags.split(","), args.seed, args.name, overrides)
    print(json.dumps(status, indent=2))
    return 1 if status["failed"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
