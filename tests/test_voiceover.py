from trendclip import local_voice, voiceover
from trendclip.video_assembler import Word


class Handshake403(Exception):
    pass


def test_f5_speed_follows_the_edge_rate():
    assert local_voice.speed("+5%") == 1.05
    assert local_voice.speed("+0%") == 1.0
    assert local_voice.speed("") == 1.0
    assert local_voice.REF_WAV.is_file() and "three percent" in local_voice.REF_TEXT


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
