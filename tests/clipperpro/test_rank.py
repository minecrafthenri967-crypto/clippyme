"""Phase 3 orchestration: provider dispatch, SQLite caching, artifact writing."""

import json

import pytest

from clipper_pro import rank as phase3
from clipper_pro.config import Settings
from clipper_pro.errors import ConfigError, ToolFailureError, ValidationError
from clipper_pro.rank import cache, providers
from clipper_pro.rank.base import RANKERS, ranker_spec
from clipper_pro.transcribe.base import TranscriptResult
from clipper_pro.types import AudioEvent, Word


def _transcript(events=None, words=None):
    return TranscriptResult(
        words=words if words is not None else [Word(f"w{i}", i * 1.0, i * 1.0 + 0.5) for i in range(120)],
        events=events or [],
        language="en",
        text="a long talk",
        provider="elevenlabs",
        model="scribe_v1",
    )


def _response(*ranges):
    return {
        "clips": [
            {
                "start": s,
                "end": e,
                "title": f"clip {i}",
                "reason": "specific moment",
                "scores": {"hook": 9, "emotion": 7, "quotability": 8,
                           "completeness": 8, "density": 6},
            }
            for i, (s, e) in enumerate(ranges)
        ]
    }


@pytest.fixture
def workspace(tmp_path):
    from clipper_pro.workspace import init

    root = tmp_path / "run"
    init(str(root))
    return str(root)


@pytest.fixture
def stub_model(monkeypatch):
    """Replace the provider HTTP call with a canned JSON answer."""
    state = {"calls": [], "text": json.dumps(_response((10.0, 40.0), (60.0, 100.0)))}

    def fake_complete(prompt, provider, **kwargs):
        state["calls"].append((provider, prompt))
        if isinstance(state["text"], Exception):
            raise state["text"]
        return state["text"]

    monkeypatch.setattr(phase3, "complete", fake_complete)
    return state


class TestRankerSpec:
    def test_deepseek_is_the_default_provider(self):
        assert Settings().ranker == "deepseek"
        assert ranker_spec("deepseek").default_model == "deepseek-chat"

    def test_gemini_reuses_the_host_credential(self):
        # A deployment already running ClippyMe needs no second key to rank.
        assert ranker_spec("gemini").api_key_env == "GEMINI_API_KEY"

    def test_lookup_is_case_insensitive(self):
        assert ranker_spec("DeepSeek").name == "deepseek"

    def test_unknown_provider_lists_the_options(self):
        with pytest.raises(ValidationError, match="unknown ranking provider"):
            ranker_spec("gpt4")

    def test_every_spec_names_its_credential_and_model_env(self):
        for spec in RANKERS.values():
            assert spec.api_key_env and spec.model_env and spec.default_model


