"""Content-addressed transcript cache."""

from clipper_pro.transcribe import cache


def _audio(tmp_path, name="a.flac", data=b"AUDIO-BYTES"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


class TestCacheKey:
    def test_same_bytes_and_model_give_the_same_key(self, tmp_path):
        a = _audio(tmp_path, "a.flac")
        b = _audio(tmp_path, "b.flac")  # different path, identical bytes
        assert cache.cache_key(a, "deepgram", "nova-3") == cache.cache_key(
            b, "deepgram", "nova-3"
        )

    def test_different_bytes_give_different_keys(self, tmp_path):
        a = _audio(tmp_path, "a.flac", b"one")
        b = _audio(tmp_path, "b.flac", b"two")
        assert cache.cache_key(a, "deepgram", "nova-3") != cache.cache_key(
            b, "deepgram", "nova-3"
        )

    def test_provider_is_part_of_the_key(self, tmp_path):
        # Scribe tags audio events Nova-3 does not; serving one for the other
        # would silently change what phase 3 sees.
        path = _audio(tmp_path)
        assert cache.cache_key(path, "deepgram", "m") != cache.cache_key(
            path, "elevenlabs", "m"
        )

    def test_model_is_part_of_the_key(self, tmp_path):
        path = _audio(tmp_path)
        assert cache.cache_key(path, "elevenlabs", "scribe_v1") != cache.cache_key(
            path, "elevenlabs", "scribe_v2"
        )


class TestLoadStore:
    def test_round_trips_a_payload(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        cache.store(cache_dir, "k1", {"words": [{"text": "hi"}]})
        assert cache.load(cache_dir, "k1") == {"words": [{"text": "hi"}]}

    def test_creates_the_cache_directory(self, tmp_path):
        cache_dir = tmp_path / "nested" / "cache"
        cache.store(str(cache_dir), "k1", {"a": 1})
        assert cache_dir.is_dir()

    def test_missing_entry_is_a_miss(self, tmp_path):
        assert cache.load(str(tmp_path), "absent") is None

    def test_corrupt_entry_is_a_miss_not_a_crash(self, tmp_path):
        # Paying for the transcription again beats failing the run.
        cache_dir = str(tmp_path)
        with open(cache.cache_path(cache_dir, "k1"), "w") as fh:
            fh.write("{not json")
        assert cache.load(cache_dir, "k1") is None

    def test_non_object_payload_is_a_miss(self, tmp_path):
        cache_dir = str(tmp_path)
        with open(cache.cache_path(cache_dir, "k1"), "w") as fh:
            fh.write("[1, 2, 3]")
        assert cache.load(cache_dir, "k1") is None

    def test_store_overwrites_an_existing_entry(self, tmp_path):
        cache_dir = str(tmp_path)
        cache.store(cache_dir, "k1", {"v": 1})
        cache.store(cache_dir, "k1", {"v": 2})
        assert cache.load(cache_dir, "k1") == {"v": 2}

    def test_store_leaves_no_temp_file(self, tmp_path):
        cache_dir = str(tmp_path)
        path = cache.store(cache_dir, "k1", {"v": 1})
        assert not (tmp_path / (path + ".tmp")).exists()
