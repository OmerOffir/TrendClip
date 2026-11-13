import pytest

from trendclip import kokoro_voice, stickers


@pytest.fixture(autouse=True)
def no_kokoro(monkeypatch):
    """Tests see the Edge voice unless they ask for Kokoro: the same path on the Mac and the hub GPU."""
    monkeypatch.setattr(kokoro_voice, "available", lambda: False)


@pytest.fixture(autouse=True)
def no_ear(monkeypatch):
    """Tests never load Whisper (modules.audio_check): it is the hub's GPU work, minutes on a CPU."""
    monkeypatch.setenv("AUDIO_CHECK", "off")


@pytest.fixture(autouse=True)
def offline_sticker_tagging(monkeypatch):
    """Tests never send the real stickers/ folder to Gemini."""
    monkeypatch.setattr(stickers, "tag_with_gemini", lambda settings, infos, client=None: infos)
    stickers._memory.clear()


@pytest.fixture(autouse=True)
def own_gemini_ledger(tmp_path, monkeypatch):
    """Every test gets its own Gemini quota ledger: fake 429s and successes never touch output/gemini_quota.json."""
    from trendclip import gemini_quota

    monkeypatch.setattr(gemini_quota, "LEDGER", tmp_path / "gemini_quota.json")
    monkeypatch.setattr(gemini_quota, "_setting", lambda name, default: default)


@pytest.fixture(autouse=True)
def no_real_youtube(monkeypatch):
    """Tests never reach YouTube with the owner's login. On 2026-11-02 the tab test approved a fake video whose
    slot had passed, the background upload ran with the real token, and every test run put a broken
    "Minnesota | Weird Borders #1" Short on the channel. Tests that need a client pass a fake one."""
    from trendclip import publish

    def offline(*args, **kwargs):
        raise publish.PublishError("tests never reach YouTube")

    monkeypatch.setattr(publish, "_youtube", offline)
    monkeypatch.setattr(publish, "_credentials", offline)