class TestRunRank:
    def test_returns_scored_candidates_in_time_order(self, workspace, stub_model):
        candidates = phase3.run_rank(
            _transcript(), workspace, settings=Settings(rank_cache=False)
        )
        assert [c.start for c in candidates] == [10.0, 60.0]
        assert candidates[0].scores.hook == 9

    def test_writes_the_candidates_artifact_with_the_weights(self, workspace, stub_model):
        phase3.run_rank(_transcript(), workspace, settings=Settings(rank_cache=False))
        with open(f"{workspace}/analysis/candidates.json") as fh:
            payload = json.load(fh)
        assert len(payload["clips"]) == 2
        # Weights are recorded so a report stays explainable after a retune.
        assert payload["weights"]["hook"] == pytest.approx(0.30)
        assert payload["clips"][0]["scores"]["total"] > 0

    def test_uses_the_requested_provider(self, workspace, stub_model):
        phase3.run_rank(
            _transcript(), workspace, provider="gemini", settings=Settings(rank_cache=False)
        )
        assert stub_model["calls"][0][0] == "gemini"

    def test_settings_provider_applies_when_none_is_passed(self, workspace, stub_model):
        phase3.run_rank(
            _transcript(), workspace, settings=Settings(ranker="gemini", rank_cache=False)
        )
        assert stub_model["calls"][0][0] == "gemini"

    def test_audio_events_reach_the_prompt(self, workspace, stub_model):
        phase3.run_rank(
            _transcript(events=[AudioEvent("laughter", 30.0, 31.0)]),
            workspace, settings=Settings(rank_cache=False),
        )
        assert "laughter,30.00,31.00" in stub_model["calls"][0][1]

    def test_instructions_reach_the_prompt_fenced(self, workspace, stub_model):
        phase3.run_rank(
            _transcript(), workspace, instructions="prefer the technical bits",
            settings=Settings(rank_cache=False),
        )
        prompt = stub_model["calls"][0][1]
        assert "<preferences>" in prompt and "prefer the technical bits" in prompt

    def test_max_clips_caps_the_selection(self, workspace, stub_model):
        stub_model["text"] = json.dumps(
            _response((10.0, 40.0), (60.0, 100.0), (110.0, 140.0))
        )
        candidates = phase3.run_rank(
            _transcript(), workspace, max_clips=2, settings=Settings(rank_cache=False)
        )
        assert len(candidates) == 2

    def test_empty_transcript_is_refused(self, workspace, stub_model):
        with pytest.raises(ToolFailureError, match="transcript is empty"):
            phase3.run_rank(
                _transcript(words=[]), workspace, settings=Settings(rank_cache=False)
            )
        assert stub_model["calls"] == []

    def test_unparseable_model_output_is_reported_with_a_sample(self, workspace, stub_model):
        stub_model["text"] = "I'm afraid I can't do that."
        with pytest.raises(ToolFailureError, match="could not be parsed as JSON"):
            phase3.run_rank(_transcript(), workspace, settings=Settings(rank_cache=False))

    def test_json_is_recovered_from_fences_and_reasoning(self, workspace, stub_model):
        # The host repair chain handles chain-of-thought plus a code fence.
        stub_model["text"] = (
            "Let me think about this.\n### JSON ###\n```json\n"
            + json.dumps(_response((10.0, 40.0)))
            + "\n```"
        )
        candidates = phase3.run_rank(
            _transcript(), workspace, settings=Settings(rank_cache=False)
        )
        assert len(candidates) == 1

    def test_smart_quotes_and_trailing_commas_are_repaired(self, workspace, stub_model):
        stub_model["text"] = (
            '### JSON ###\n{"clips": [{"start": 10.0, "end": 40.0, '
            '"title": "x", "reason": "y", "scores": {"hook": 9},},]}'
        )
        assert len(phase3.run_rank(
            _transcript(), workspace, settings=Settings(rank_cache=False)
        )) == 1

    def test_all_clips_rejected_is_an_error_not_an_empty_list(self, workspace, stub_model):
        # Silently returning nothing would let phases 4-7 run on an empty set.
        stub_model["text"] = json.dumps(_response((10.0, 11.0)))  # below the minimum
        with pytest.raises(ToolFailureError, match="no usable clips"):
            phase3.run_rank(_transcript(), workspace, settings=Settings(rank_cache=False))

    def test_provider_failure_propagates(self, workspace, stub_model):
        stub_model["text"] = ToolFailureError("deepseek ranking failed — HTTP 401")
        with pytest.raises(ToolFailureError, match="401"):
            phase3.run_rank(_transcript(), workspace, settings=Settings(rank_cache=False))

    def test_overlapping_proposals_are_deduped(self, workspace, stub_model):
        stub_model["text"] = json.dumps(_response((10.0, 60.0), (12.0, 60.0)))
        candidates = phase3.run_rank(
            _transcript(), workspace, settings=Settings(rank_cache=False)
        )
        assert len(candidates) == 1


