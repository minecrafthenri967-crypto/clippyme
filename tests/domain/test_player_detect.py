"""Host-unit + mocked-client tests for the player-image detection call."""
import pytest

from clippyme.domain import player_detect as pd

TRANSCRIPT = {
    "segments": [
        {"words": [
            {"word": "and", "start": 10.0, "end": 10.2},
            {"word": "he", "start": 10.2, "end": 10.3},
            {"word": "pulls", "start": 10.3, "end": 10.6},
            {"word": "LeBron", "start": 10.6, "end": 11.0},
            {"word": "James", "start": 11.0, "end": 11.4},
        ]},
    ],
}


# --- _extract_clip_words -----------------------------------------------------

def test_extract_clip_words_filters_and_rebases():
    words = pd._extract_clip_words(TRANSCRIPT, clip_start=10.0, clip_end=20.0)
    assert [w["w"] for w in words] == ["and", "he", "pulls", "LeBron", "James"]
    assert words[0]["s"] == 0.0
    assert words[-1]["e"] == pytest.approx(1.4)


def test_extract_clip_words_excludes_words_outside_window():
    # Half-open window [clip_start, clip_end) — same convention as
    # smartcut_ops.analyze_silences's own word filter (end > start AND
    # start < end). "LeBron" ends exactly at 11.0 so it's excluded.
    words = pd._extract_clip_words(TRANSCRIPT, clip_start=11.0, clip_end=20.0)
    assert [w["w"] for w in words] == ["James"]


def test_extract_clip_words_empty_transcript():
    assert pd._extract_clip_words({}, 0, 10) == []
    assert pd._extract_clip_words(None, 0, 10) == []


def test_extract_clip_words_caps_at_max():
    big_transcript = {"segments": [{"words": [
        {"word": f"w{i}", "start": float(i), "end": float(i) + 0.5} for i in range(1000)
    ]}]}
    words = pd._extract_clip_words(big_transcript, 0, 1000)
    assert len(words) == pd.MAX_CLIP_WORDS


# --- build_player_prompt -----------------------------------------------------

def test_build_player_prompt_contains_delimiter_and_words():
    words = [{"w": "LeBron", "s": 0.0, "e": 0.4}]
    prompt = pd.build_player_prompt(words, 12.5)
    assert "### JSON ###" in prompt
    assert "LeBron" in prompt
    assert "12.50 seconds" in prompt


# --- build_player_reformat_prompt --------------------------------------------

def test_build_player_reformat_prompt_contains_error_and_own_schema():
    p = pd.build_player_reformat_prompt("Expecting value: line 1", "{broken")
    assert "Expecting value" in p
    assert "{broken" in p
    assert '"mentions"' in p
    assert "shorts" not in p  # must not leak the viral-clip schema


# --- validate_player_mentions -------------------------------------------------

def test_validate_player_mentions_happy_path():
    data = {"mentions": [{"player_name": "LeBron James", "timestamp": 5.0, "confidence": 0.9}]}
    result = pd.validate_player_mentions(data, clip_duration=10.0)
    assert result == [{"player_name": "LeBron James", "timestamp": 5.0, "confidence": 0.9}]


def test_validate_player_mentions_drops_invalid_confidence():
    data = {"mentions": [
        {"player_name": "A", "timestamp": 1.0, "confidence": 1.5},
        {"player_name": "B", "timestamp": 2.0, "confidence": 0.5},
    ]}
    result = pd.validate_player_mentions(data, clip_duration=10.0)
    assert [m["player_name"] for m in result] == ["B"]


def test_validate_player_mentions_clamps_out_of_range_timestamp():
    data = {"mentions": [{"player_name": "A", "timestamp": 99.0, "confidence": 0.5}]}
    result = pd.validate_player_mentions(data, clip_duration=10.0)
    assert result[0]["timestamp"] == 10.0


def test_validate_player_mentions_missing_mentions_key():
    assert pd.validate_player_mentions({}, clip_duration=10.0) == []
    assert pd.validate_player_mentions(None, clip_duration=10.0) == []


def test_validate_player_mentions_caps_at_max():
    data = {"mentions": [
        {"player_name": f"P{i}", "timestamp": 1.0, "confidence": 0.5} for i in range(30)
    ]}
    result = pd.validate_player_mentions(data, clip_duration=100.0)
    assert len(result) == pd.MAX_MENTIONS


# --- detect_player_mentions (mocked genai.Client) -----------------------------

class _Response:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        if not self._responses:
            raise RuntimeError("no more fake responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Response(item)


class _FakeClient:
    def __init__(self, models):
        self.models = models


def test_detect_player_mentions_missing_api_key_raises():
    with pytest.raises(ValueError):
        pd.detect_player_mentions(
            api_key="", model="gemini-3.5-flash", transcript=TRANSCRIPT,
            clip_start=10.0, clip_end=20.0)


def test_detect_player_mentions_no_words_returns_empty_without_calling_gemini(monkeypatch):
    called = []
    monkeypatch.setattr(
        pd, "generate_with_model_fallback",
        lambda *a, **k: (called.append(1), (_Response("x"), "m"))[1])
    result = pd.detect_player_mentions(
        api_key="key", model="gemini-3.5-flash", transcript={}, clip_start=0, clip_end=10)
    assert result == []
    assert called == []


def test_detect_player_mentions_happy_path(monkeypatch):
    text = '### JSON ###\n{"mentions": [{"player_name": "LeBron James", "timestamp": 0.6, "confidence": 0.9}]}'
    models = _FakeModels([text])
    monkeypatch.setattr("google.genai.Client", lambda api_key: _FakeClient(models))
    result = pd.detect_player_mentions(
        api_key="key", model="gemini-3.5-flash", transcript=TRANSCRIPT,
        clip_start=10.0, clip_end=20.0)
    assert result == [{"player_name": "LeBron James", "timestamp": 0.6, "confidence": 0.9}]


def test_detect_player_mentions_malformed_json_triggers_retry(monkeypatch):
    broken = "not json at all, no delimiter, no braces"
    reformatted = '{"mentions": []}'
    models = _FakeModels([broken, reformatted])
    monkeypatch.setattr("google.genai.Client", lambda api_key: _FakeClient(models))
    result = pd.detect_player_mentions(
        api_key="key", model="gemini-3.5-flash", transcript=TRANSCRIPT,
        clip_start=10.0, clip_end=20.0)
    assert result == []
    assert models.calls == ["gemini-3.5-flash", "gemini-3.5-flash"]  # primary + retry


def test_detect_player_mentions_network_failure_raises(monkeypatch):
    models = _FakeModels([RuntimeError("401 unauthorized")])
    monkeypatch.setattr("google.genai.Client", lambda api_key: _FakeClient(models))
    with pytest.raises(RuntimeError):
        pd.detect_player_mentions(
            api_key="key", model="gemini-3.5-flash", transcript=TRANSCRIPT,
            clip_start=10.0, clip_end=20.0)
