import pytest

from trendclip import kokoro_voice, stickers


@pytest.fixture(autouse=True)
def no_kokoro(monkeypatch):
    """Tests see the Edge voice unless they ask for Kokoro: the same path on the Mac and the hub GPU."""
    monkeypatch.setattr(kokoro_voice, "available", lambda: False)


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
