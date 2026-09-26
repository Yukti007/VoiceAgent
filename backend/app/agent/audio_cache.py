"""On-disk cache of synthesized audio for fixed phrases (the greeting).

The greeting text is known before the call starts, so there's no reason to
pay TTS time-to-first-byte for it on every call. The first call synthesizes
it once in the background; every later call (in any worker process on this
machine) streams the cached PCM straight into the room.

Cache files are raw 16-bit PCM plus a small JSON header, keyed by a hash of
everything that changes the audio: text, speaker, language and sample rate.
Changing the greeting or voice simply produces a new key.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from livekit import rtc

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

CACHE_DIR = DATA_DIR / "tts_cache"
FRAME_MS = 20


def cache_key(text: str, *, speaker: str, language: str, sample_rate: int) -> str:
    raw = json.dumps([text, speaker, language, sample_rate], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _paths(key: str, cache_dir: Path) -> tuple[Path, Path]:
    return cache_dir / f"{key}.pcm", cache_dir / f"{key}.json"


def load(key: str, cache_dir: Path = CACHE_DIR) -> tuple[bytes, int, int] | None:
    """Return (pcm_bytes, sample_rate, num_channels), or None on a cache miss."""
    pcm_path, meta_path = _paths(key, cache_dir)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        pcm = pcm_path.read_bytes()
    except (OSError, ValueError):
        return None
    if not pcm:
        return None
    return pcm, int(meta["sample_rate"]), int(meta["num_channels"])


def save(key: str, pcm: bytes, sample_rate: int, num_channels: int, cache_dir: Path = CACHE_DIR) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    pcm_path, meta_path = _paths(key, cache_dir)
    # Write the audio first and the header last, via temp files + rename, so a
    # concurrent reader never sees a half-written entry as a hit.
    tmp_pcm = pcm_path.with_suffix(".pcm.tmp")
    tmp_pcm.write_bytes(pcm)
    tmp_pcm.replace(pcm_path)
    tmp_meta = meta_path.with_suffix(".json.tmp")
    tmp_meta.write_text(json.dumps({"sample_rate": sample_rate, "num_channels": num_channels}))
    tmp_meta.replace(meta_path)


async def frames_from_pcm(pcm: bytes, sample_rate: int, num_channels: int) -> AsyncIterator[rtc.AudioFrame]:
    samples_per_frame = sample_rate * FRAME_MS // 1000
    bytes_per_frame = samples_per_frame * num_channels * 2
    for start in range(0, len(pcm), bytes_per_frame):
        chunk = pcm[start : start + bytes_per_frame]
        if len(chunk) < bytes_per_frame:
            chunk = chunk + b"\x00" * (bytes_per_frame - len(chunk))
        yield rtc.AudioFrame(
            data=chunk,
            sample_rate=sample_rate,
            num_channels=num_channels,
            samples_per_channel=samples_per_frame,
        )


async def synthesize_and_save(tts, text: str, key: str, cache_dir: Path = CACHE_DIR) -> None:
    """Synthesize `text` once with `tts` and store it. Never raises: a failed
    warm-up just means the next call uses live TTS again."""
    try:
        chunks: list[bytes] = []
        sample_rate = num_channels = 0
        async with tts.synthesize(text) as stream:
            async for audio in stream:
                frame = audio.frame
                sample_rate, num_channels = frame.sample_rate, frame.num_channels
                chunks.append(bytes(frame.data.cast("b")))
        if chunks:
            await asyncio.to_thread(save, key, b"".join(chunks), sample_rate, num_channels, cache_dir)
            logger.info("cached TTS audio %s (%d bytes)", key, sum(len(c) for c in chunks))
    except Exception:
        logger.warning("could not cache TTS audio for %s", key, exc_info=True)
