"""Gemini writes the Short: it watches the start of the background clip and returns a voiceover
script, title, description and hashtags as structured JSON."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from dotenv import dotenv_values
from pydantic import BaseModel, Field

from .config import PROJECT_ROOT, Settings

logger = logging.getLogger(__name__)

WORDS_PER_SECOND = 3.6  # measured: edge-tts neural voices at +5% speak ~3.7-3.8 words/s
UPLOAD_WAIT_SECONDS = 180
ProgressFn = Callable[[float | None, str], None]


class ScriptError(RuntimeError):
    pass


class GeminiShort(BaseModel):
    """Response schema sent to Gemini (field descriptions become part of the instructions)."""

    on_screen: str = Field(description="One or two sentences on what actually happens in the clip.")
    hook: str = Field(description="The first sentence of the script: a scroll-stopping hook, at most 12 words.")
    script: str = Field(description="The full voiceover, starting with the hook. Plain spoken English only.")
    title: str = Field(description="YouTube Shorts title, at most 70 characters, no hashtags.")
    description: str = Field(description="Two or three short sentences for the video description, no hashtags.")
    hashtags: list[str] = Field(description="5 to 8 relevant hashtags, each starting with #, including #shorts.")


class ShortScript(GeminiShort):
    game: str
    model: str
    watched_clip: bool
    word_count: int
    estimated_seconds: float


def gemini_api_key(settings: Settings | None = None) -> str | None:
    """Read fresh from .env each time, so adding the key works without restarting the dashboard."""
    from_file = dotenv_values(PROJECT_ROOT / ".env").get("GEMINI_API_KEY")
    key = (from_file or os.getenv("GEMINI_API_KEY") or "").strip()
    if not key and settings and settings.gemini_api_key:
        key = settings.gemini_api_key.get_secret_value().strip()
    return key or None


def gemini_model(settings: Settings) -> str:
    return (dotenv_values(PROJECT_ROOT / ".env").get("GEMINI_MODEL") or settings.gemini_model).strip()


def estimate_seconds(text: str) -> float:
    return round(len(text.split()) / WORDS_PER_SECOND, 1)


SYSTEM_INSTRUCTION = """You write voiceovers for vertical gaming Shorts (YouTube Shorts, TikTok, Reels).
The voiceover is read by a text-to-speech voice over gameplay footage, with karaoke subtitles.

Rules:
- Plain spoken English. No emojis, hashtags, stage directions, brackets, timestamps or speaker labels.
- The first sentence is the hook and must make people stop scrolling within two seconds.
- Short punchy sentences. Talk to the viewer ("you"). Keep energy high but natural.
- Tie the narration to what is visible in the clip when you have watched it.
- Only state facts you are confident are true about the game; prefer tips, reactions, questions and
  observations over specific numbers, dates or patch details you are unsure of.
