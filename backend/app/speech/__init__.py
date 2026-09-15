"""Speech I/O.

The requested layout for this package was `sarvam_stt.py` / `sarvam_tts.py`
hand-rolled adapters. Instead this project uses the official, actively
maintained `livekit-plugins-sarvam` package directly (`sarvam.STTRealtime` for
Saaras realtime STT, `sarvam.TTS` for Bulbul streaming TTS) inside
`app/agent/agent.py` -- it already implements the exact LiveKit STT/TTS
provider interfaces this package would otherwise reimplement.

This package is kept as the documented seam for a *custom* speech provider
(e.g. a different vendor, or a local model) should one be needed later: add a
module here implementing `livekit.agents.stt.STT` / `livekit.agents.tts.TTS`,
then swap it in where `app/agent/agent.py` constructs `AgentSession`.
"""
