"""The ear (modules/audio_check): what counts as a word said wrong, and how the narration is cut to be heard."""

import json

from fastapi.testclient import TestClient

from modules import audio_check, pw_channel


def test_a_word_said_wrong_is_reported_and_a_spelling_is_not():
    said = "2009, Paris. Noel walks out. Oasis is over.".split()
    assert audio_check.misheard(said, "2009 Paris. Noelle walks out. Oasis is over.") == []
    assert audio_check.misheard(said, "2009 Paris. Noel walks out. Wasis is over.") == [("oasis", "wasis")]
    assert audio_check.misheard("November 1991. Freddie dies.".split(), "November 1991. Freddy dices.") == [("dies", "dices")]
    assert audio_check.misheard("Twenty minutes, nearly two billion watching.".split(), "20 minutes, nearly 2 billion watching.") == []
    assert audio_check.misheard("Queen steal the show.".split(), "Queens steal the show.") == []
    assert audio_check.misheard("1996. Knebworth.".split(), "1996. Never.") == [("knebworth", "never")]


def test_the_narration_is_heard_a_sentence_at_a_time():
    words = [{"word": w, "start": k * 0.4, "end": k * 0.4 + 0.3} for k, w in enumerate("One two. Three four five? Six".split())]
    got = audio_check.pieces(words)
    assert [p[2] for p in got] == [["One", "two."], ["Three", "four", "five?"], ["Six"]]
    long = [{"word": "w", "start": k * 1.0, "end": k * 1.0 + 0.5} for k in range(60)]
    assert all(p[1] - p[0] <= audio_check.MAX_PIECE for p in audio_check.pieces(long))


def test_the_owner_adds_a_song_to_a_queued_band_script(tmp_path, monkeypatch):
    from trendclip.web import app as web_app

    monkeypatch.setattr(pw_channel, "OUTPUT_DIR", tmp_path)
    (tmp_path / "queue").mkdir()
    (tmp_path / "queue" / "q.json").write_text(json.dumps({"format": "band", "title": "Queen in 45 Seconds", "scenes": []}))
    client = TestClient(web_app.app)
    r = client.post("/api/pricetagwhy/song", data={"script": "q.json", "start": "65"}, files={"file": ("Bohemian Rhapsody.mp3", b"ID3data", "audio/mpeg")})
    assert r.status_code == 200, r.text
    assert (tmp_path / "songs" / "q.mp3").read_bytes() == b"ID3data"
    song = json.loads((tmp_path / "queue" / "q.json").read_text())["song"]
    assert song == {"file": "q.mp3", "start": 65.0, "name": "Bohemian Rhapsody"}
    listed = next(s for s in pw_channel.scripts(tmp_path) if s["file"] == "q.json")
    assert listed["song"]["start"] == 65.0 and listed["band"] and listed["in_queue"]
    assert client.post("/api/pricetagwhy/song", data={"script": "q.json"}, files={"file": ("x.exe", b"MZ", "application/x")}).status_code == 400
    assert client.post("/api/pricetagwhy/song/remove", json={"file": "q.json"}).status_code == 200
    assert "song" not in json.loads((tmp_path / "queue" / "q.json").read_text())
    assert not (tmp_path / "songs" / "q.mp3").exists()
    assert pw_channel.song_track({"title": "x"}, 30, tmp_path / "m.mp3", tmp_path / "v.mp3", tmp_path) is None
