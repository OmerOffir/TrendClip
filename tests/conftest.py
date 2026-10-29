import pytest

from trendclip import stickers


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
