"""Phase 2 orchestration: provider dispatch, capability gating, caching."""

import json

import pytest

from clipper_pro import transcribe as phase2
from clipper_pro.config import Settings
from clipper_pro.errors import ConfigError, ToolFailureError, ValidationError
from clipper_pro.transcribe import providers
from clipper_pro.transcribe.base import PROVIDERS, TranscriptResult, provider_spec
from clipper_pro.types import SourceMedia, Word


def _raw(words=None, events=None, language="en"):
    """A host-repository-shaped transcript dict."""
    payload = {
        "text": "flat text",
        "language": language,
        "segments": [
            {
                "text": "hello world",
                "start": 0.0,
                "end": 1.0,
                "words": words
                if words is not None
                else [
                    {"word": "hello", "start": 0.0, "end": 0.4, "probability": 0.9, "speaker": 0},
                    {"word": "world", "start": 0.5, "end": 0.9, "probability": 0.8, "speaker": 1},
                ],
            }
        ],
    }
    if events is not None:
        payload["audio_events"] = events
    return payload


@pytest.fixture
def workspace(tmp_path):
    from clipper_pro.workspace import init

    root = tmp_path / "run"
    init(str(root))
    audio = root / "audio" / "a.flac"
    audio.write_bytes(b"AUDIO")
    return str(root), SourceMedia(path="/v/a.mp4", duration=10.0, audio_path=str(audio))


@pytest.fixture
def stub_provider(monkeypatch):
    """Replace the network call; record what it was asked for."""
    state = {"calls": [], "raw": _raw()}

    def fake_transcribe_raw(audio_path, provider):
        state["calls"].append((audio_path, provider))
        if isinstance(state["raw"], Exception):
            raise state["raw"]
        return state["raw"]

    monkeypatch.setattr(phase2, "transcribe_raw", fake_transcribe_raw)
    return state


class TestProviderSpec:
    def test_deepgram_is_the_default_and_lacks_event_tagging(self):
        # The documented pipeline names Nova-3, but it has no audio-event
        # feature; the capability is declared so phase 3 is not misled.
        spec = provider_spec("deepgram")
        assert spec.supports_diarization is True
        assert spec.supports_audio_events is False

    def test_elevenlabs_supports_events(self):
        assert provider_spec("elevenlabs").supports_audio_events is True

    def test_lookup_is_case_insensitive(self):
        assert provider_spec("DeepGram").name == "deepgram"

    def test_unknown_provider_is_rejected_with_the_options(self):
        with pytest.raises(ValidationError, match="unknown transcription provider"):
            provider_spec("whisper")

    def test_at_least_one_provider_can_tag_events(self):
        assert any(s.supports_audio_events for s in PROVIDERS.values())


class TestRunTranscribe:
    def test_returns_words_and_language(self, workspace, stub_provider):
        root, media = workspace
        result = phase2.run_transcribe(media, root, settings=Settings(transcript_cache=False))
        assert [w.text for w in result.words] == ["hello", "world"]
        assert result.language == "en"
        assert result.provider == "deepgram"
        assert result.model == "nova-3"

    def test_writes_the_transcript_artifact(self, workspace, stub_provider):
        root, media = workspace
        phase2.run_transcribe(media, root, settings=Settings(transcript_cache=False))
        with open(f"{root}/analysis/transcript.json") as fh:
            payload = json.load(fh)
        assert len(payload["words"]) == 2
        assert payload["speakers"] == [0, 1]

    def test_selects_the_requested_provider(self, workspace, stub_provider):
        root, media = workspace
        phase2.run_transcribe(
            media, root, provider="elevenlabs", settings=Settings(transcript_cache=False)
        )
        assert stub_provider["calls"][0][1] == "elevenlabs"

    def test_settings_provider_is_used_when_none_is_passed(self, workspace, stub_provider):
        root, media = workspace
        phase2.run_transcribe(
            media, root, settings=Settings(transcriber="elevenlabs", transcript_cache=False)
        )
        assert stub_provider["calls"][0][1] == "elevenlabs"

    def test_audio_events_are_surfaced_when_the_provider_tags_them(self, workspace, stub_provider):
        root, media = workspace
        stub_provider["raw"] = _raw(events=[{"text": "(laughter)", "start": 5.0, "end": 6.0}])
        result = phase2.run_transcribe(
            media, root, provider="elevenlabs", settings=Settings(transcript_cache=False)
        )
        assert [e.kind for e in result.events] == ["laughter"]

    def test_text_weaves_events_in_at_their_timestamps(self, workspace, stub_provider):
        root, media = workspace
        stub_provider["raw"] = _raw(events=[{"text": "(laughter)", "start": 0.45, "end": 0.5}])
        result = phase2.run_transcribe(
            media, root, provider="elevenlabs", settings=Settings(transcript_cache=False)
        )
        # Phase 3 reads this, so the payoff must sit where it happened.
        assert result.text == "hello (laughter) world"

    def test_require_events_rejects_a_provider_that_cannot_tag_them(self, workspace, stub_provider):
        root, media = workspace
        # An empty event list from Deepgram means "nobody looked", not "no
        # laughter" — the caller can refuse to accept that ambiguity.
        with pytest.raises(ValidationError, match="does not tag audio events"):
            phase2.run_transcribe(
                media, root, provider="deepgram", require_events=True,
                settings=Settings(transcript_cache=False),
            )
        assert stub_provider["calls"] == []

    def test_require_events_passes_for_a_capable_provider(self, workspace, stub_provider):
        root, media = workspace
        stub_provider["raw"] = _raw(events=[{"text": "(applause)", "start": 1.0, "end": 2.0}])
        result = phase2.run_transcribe(
            media, root, provider="elevenlabs", require_events=True,
            settings=Settings(transcript_cache=False),
        )
        assert len(result.events) == 1

    def test_missing_phase_one_audio_is_rejected(self, workspace, stub_provider):
        root, _ = workspace
        media = SourceMedia(path="/v/a.mp4", duration=10.0, audio_path="")
        with pytest.raises(ValidationError, match="needs the extracted audio"):
            phase2.run_transcribe(media, root, settings=Settings(transcript_cache=False))

    def test_transcript_without_word_timings_is_refused(self, workspace, stub_provider):
        root, media = workspace
        # Phases 4 and 5 cannot run on segment-level output, so accepting this
        # would only defer the failure to a later, more expensive phase.
        stub_provider["raw"] = {"text": "t", "language": "en", "segments": [{"words": []}]}
        with pytest.raises(ToolFailureError, match="no word-level timings"):
            phase2.run_transcribe(media, root, settings=Settings(transcript_cache=False))

    def test_provider_failure_propagates(self, workspace, stub_provider):
        root, media = workspace
        stub_provider["raw"] = ToolFailureError("deepgram transcription failed: 401")
        with pytest.raises(ToolFailureError, match="401"):
            phase2.run_transcribe(media, root, settings=Settings(transcript_cache=False))


