from contextlib import asynccontextmanager
from types import SimpleNamespace

from livekit import rtc

from app.agent import audio_cache


def test_cache_key_changes_with_voice_settings():
    base = dict(speaker="pooja", language="hi-IN", sample_rate=24000)
    key = audio_cache.cache_key("Hello", **base)
    assert key == audio_cache.cache_key("Hello", **base)
    assert key != audio_cache.cache_key("Hello!", **base)
    assert key != audio_cache.cache_key("Hello", **{**base, "speaker": "other"})


def test_load_miss_returns_none(tmp_path):
    assert audio_cache.load("missing", cache_dir=tmp_path) is None


async def test_synthesize_save_load_and_replay_roundtrip(tmp_path):
    sample_rate, samples = 24000, 480  # 20ms frames
    frames = [
        rtc.AudioFrame(
            data=bytes([i]) * samples * 2,
            sample_rate=sample_rate,
            num_channels=1,
            samples_per_channel=samples,
        )
        for i in range(1, 4)
    ]

    class FakeTTS:
        @asynccontextmanager
        async def _stream(self):
            async def gen():
                for f in frames:
                    yield SimpleNamespace(frame=f)

            yield gen()

        def synthesize(self, text):
            return self._stream()

    await audio_cache.synthesize_and_save(FakeTTS(), "Hello", "k1", cache_dir=tmp_path)
    loaded = audio_cache.load("k1", cache_dir=tmp_path)
    assert loaded is not None
    pcm, rate, channels = loaded
    assert (rate, channels) == (sample_rate, 1)
    assert pcm == b"".join(bytes(f.data.cast("b")) for f in frames)

    replayed = [f async for f in audio_cache.frames_from_pcm(pcm, rate, channels)]
    assert len(replayed) == 3
    assert all(f.samples_per_channel == samples for f in replayed)


async def test_synthesize_failure_is_swallowed(tmp_path):
    class BrokenTTS:
        def synthesize(self, text):
            raise RuntimeError("provider down")

    await audio_cache.synthesize_and_save(BrokenTTS(), "Hello", "k2", cache_dir=tmp_path)
    assert audio_cache.load("k2", cache_dir=tmp_path) is None
