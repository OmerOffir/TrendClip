"""The Borderline Why animation library: clips kept by tag with sidecars, the pilot's progress, the dashboard's view."""

import json
import subprocess
import sys

import numpy as np
import pytest
from fastapi.testclient import TestClient

from modules import bw_anim


@pytest.fixture
def anim_root(tmp_path, monkeypatch):
    monkeypatch.setattr(bw_anim, "ROOT", tmp_path / "bw_anim")
    return tmp_path / "bw_anim"


def _fake_frames(n=4, h=64, w=64):
    return [np.full((h, w, 3), i * 40, dtype=np.uint8) for i in range(n)]


def test_every_tag_has_one_prompt_in_the_channel_style():
    for tag in bw_anim.TAGS:
        assert bw_anim.prompt_for(tag).endswith(bw_anim.STYLE)
    assert "no text" in bw_anim.STYLE


def test_a_clip_is_saved_with_its_sidecar_and_listed(anim_root, monkeypatch):
    monkeypatch.setitem(bw_anim.GENERATORS, "animatediff", lambda prompt, seed, cfg, progress: _fake_frames())
    monkeypatch.setattr(bw_anim, "_device_name", lambda: "test gpu")
    steps = []
    clip = bw_anim.generate("animatediff", "war", seed=7, progress=steps.append)
    assert clip.name == "animatediff_war_7" and clip.frames == 4 and clip.seconds == 0.5 and clip.fps == 24
    assert (anim_root / "pilot" / "animatediff_war_7.mp4").stat().st_size > 0
    side = json.loads((anim_root / "pilot" / "animatediff_war_7.json").read_text())
    assert side["tag"] == "war" and side["model"] == "animatediff" and side["device"] == "test gpu"
    assert "encoding the mp4" in steps
    assert [c["name"] for c in bw_anim.clips()] == ["animatediff_war_7"]
    assert bw_anim.clip_path("pilot", "animatediff_war_7") is not None
    assert bw_anim.clip_path("pilot", "../animatediff_war_7") is None


def test_the_pilot_records_a_failed_clip_and_goes_on(anim_root, monkeypatch):
    def boom(prompt, seed, cfg, progress):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setitem(bw_anim.GENERATORS, "wan", boom)
    monkeypatch.setitem(bw_anim.GENERATORS, "animatediff", lambda prompt, seed, cfg, progress: _fake_frames())
    monkeypatch.setattr(bw_anim, "_device_name", lambda: "test gpu")
    status = bw_anim.run_pilot(["animatediff", "wan"], ["war", "river"])
    assert status["done"] == 4 and status["total"] == 4 and not status["running"] and status["finished"]
    assert [f["clip"] for f in status["failed"]] == ["wan_war_1", "wan_river_1"]
    assert "out of memory" in status["failed"][0]["error"]
    assert sorted(c["name"] for c in bw_anim.clips()) == ["animatediff_river_1", "animatediff_war_1"]
    assert bw_anim.pilot_status()["done"] == 4
    # a second run keeps the clips it has
    again = bw_anim.run_pilot(["animatediff"], ["war"])
    assert again["done"] == 1 and not again["failed"]


def test_the_dashboard_lists_the_clips_and_serves_their_files(anim_root, monkeypatch):
    monkeypatch.setitem(bw_anim.GENERATORS, "animatediff", lambda prompt, seed, cfg, progress: _fake_frames())
    monkeypatch.setattr(bw_anim, "_device_name", lambda: "test gpu")
    bw_anim.generate("animatediff", "treaty")
    from trendclip.web.app import app

    client = TestClient(app)
    body = client.get("/api/borderlinewhy/anim").json()
    assert [c["name"] for c in body["clips"]] == ["animatediff_treaty_1"]
    assert body["tags"] == list(bw_anim.TAGS) and "wan" in body["models"]
    assert client.get("/api/borderlinewhy/anim/animatediff_treaty_1.mp4").status_code == 200
    assert client.get("/api/borderlinewhy/anim/nope.mp4").status_code == 404


def test_the_cli_parses_a_smoke_test(anim_root, monkeypatch):
    seen = {}

    def fake(models, tags, seed, name, overrides):
        seen.update(models=models, tags=tags, seed=seed, name=name, overrides=overrides)
        return {"failed": [], "done": 1, "total": 1}

    monkeypatch.setattr(bw_anim, "run_pilot", fake)
    assert bw_anim.main(["pilot", "--models", "wan", "--tags", "war", "--steps", "2", "--frames", "9", "--set", "smoke"]) == 0
    assert seen == {"models": ["wan"], "tags": ["war"], "seed": 1, "name": "smoke", "overrides": {"steps": 2, "frames": 9}}
