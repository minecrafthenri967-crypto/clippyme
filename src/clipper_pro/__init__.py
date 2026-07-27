"""AI-Clipper Pro — an agentic long-form → vertical-short pipeline.

The package is organised as seven phases, each an importable subpackage. Data
moves between them as the plain dataclasses in :mod:`clipper_pro.types`, so any
phase can be run, tested, or replaced on its own:

1. :mod:`clipper_pro.ingest`     — yt-dlp download + immediate mono-16 kHz FLAC
   extraction (audio is separated *before* anything else touches the media, so
   every downstream ASR/analysis step uploads megabytes instead of gigabytes).
2. :mod:`clipper_pro.transcribe` — Deepgram Nova-3 with ``smart_format`` and
   ``diarize``, plus audio-event extraction (laughter/applause) used as
   "emotional payoff" markers.
3. :mod:`clipper_pro.rank`       — 5-axis virality rubric behind a provider
   abstraction (DeepSeek-V3 default, Gemini adapter), SQLite-cached.
4. :mod:`clipper_pro.cut`        — semantic edge snapping: word alignment →
   sentence expansion → waveform-silence nudging.
5. :mod:`clipper_pro.reframe`    — MAR speaker identification, predictive pan
   ahead of speech onset, Savitzky-Golay camera smoothing.
6. :mod:`clipper_pro.render`     — one filtergraph per clip, single decode/encode.
7. :mod:`clipper_pro.export`     — CapCut-ready CRF-18 MP4s + a scored report.

**Layering rule** (inherited from the host repository): every module named
``*_ops.py`` is pure — stdlib only, no subprocess, no cv2/torch — and is
covered by the fast host suite. Modules that shell out or touch the network sit
next to them and stay thin enough to be obvious by inspection. Put new logic in
the ``_ops`` half unless it genuinely needs I/O.
"""

__all__ = ["SCHEMA_VERSION", "__version__"]

__version__ = "0.1.0"

# Bumped only when the on-disk contracts change shape (workspace manifest,
# provenance sidecars, phase artifact JSON). Readers compare against this to
# refuse artifacts they cannot interpret.
SCHEMA_VERSION = 1
