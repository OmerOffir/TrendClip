import json
import os
import subprocess

import pytest
from PIL import Image

from modules import thumbs


def _video(path, color="red"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x568:d=1",
                    "-pix_fmt", "yuv420p", str(path)], check=True)


def test_paths_per_channel(tmp_path):
    assert thumbs.paths("borderlinewhy", "2026-10-31_1430", tmp_path) == (
        tmp_path / "borderlinewhy" / "2026-10-31_1430" / "video.mp4", tmp_path / "borderlinewhy" / "2026-10-31_1430" / "thumbnail.jpg")
    assert thumbs.paths("darkarchives", "30", tmp_path)[1] == tmp_path / "darkarchives" / "story_30.thumbnail.jpg"
    assert thumbs.paths("sidequestlogic", "gta_1.mp4", tmp_path)[1] == tmp_path / "shorts" / "gta_1.thumbnail.jpg"


@pytest.mark.parametrize("ref", ["../secret", "a/b", ".hidden", ""])
def test_bad_refs_are_refused(tmp_path, ref):
    with pytest.raises(thumbs.ThumbError):
        thumbs.paths("sidequestlogic", ref, tmp_path)


def test_unknown_channel_and_missing_video(tmp_path):
    with pytest.raises(thumbs.ThumbError):
        thumbs.paths("aftermarket", "1", tmp_path)
    with pytest.raises(thumbs.ThumbError):
        thumbs.ensure("darkarchives", "9", tmp_path)


def test_dark_outliers_thumbnail(tmp_path):
    _video(tmp_path / "darkarchives" / "story_7.mp4")
    (tmp_path / "darkarchives" / "story_7.json").write_text(json.dumps({"cover_text": "Infected on purpose?"}))
    out = thumbs.draw("darkarchives", "7", tmp_path)
    assert Image.open(out).size == (1080, 1920)
    assert thumbs.fresh("darkarchives", "7", tmp_path) == out


def test_sidequest_thumbnail_without_its_clip(tmp_path):
    _video(tmp_path / "shorts" / "gta_1.mp4", "blue")
    (tmp_path / "shorts" / "gta_1.json").write_text(json.dumps({"title_card": "LEVEL 12 - GTA FANS ONLY", "background": "gone.mp4"}))
    out = thumbs.draw("sidequestlogic", "gta_1.mp4", tmp_path)
    assert Image.open(out).size == (1080, 1920)


def test_a_newer_video_makes_the_thumbnail_stale(tmp_path):
    video, thumb = thumbs.paths("darkarchives", "3", tmp_path)
    _video(video)
    thumb.write_bytes(b"x")
    os.utime(thumb, (1, 1))
    assert thumbs.fresh("darkarchives", "3", tmp_path) is None