class TestCaching:
    def test_second_run_hits_the_cache(self, workspace, stub_provider):
        root, media = workspace
        settings = Settings(transcript_cache=True)
        first = phase2.run_transcribe(media, root, settings=settings)
        phase2.run_transcribe(media, root, settings=settings)
        assert len(stub_provider["calls"]) == 1
        assert first.words[0].text == "hello"

    def test_cached_result_round_trips_words_and_events(self, workspace, stub_provider):
        root, media = workspace
        stub_provider["raw"] = _raw(events=[{"text": "(laughter)", "start": 5.0, "end": 6.0}])
        settings = Settings(transcriber="elevenlabs", transcript_cache=True)
        first = phase2.run_transcribe(media, root, settings=settings)
        second = phase2.run_transcribe(media, root, settings=settings)
        assert first.to_dict() == second.to_dict()

    def test_no_cache_forces_a_re_transcription(self, workspace, stub_provider):
        root, media = workspace
        settings = Settings(transcript_cache=True)
        phase2.run_transcribe(media, root, settings=settings)
        phase2.run_transcribe(media, root, settings=settings, use_cache=False)
        assert len(stub_provider["calls"]) == 2

    def test_switching_provider_does_not_reuse_the_other_cache_entry(self, workspace, stub_provider):
        root, media = workspace
        phase2.run_transcribe(media, root, settings=Settings(transcript_cache=True))
        phase2.run_transcribe(
            media, root, provider="elevenlabs", settings=Settings(transcript_cache=True)
        )
        assert [c[1] for c in stub_provider["calls"]] == ["deepgram", "elevenlabs"]


class TestNormalizeTranscript:
    def test_reports_dropped_entries(self):
        result, dropped = phase2.normalize_transcript(
            _raw(words=[
                {"word": "ok", "start": 0, "end": 1},
                {"word": "", "start": 0, "end": 1},
            ]),
            provider="deepgram", model="nova-3",
        )
        assert [w.text for w in result.words] == ["ok"]
        assert dropped == 1

    def test_falls_back_to_provider_text_when_nothing_can_be_woven(self):
        result, _ = phase2.normalize_transcript({"text": "flat", "segments": []})
        assert result.text == "flat"


class TestTranscriptResult:
    def test_duration_is_the_last_word_end(self):
        result = TranscriptResult(words=[Word("a", 0, 1), Word("b", 4, 7.5)])
        assert result.duration == pytest.approx(7.5)

    def test_empty_transcript_has_zero_duration_and_no_speakers(self):
        assert TranscriptResult().duration == 0.0
        assert TranscriptResult().speakers == set()

    def test_speakers_ignores_unlabelled_words(self):
        result = TranscriptResult(words=[Word("a", 0, 1, speaker=0), Word("b", 1, 2)])
        assert result.speakers == {0}

    def test_round_trips_through_dict(self):
        result = TranscriptResult(
            words=[Word("a", 0, 1, speaker=0, confidence=0.9)],
            language="en", text="a", provider="deepgram", model="nova-3",
        )
        assert TranscriptResult.from_dict(result.to_dict()).to_dict() == result.to_dict()


class TestProviderAdapters:
    def test_effective_model_defaults_per_provider(self, monkeypatch):
        monkeypatch.delenv("DEEPGRAM_MODEL", raising=False)
        monkeypatch.delenv("ELEVENLABS_MODEL", raising=False)
        assert providers.effective_model(provider_spec("deepgram")) == "nova-3"
        assert providers.effective_model(provider_spec("elevenlabs")) == "scribe_v1"

    def test_effective_model_honours_the_env_override(self, monkeypatch):
        # The cache key embeds this, so it must match what the backend will use.
        monkeypatch.setenv("DEEPGRAM_MODEL", "nova-2")
        assert providers.effective_model(provider_spec("deepgram")) == "nova-2"

    def test_blank_env_override_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("DEEPGRAM_MODEL", "   ")
        assert providers.effective_model(provider_spec("deepgram")) == "nova-3"

    def test_missing_credential_names_the_variable(self, monkeypatch):
        monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
        with pytest.raises(ConfigError, match="DEEPGRAM_API_KEY"):
            providers.require_api_key(provider_spec("deepgram"))

    def test_present_credential_is_returned(self, monkeypatch):
        monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-test")
        assert providers.require_api_key(provider_spec("elevenlabs")) == "sk-test"

    def test_transcribe_raw_checks_the_credential_before_the_network(self, monkeypatch):
        monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
        with pytest.raises(ConfigError):
            providers.transcribe_raw("/tmp/a.flac", "deepgram")
