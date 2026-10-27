"""The depth camera: camera paths, depth normalising, a rendered clip, and video_gen's fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from modules import scene_motion, video_gen

torch = pytest.importorskip("torch")


def test_camera_pushes_in_and_drifts_each_way():
    right = scene_motion.camera(0, 31, parallax=0.05)
    left = scene_motion.camera(1, 31, parallax=0.05)
    for clip in (right, left):  # every clip pushes in
        assert clip[0][0] == scene_motion.ZOOM[0] and clip[-1][0] == pytest.approx(scene_motion.ZOOM[1])
    assert right[0][1] == -scene_motion.PAN and right[-1][1] == pytest.approx(scene_motion.PAN)  # drifts one way
    assert left[0][1] == scene_motion.PAN and left[-1][1] == pytest.approx(-scene_motion.PAN)  # and the next the other
    assert right[0][3] == -0.05 and right[-1][3] == pytest.approx(0.05)  # the parallax sweeps with the drift
    assert abs(right[15][4]) <= 0.05 * 0.35 + 1e-9 and right[15][1] == pytest.approx(0.0, abs=1e-9)
    # the window never shows an edge: zoom margin covers the drift plus the near pixels' parallax
    assert 1 - 1 / scene_motion.ZOOM[0] >= 2 * scene_motion.PAN + scene_motion.PARALLAX


def test_normalize_depth_ignores_stray_pixels():
    depth = torch.linspace(0, 1, 10000).reshape(100, 100)
    depth[0, 0], depth[99, 99] = -50.0, 50.0
    out = scene_motion.normalize_depth(depth)
    assert out.min() == 0 and out.max() == 1 and 0.45 < out[50, 50] < 0.55


def _still(path: Path, w=144, h=256):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), (30, 34, 40))
    d = ImageDraw.Draw(img)
    d.rectangle((0, h // 2, w, h), fill=(90, 70, 50))
    d.ellipse((w // 3, h // 4, 2 * w // 3, h // 2), fill=(200, 200, 210))
    img.save(path)
    return path


def test_animate_renders_a_clip_with_parallax(tmp_path):
    from trendclip.video_assembler import media_duration

    still = _still(tmp_path / "still.jpg")
    depth = torch.linspace(1, 0, 256)[:, None].expand(256, 144)  # near at the bottom
    out = scene_motion.animate(still, 1.0, tmp_path / "clip.mp4", index=0, fps=12, size=(144, 256), depth=depth, seed=3)
    assert out.is_file() and 0.9 <= media_duration(out) <= 1.1
    # the warp moves the near pixels more than the far ones
    image = torch.zeros(3, 256, 144)
    image[0, :, 72:] = 1.0  # a red right half
    frames = scene_motion.warp(image, depth, [(1.08, 0.0, 0.0, 0.05, 0.0)])
    near_edge = int((frames[0, 0, 250] > 0.5).float().argmax())
    far_edge = int((frames[0, 0, 5] > 0.5).float().argmax())
    assert near_edge != far_edge
    # a wrong size is fitted, fog and dust can be off
    out2 = scene_motion.animate(_still(tmp_path / "wide.jpg", 300, 200), 0.5, tmp_path / "clip2.mp4", fps=12,
                                size=(144, 256), depth=depth, fog=0, dust=False)
    assert out2.is_file()


def test_moving_pictures_falls_back_to_the_zoom(tmp_path, monkeypatch):
    beats = [video_gen.Beat(0, "hook", "t", "", "", 0.0, 2.0), video_gen.Beat(1, "scene 1", "t", "", "", 2.0, 5.0)]
    stills = [_still(tmp_path / f"g{i}.jpg") for i in range(2)]
    monkeypatch.setenv("DA_MOTION", "off")
    assert video_gen.moving_pictures(stills, beats, tmp_path) == (stills, "zoom")
    monkeypatch.setenv("DA_MOTION", "depth")
    monkeypatch.setattr(scene_motion, "available", lambda: False)
    assert video_gen.moving_pictures(stills, beats, tmp_path) == (stills, "zoom")
    monkeypatch.setattr(scene_motion, "available", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("no depth model")

    monkeypatch.setattr(scene_motion, "animate_all", boom)
    messages = []
    assert video_gen.moving_pictures(stills, beats, tmp_path, progress=lambda f, m: messages.append(m)) == (stills, "zoom")
    assert "no depth model" in messages[-1]
    seen = {}

    def fake_all(pictures, seconds, out_dir, fps, size, seed, progress):
        seen.update(seconds=seconds, seed=seed)
        return [out_dir / f"clip_{i + 1}.mp4" for i in range(len(pictures))]

    monkeypatch.setattr(scene_motion, "animate_all", fake_all)
    clips, how = video_gen.moving_pictures(stills, beats, tmp_path, seed=7)
    assert how == "depth" and [c.name for c in clips] == ["clip_1.mp4", "clip_2.mp4"] and seen["seed"] == 7
    assert seen["seconds"] == [pytest.approx(2.0 + 0.6 + 0.2), pytest.approx(3.0 + 0.6 + 1.5)]


def test_animate_all_with_a_fake_depth_model(tmp_path, monkeypatch):
    stills = [_still(tmp_path / f"g{i}.jpg") for i in range(2)]
    monkeypatch.setattr(scene_motion, "depth_map", lambda img: torch.rand(img.height, img.width))
    monkeypatch.setattr(scene_motion, "unload", lambda: None)
    clips = scene_motion.animate_all(stills, [0.5, 0.5], tmp_path / "clips", fps=12, size=(144, 256))
    assert [c.name for c in clips] == ["clip_1.mp4", "clip_2.mp4"] and all(c.is_file() for c in clips)