class TestCaching:
    def test_second_run_hits_the_sqlite_cache(self, workspace, stub_model):
        settings = Settings(rank_cache=True)
        first = phase3.run_rank(_transcript(), workspace, settings=settings)
        second = phase3.run_rank(_transcript(), workspace, settings=settings)
        assert len(stub_model["calls"]) == 1
        assert [c.start for c in first] == [c.start for c in second]

    def test_no_cache_forces_a_fresh_call(self, workspace, stub_model):
        settings = Settings(rank_cache=True)
        phase3.run_rank(_transcript(), workspace, settings=settings)
        phase3.run_rank(_transcript(), workspace, settings=settings, use_cache=False)
        assert len(stub_model["calls"]) == 2

    def test_changed_instructions_miss_the_cache(self, workspace, stub_model):
        # The key is the prompt hash, so a different question cannot be served a
        # stored answer.
        settings = Settings(rank_cache=True)
        phase3.run_rank(_transcript(), workspace, settings=settings)
        phase3.run_rank(
            _transcript(), workspace, settings=settings, instructions="only funny bits"
        )
        assert len(stub_model["calls"]) == 2

    def test_changed_duration_bounds_miss_the_cache(self, workspace, stub_model):
        phase3.run_rank(_transcript(), workspace, settings=Settings(rank_cache=True))
        phase3.run_rank(
            _transcript(), workspace,
            settings=Settings(rank_cache=True, max_clip_duration=45.0),
        )
        assert len(stub_model["calls"]) == 2

    def test_switching_provider_misses_the_cache(self, workspace, stub_model):
        settings = Settings(rank_cache=True)
        phase3.run_rank(_transcript(), workspace, settings=settings)
        phase3.run_rank(_transcript(), workspace, provider="gemini", settings=settings)
        assert [c[0] for c in stub_model["calls"]] == ["deepseek", "gemini"]


class TestCacheModule:
    def test_key_is_stable_and_prompt_sensitive(self):
        assert cache.cache_key("p", "deepseek", "m") == cache.cache_key("p", "deepseek", "m")
        assert cache.cache_key("p", "deepseek", "m") != cache.cache_key("q", "deepseek", "m")

    def test_provider_and_model_are_in_the_key(self):
        assert cache.cache_key("p", "deepseek", "m") != cache.cache_key("p", "gemini", "m")
        assert cache.cache_key("p", "deepseek", "m1") != cache.cache_key("p", "deepseek", "m2")

    def test_round_trips_a_payload(self, tmp_path):
        d = str(tmp_path / "c")
        cache.store(d, "k", {"clips": []}, provider="deepseek", model="m")
        assert cache.load(d, "k") == {"clips": []}

    def test_missing_entry_is_a_miss(self, tmp_path):
        assert cache.load(str(tmp_path / "c"), "absent") is None

    def test_replaces_an_existing_row(self, tmp_path):
        d = str(tmp_path / "c")
        cache.store(d, "k", {"v": 1}, provider="p", model="m")
        cache.store(d, "k", {"v": 2}, provider="p", model="m")
        assert cache.load(d, "k") == {"v": 2}

    def test_unserialisable_payload_does_not_fail_the_run(self, tmp_path):
        # A cache write failure must never cost a completed ranking.
        d = str(tmp_path / "c")
        cache.store(d, "k", {"bad": object()}, provider="p", model="m")
        assert cache.load(d, "k") is None

    def test_corrupt_database_is_a_miss(self, tmp_path):
        d = tmp_path / "c"
        d.mkdir()
        (d / cache.CACHE_FILENAME).write_bytes(b"not a database")
        assert cache.load(str(d), "k") is None


class TestProviderAdapters:
    def test_effective_model_defaults_per_provider(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        assert providers.effective_model(ranker_spec("deepseek")) == "deepseek-chat"
        assert providers.effective_model(ranker_spec("gemini")) == "gemini-3.5-flash"

    def test_effective_model_honours_the_env_override(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")
        assert providers.effective_model(ranker_spec("deepseek")) == "deepseek-reasoner"

    def test_missing_credential_names_the_variable_and_the_way_out(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
            providers.require_api_key(ranker_spec("deepseek"))

    def test_complete_checks_the_credential_before_the_network(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(ConfigError):
            providers.complete("prompt", "deepseek")
