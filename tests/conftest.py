import pytest

from trendclip import stickers


@pytest.fixture(autouse=True)
def offline_sticker_tagging(monkeypatch):
    """Tests never send the real stickers/ folder to Gemini."""
    monkeypatch.setattr(stickers, "tag_with_gemini", lambda settings, infos, client=None: infos)
    stickers._memory.clear()
