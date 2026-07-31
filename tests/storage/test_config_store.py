"""Tests for clippyme.storage.config_store.

Covers core config round-trip, env-var fallback, HF_TOKEN aliasing, Zernio
namespace isolation, masking, and corrupt/missing-file resilience. All I/O is
redirected to a tmp file via monkeypatch so the real data/config.json is never
touched.
"""
import json
import os

import pytest

from clippyme.storage import config_store


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Point config_store at an isolated tmp config file."""
    data_dir = tmp_path / "data"
    cfg = data_dir / "config.json"
    monkeypatch.setattr(config_store, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(config_store, "CONFIG_FILE", str(cfg))
    return cfg


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions only")
def test_write_config_enforces_owner_only_perms(tmp_config):
    # Pre-create the file world-readable to simulate a legacy/umask-widened
    # config.json; the write must clamp it back to 0o600 (M5 regression).
    tmp_config.parent.mkdir(parents=True, exist_ok=True)
    tmp_config.write_text("{}")
    os.chmod(str(tmp_config), 0o644)
    assert config_store._write_raw_config({"GEMINI_API_KEY": "secret"}) is True
    mode = os.stat(str(tmp_config)).st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_read_raw_missing_file_returns_empty(tmp_config):
    assert config_store._read_raw_config() == {}


def test_read_raw_corrupt_json_returns_empty(tmp_config):
    tmp_config.parent.mkdir(parents=True, exist_ok=True)
    tmp_config.write_text("{not valid json")
    assert config_store._read_raw_config() == {}


def test_save_and_load_core_keys(tmp_config, monkeypatch):
    # Clear env so file values are what we read back.
    for k in config_store.VALID_CONFIG_KEYS:
        monkeypatch.delenv(k, raising=False)
    assert config_store.save_persistent_config({"GEMINI_API_KEY": "g-key"}) is True
    loaded = config_store.load_persistent_config()
    assert loaded["GEMINI_API_KEY"] == "g-key"
    # File on disk only stores known keys.
    on_disk = json.loads(tmp_config.read_text())
    assert on_disk["GEMINI_API_KEY"] == "g-key"


def test_unknown_keys_are_dropped(tmp_config, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config_store.save_persistent_config({"GEMINI_API_KEY": "g", "BOGUS": "x"})
    on_disk = json.loads(tmp_config.read_text())
    assert "BOGUS" not in on_disk


def test_env_fallback_when_no_file(tmp_config, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    loaded = config_store.load_persistent_config()
    assert loaded["GEMINI_API_KEY"] == "from-env"


def test_hf_token_alias_normalization(tmp_config, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    config_store.save_persistent_config({"HUGGINGFACE_TOKEN": "hf-123"})
    loaded = config_store.load_persistent_config()
    assert loaded["HF_TOKEN"] == "hf-123"
    # Mirrored into the long form for libraries that only read it.
    assert os.environ.get("HUGGINGFACE_TOKEN") == "hf-123"


def test_clearing_key_with_empty_string(tmp_config, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config_store.save_persistent_config({"GEMINI_API_KEY": "g"})
    config_store.save_persistent_config({"GEMINI_API_KEY": ""})
    on_disk = json.loads(tmp_config.read_text())
    assert "GEMINI_API_KEY" not in on_disk


def test_zernio_namespace_isolated_from_core(tmp_config, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config_store.save_persistent_config({"GEMINI_API_KEY": "g"})
    config_store.save_zernio_config(api_key="sk_secretkey_1234", timezone="Europe/Rome")
    # Updating core config must NOT wipe the zernio namespace.
    config_store.save_persistent_config({"GEMINI_API_KEY": "g2"})
    z = config_store.load_zernio_config()
    assert z["api_key"] == "sk_secretkey_1234"
    assert z["timezone"] == "Europe/Rome"


def test_zernio_accounts_merge_and_clear(tmp_config):
    config_store.save_zernio_config(accounts={"tiktok": "tt1", "youtube": "yt1"})
    config_store.save_zernio_config(accounts={"youtube": ""})  # clear youtube only
    z = config_store.load_zernio_config()
    assert z["accounts"] == {"tiktok": "tt1"}


def test_zernio_status_masks_key(tmp_config):
    config_store.save_zernio_config(api_key="sk_abcdef_longenough_key")
    status = config_store.zernio_config_status()
    assert status["configured"] is True
    assert "..." in status["api_key_masked"]
    assert "longenough" not in status["api_key_masked"]


def test_zernio_status_unconfigured(tmp_config):
    status = config_store.zernio_config_status()
    assert status["configured"] is False
    assert status["api_key_masked"] == ""


def test_failed_atomic_write_preserves_previous_config(tmp_config, monkeypatch):
    tmp_config.parent.mkdir(parents=True, exist_ok=True)
    tmp_config.write_text('{"GEMINI_API_KEY":"old"}', encoding="utf-8")
    real_dump = config_store.json.dump

    def fail_dump(data, file, *args, **kwargs):
        file.write("{")
        raise OSError("disk full")

    monkeypatch.setattr(config_store.json, "dump", fail_dump)
    assert config_store._write_raw_config({"GEMINI_API_KEY": "new"}) is False
    monkeypatch.setattr(config_store.json, "dump", real_dump)
    assert json.loads(tmp_config.read_text()) == {"GEMINI_API_KEY": "old"}
    assert not list(tmp_config.parent.glob(".config-*.tmp"))


def test_concurrent_core_and_zernio_updates_do_not_lose_namespaces(tmp_config, monkeypatch):
    import threading

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    barrier = threading.Barrier(3)

    def save_core():
        barrier.wait()
        for index in range(20):
            assert config_store.save_persistent_config({"GEMINI_API_KEY": f"g{index}"})

    def save_zernio():
        barrier.wait()
        for index in range(20):
            assert config_store.save_zernio_config(api_key=f"z{index}")

    threads = [threading.Thread(target=save_core), threading.Thread(target=save_zernio)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    raw = json.loads(tmp_config.read_text())
    assert raw["GEMINI_API_KEY"].startswith("g")
    assert raw["zernio"]["api_key"].startswith("z")


# --- Zernio profiles ---------------------------------------------------------


def test_default_profile_is_byte_identical_to_legacy_zernio_key(tmp_config):
    """profile='default' (the implicit default) must read/write the exact same
    top-level 'zernio' key as before profiles existed — no migration needed."""
    config_store.save_zernio_config(api_key="sk_default_key", timezone="Europe/Rome")
    raw = json.loads(tmp_config.read_text())
    assert raw["zernio"]["api_key"] == "sk_default_key"
    assert "zernio_profiles" not in raw
    assert config_store.load_zernio_config(profile="default")["api_key"] == "sk_default_key"


def test_named_profile_isolated_from_default_and_from_each_other(tmp_config):
    config_store.save_zernio_config(api_key="sk_default")
    config_store.create_zernio_profile("ebay_live", label="eBay Live")
    config_store.save_zernio_config(api_key="sk_ebay", profile="ebay_live")

    assert config_store.load_zernio_config()["api_key"] == "sk_default"
    assert config_store.load_zernio_config(profile="ebay_live")["api_key"] == "sk_ebay"
    raw = json.loads(tmp_config.read_text())
    assert raw["zernio"]["api_key"] == "sk_default"
    assert raw["zernio_profiles"]["ebay_live"]["api_key"] == "sk_ebay"


def test_save_zernio_config_for_unregistered_profile_creates_it_implicitly(tmp_config):
    # save_zernio_config is content-only; profile *registration* (create/delete/
    # list) is a separate concern layered on top, so saving to a profile id
    # that was never explicitly created still works.
    assert config_store.save_zernio_config(api_key="sk_x", profile="adhoc") is True
    assert config_store.load_zernio_config(profile="adhoc")["api_key"] == "sk_x"


def test_create_zernio_profile_rejects_default(tmp_config):
    with pytest.raises(ValueError):
        config_store.create_zernio_profile("default")


def test_create_zernio_profile_rejects_invalid_id(tmp_config):
    with pytest.raises(ValueError):
        config_store.create_zernio_profile("Has Spaces")
    with pytest.raises(ValueError):
        config_store.create_zernio_profile("")


def test_create_zernio_profile_rejects_duplicate(tmp_config):
    config_store.create_zernio_profile("ebay_live")
    with pytest.raises(ValueError):
        config_store.create_zernio_profile("ebay_live")


def test_create_zernio_profile_enforces_max_profiles(tmp_config, monkeypatch):
    monkeypatch.setattr(config_store, "MAX_ZERNIO_PROFILES", 2)
    config_store.create_zernio_profile("p1")
    config_store.create_zernio_profile("p2")
    with pytest.raises(ValueError):
        config_store.create_zernio_profile("p3")


def test_delete_zernio_profile_rejects_default(tmp_config):
    with pytest.raises(ValueError):
        config_store.delete_zernio_profile("default")


def test_delete_zernio_profile_removes_it(tmp_config):
    config_store.create_zernio_profile("ebay_live")
    config_store.save_zernio_config(api_key="sk_ebay", profile="ebay_live")
    assert config_store.delete_zernio_profile("ebay_live") is True
    raw = json.loads(tmp_config.read_text())
    assert "ebay_live" not in raw.get("zernio_profiles", {})


def test_delete_zernio_profile_missing_returns_false(tmp_config):
    assert config_store.delete_zernio_profile("nope") is False


def test_list_zernio_profiles_always_includes_default_first(tmp_config):
    profiles = config_store.list_zernio_profiles()
    assert profiles == [{"id": "default", "label": "Default", "configured": False}]


def test_list_zernio_profiles_includes_named_profiles(tmp_config):
    config_store.save_zernio_config(api_key="sk_default")
    config_store.create_zernio_profile("ebay_live", label="eBay Live")
    config_store.create_zernio_profile("aaa_first")
    profiles = config_store.list_zernio_profiles()
    assert profiles[0] == {"id": "default", "label": "Default", "configured": True}
    ids = [p["id"] for p in profiles]
    assert ids == ["default", "aaa_first", "ebay_live"]  # non-default sorted
    ebay = next(p for p in profiles if p["id"] == "ebay_live")
    assert ebay["label"] == "eBay Live"
    assert ebay["configured"] is False


# --- Caption presets ---------------------------------------------------------


def test_list_caption_presets_empty_by_default(tmp_config):
    assert config_store.list_caption_presets() == []


def test_save_and_list_caption_preset_roundtrip(tmp_config):
    config_store.save_caption_preset(
        "johns_breaks", "John's Card Breaks",
        "#eBayLive #JohnsCardBreaks @johnscardbreaks #tradingcards #breaks")
    presets = config_store.list_caption_presets()
    assert len(presets) == 1
    assert presets[0]["id"] == "johns_breaks"
    assert presets[0]["label"] == "John's Card Breaks"
    assert "#eBayLive" in presets[0]["text"]


def test_save_caption_preset_upserts_by_id(tmp_config):
    config_store.save_caption_preset("seller1", "Seller One", "first text")
    config_store.save_caption_preset("seller1", "Seller One Renamed", "second text")
    presets = config_store.list_caption_presets()
    assert len(presets) == 1
    assert presets[0]["label"] == "Seller One Renamed"
    assert presets[0]["text"] == "second text"


def test_save_caption_preset_rejects_invalid_id(tmp_config):
    with pytest.raises(ValueError):
        config_store.save_caption_preset("Has Spaces", "Label", "text")
    with pytest.raises(ValueError):
        config_store.save_caption_preset("", "Label", "text")


def test_save_caption_preset_rejects_blank_label(tmp_config):
    with pytest.raises(ValueError):
        config_store.save_caption_preset("seller1", "   ", "text")


def test_save_caption_preset_truncates_overlong_text_and_label(tmp_config):
    config_store.save_caption_preset("seller1", "x" * 200, "y" * 5000)
    preset = config_store.list_caption_presets()[0]
    assert len(preset["label"]) == config_store._CAPTION_PRESET_LABEL_MAX
    assert len(preset["text"]) == config_store._CAPTION_PRESET_TEXT_MAX


def test_save_caption_preset_enforces_max_count(tmp_config, monkeypatch):
    monkeypatch.setattr(config_store, "MAX_CAPTION_PRESETS", 2)
    config_store.save_caption_preset("p1", "P1", "t1")
    config_store.save_caption_preset("p2", "P2", "t2")
    with pytest.raises(ValueError):
        config_store.save_caption_preset("p3", "P3", "t3")
    # Updating an existing preset must not be blocked by the cap.
    assert config_store.save_caption_preset("p1", "P1 updated", "t1b") is True


def test_delete_caption_preset_removes_it(tmp_config):
    config_store.save_caption_preset("seller1", "Seller One", "text")
    assert config_store.delete_caption_preset("seller1") is True
    assert config_store.list_caption_presets() == []


def test_delete_caption_preset_missing_returns_false(tmp_config):
    assert config_store.delete_caption_preset("nope") is False


def test_caption_presets_are_isolated_from_zernio_and_core_config(tmp_config, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config_store.save_persistent_config({"GEMINI_API_KEY": "g"})
    config_store.save_zernio_config(api_key="sk_default")
    config_store.save_caption_preset("seller1", "Seller One", "text")
    raw = json.loads(tmp_config.read_text())
    assert raw["GEMINI_API_KEY"] == "g"
    assert raw["zernio"]["api_key"] == "sk_default"
    assert raw["caption_presets"]["seller1"]["label"] == "Seller One"


def test_zernio_config_status_accepts_profile(tmp_config):
    config_store.create_zernio_profile("ebay_live")
    config_store.save_zernio_config(api_key="sk_abcdef_longenough_key", profile="ebay_live")
    status = config_store.zernio_config_status(profile="ebay_live")
    assert status["configured"] is True
    assert "..." in status["api_key_masked"]
    # Default profile is untouched.
    assert config_store.zernio_config_status()["configured"] is False
