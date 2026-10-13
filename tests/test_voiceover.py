from trendclip import local_voice, voiceover
from trendclip.video_assembler import Word


class Handshake403(Exception):
    pass


def test_f5_speed_follows_the_edge_rate():
    assert local_voice.speed("+5%") == 0.892
    assert local_voice.speed("+0%") == 0.85
    assert local_voice.speed("-20%") == 0.68
    assert local_voice.speed("") == 0.85
    assert local_voice.REF_WAV.is_file() and "three percent" in local_voice.REF_TEXT


def test_f5_silence_at_the_end_is_cut():
    import numpy as np

    rate = 24000
    rng = np.random.default_rng(0)
    speech = 0.3 * np.sin(np.linspace(0, 2000, 2 * rate))
    hiss = 0.002 * rng.standard_normal(3 * rate)
    audio = np.concatenate([hiss[: rate // 2], speech, hiss]).astype(np.float32)  # 0.5 s + 2 s + 3 s
    trimmed = local_voice.trim_silence(audio, rate)
    assert abs(trimmed.size / rate - (2 + 0.1 + 0.25)) < 0.05
    quiet = np.zeros(rate, dtype=np.float32)
    assert local_voice.trim_silence(quiet, rate).size == rate


def test_microsoft_refused_is_only_the_403_handshake():
    assert voiceover.microsoft_refused(Handshake403("403, message='Invalid response status'"))
    assert not voiceover.microsoft_refused(ConnectionError("nodename nor servname provided"))
    assert not voiceover.microsoft_refused(TimeoutError("timed out"))


def test_a_403_uses_the_local_voice_without_retrying(tmp_path, monkeypatch):
    sleeps = []

    async def refused(text, voice, rate):
        raise Handshake403("403, message='Invalid response status', url='wss://speech.platform.bing.com'")

    def local(text, rate):
        return b"ID3local", [Word("Hello", 0.1, 0.4), Word("there.", 0.4, 0.9)]

    monkeypatch.setattr(voiceover, "_synthesize", refused)
    monkeypatch.setattr(voiceover, "_local_voice", lambda text, rate, cause: local(text, rate))
    monkeypatch.setattr(voiceover.time, "sleep", lambda seconds: sleeps.append(seconds))

    words = voiceover.synthesize("Hello there.", tmp_path / "v.mp3", "en-US-AndrewNeural", waits=(10, 10))
    assert words[0].text == "Hello" and (tmp_path / "v.mp3").read_bytes() == b"ID3local"
    assert sleeps == []


def test_a_network_error_does_not_switch_to_the_local_voice(tmp_path, monkeypatch):
    async def offline(text, voice, rate):
        raise ConnectionError("nodename nor servname provided, or not known")

    monkeypatch.setattr(voiceover, "_synthesize", offline)
    monkeypatch.setattr(voiceover, "_local_voice", lambda *args: (_ for _ in ()).throw(AssertionError("local")))

    try:
        voiceover.synthesize("Hello there.", tmp_path / "v.mp3", "en-US-AndrewNeural", waits=())
    except voiceover.VoiceError as err:
        assert "internet" in str(err).lower()
    else:
        raise AssertionError("expected VoiceError")