- Never mention copyright, footage sources, AI, or that the clip is stock gameplay.
- End with a short call to action (follow, comment, or a question).
"""


def build_prompt(game: str, target_seconds: int, trend_titles: list[str], notes: str, watched: bool) -> str:
    words = int(target_seconds * WORDS_PER_SECOND)
    lines = [
        f"Game: {game}",
        f"Target length: about {target_seconds} seconds of speech, so {words - 8} to {words + 5} words in total.",
    ]
    if watched:
        lines.append(f"The attached video is the exact footage that plays under the voiceover ({target_seconds}s).")
    else:
        lines.append("You have not seen the footage; write narration that fits generic gameplay of this game.")
    if trend_titles:
        lines.append("Videos about this game trending on YouTube right now (use them for the angle, don't copy them):")
        lines += [f"- {t}" for t in trend_titles[:10]]
    if notes.strip():
        lines.append(f"Creator's notes for this Short: {notes.strip()}")
    return "\n".join(lines)


def _preview_clip(clip: Path, seconds: int) -> Path:
    """Small 480p silent copy of the part of the clip the Short uses: quick to upload."""
    from .video_assembler import ffmpeg_with_libass

    out = Path(tempfile.mkstemp(prefix="trendclip-preview-", suffix=".mp4")[1])
    proc = subprocess.run(
        [ffmpeg_with_libass(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip),
         "-t", str(seconds + 2), "-vf", "scale=-2:480", "-r", "15", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        out.unlink(missing_ok=True)
        raise ScriptError(f"Could not prepare the clip for Gemini: {proc.stderr.strip()[-200:]}")
    return out


def _explain(err: Exception, model: str) -> ScriptError:
    code = getattr(err, "code", None)
    text = str(getattr(err, "message", "") or err)
    if code in (401, 403) or "API key" in text or "API_KEY" in text:
        return ScriptError("Gemini rejected the API key: check GEMINI_API_KEY in .env")
    if code == 429:
        return ScriptError("Gemini quota reached (free tier limit); wait a minute or try tomorrow")
    if code == 404:
        return ScriptError(f"Gemini model '{model}' not found: set GEMINI_MODEL in .env")
    return ScriptError(f"Gemini error{f' {code}' if code else ''}: {text[:300]}")


def _clean_hashtags(tags: list[str]) -> list[str]:
    out = []
    for tag in tags:
        tag = "#" + re.sub(r"[^\w]", "", tag.lstrip("#"))
        if len(tag) > 1 and tag.lower() not in {t.lower() for t in out}:
            out.append(tag)
    if "#shorts" not in {t.lower() for t in out}:
        out.append("#shorts")
    return out[:10]


def _clean_script(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)|\*+", "", text)  # stage directions / markdown
    text = re.sub(r"#\w+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def write_script(
    settings: Settings,
    game: str,
    clip: Path | None = None,
    *,
    trend_titles: list[str] | None = None,
    notes: str = "",
    target_seconds: int | None = None,
    watch_clip: bool = True,
    progress: ProgressFn = lambda f, m: None,
    client=None,
) -> ShortScript:
    api_key = gemini_api_key(settings)
    if client is None and not api_key:
        raise ScriptError("GEMINI_API_KEY is not set in .env (create one at https://aistudio.google.com/apikey)")
    from google import genai
    from google.genai import errors, types

    model = gemini_model(settings)
    target = target_seconds or settings.short_target_seconds
    client = client or genai.Client(api_key=api_key)
    watched = bool(watch_clip and clip and clip.is_file())

    contents: list = []
    uploaded = None
    preview = None
    try:
        if watched:
            progress(None, "Preparing the clip for Gemini")
            preview = _preview_clip(clip, target)
            progress(None, "Uploading the clip to Gemini")
            uploaded = client.files.upload(file=str(preview), config={"mime_type": "video/mp4"})
            deadline = time.monotonic() + UPLOAD_WAIT_SECONDS
            while uploaded.state and uploaded.state.name == "PROCESSING":
                if time.monotonic() > deadline:
                    raise ScriptError("Gemini took too long to process the clip; try again or untick 'watch the clip'")
                progress(None, "Gemini is watching the clip")
                time.sleep(3)
                uploaded = client.files.get(name=uploaded.name)
            if uploaded.state and uploaded.state.name == "FAILED":
                raise ScriptError("Gemini could not process the clip")
            contents.append(uploaded)

        contents.append(build_prompt(game, target, trend_titles or [], notes, watched))
        progress(None, f"Gemini ({model}) is writing the script")
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=GeminiShort,
                temperature=0.9,
            ),
        )
        result = response.parsed
        if not isinstance(result, GeminiShort):
            if not response.text:
                raise ScriptError("Gemini returned an empty answer (possibly blocked); try different notes")
            result = GeminiShort.model_validate_json(response.text)
    except errors.APIError as err:
        raise _explain(err, model) from err
    finally:
        if preview:
            preview.unlink(missing_ok=True)
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:  # noqa: BLE001 - files expire after 48h anyway
                logger.debug("Could not delete uploaded file %s", uploaded.name)

    script = _clean_script(result.script)
    return ShortScript(
        on_screen=result.on_screen.strip(),
        hook=result.hook.strip(),
        script=script,
        title=result.title.strip().strip('"')[:100],
        description=result.description.strip(),
        hashtags=_clean_hashtags(result.hashtags),
        game=game,
        model=model,
        watched_clip=watched,
        word_count=len(script.split()),
        estimated_seconds=estimate_seconds(script),
    )
