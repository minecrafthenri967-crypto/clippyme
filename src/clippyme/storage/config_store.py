"""Persistent configuration loader/saver for ClippyMe."""
import contextlib
import json
import logging
import os
import re
import tempfile
import threading

logger = logging.getLogger("clippyme")

DATA_DIR = "data"
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
VALID_CONFIG_KEYS = (
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "YOUTUBE_COOKIES",
    "HF_TOKEN",
    "DEEPGRAM_API_KEY",
    "ELEVENLABS_API_KEY",
    "TRANSCRIPTION_PROVIDER",
    "TWITCH_CLIENT_ID",
    "TWITCH_CLIENT_SECRET",
    "CLIPPYME_MAX_DOWNLOAD_HEIGHT",
)
ZERNIO_CONFIG_NAMESPACE = "zernio"
# Additional named Zernio accounts (multi-campaign / multi-profile setups)
# live in this sibling namespace, keyed by profile id. The "default" profile
# always reads/writes ZERNIO_CONFIG_NAMESPACE above — untouched — so a
# single-campaign install never sees this key at all.
ZERNIO_PROFILES_NAMESPACE = "zernio_profiles"
DEFAULT_ZERNIO_PROFILE = "default"
MAX_ZERNIO_PROFILES = 20
_PROFILE_ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
# Saved caption templates (e.g. one per seller in a multi-account clipping
# campaign) so a mandatory hashtag/mention block doesn't need retyping per
# clip at publish time — pick a preset, its text fills the caption field,
# still freely editable afterward.
CAPTION_PRESETS_NAMESPACE = "caption_presets"
MAX_CAPTION_PRESETS = 50
_CAPTION_PRESET_ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
_CAPTION_PRESET_TEXT_MAX = 2200  # mirrors PublishRequest.caption's max_length
_CAPTION_PRESET_LABEL_MAX = 60
_CONFIG_LOCK = threading.RLock()


def _read_raw_config() -> dict:
    with _CONFIG_LOCK:
        if not os.path.exists(CONFIG_FILE):
            return {}
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as file:
                data = json.load(file) or {}
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("Error reading config.json: %s", exc)
            return {}


def _write_raw_config(data: dict) -> bool:
    """Atomically replace config.json with owner-only permissions.

    Writing to a sibling temporary file and then ``os.replace`` prevents a
    crash, disk-full condition, or concurrent reader from observing a
    half-truncated JSON document containing secrets.
    """
    with _CONFIG_LOCK:
        tmp_path = None
        try:
            os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
            with contextlib.suppress(OSError):
                os.chmod(DATA_DIR, 0o700)

            fd, tmp_path = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=DATA_DIR)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(data, file, indent=4)
                    file.flush()
                    os.fsync(file.fileno())
                with contextlib.suppress(OSError):
                    os.chmod(tmp_path, 0o600)
                os.replace(tmp_path, CONFIG_FILE)
                tmp_path = None
                with contextlib.suppress(OSError):
                    os.chmod(CONFIG_FILE, 0o600)
                # Persist the rename itself on POSIX filesystems when possible.
                try:
                    dir_fd = os.open(DATA_DIR, os.O_RDONLY)
                except OSError:
                    dir_fd = None
                if dir_fd is not None:
                    try:
                        os.fsync(dir_fd)
                    except OSError:
                        pass
                    finally:
                        os.close(dir_fd)
                return True
            except Exception:
                # fdopen owns/closes fd once entered; close only if creation
                # failed before that ownership transfer.
                with contextlib.suppress(OSError):
                    os.close(fd)
                raise
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Error writing config.json: %s", exc)
            return False
        finally:
            if tmp_path:
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)


def _read_zernio_profile_entry(raw: dict, profile: str) -> dict:
    """Return the raw (unvalidated) stored dict for ``profile``, `{}` if unset.

    ``profile == "default"`` reads the original top-level ``zernio`` key —
    byte-for-byte the same lookup as before profiles existed. Anything else
    reads the ``zernio_profiles`` sibling namespace.
    """
    if profile == DEFAULT_ZERNIO_PROFILE:
        entry = raw.get(ZERNIO_CONFIG_NAMESPACE) or {}
    else:
        profiles = raw.get(ZERNIO_PROFILES_NAMESPACE) or {}
        entry = profiles.get(profile) if isinstance(profiles, dict) else None
        entry = entry or {}
    return entry if isinstance(entry, dict) else {}


def load_zernio_config(profile: str = DEFAULT_ZERNIO_PROFILE) -> dict:
    raw = _read_raw_config()
    zernio = _read_zernio_profile_entry(raw, profile)
    accounts = zernio.get("accounts", {})
    return {
        "api_key": zernio.get("api_key", ""),
        "accounts": accounts if isinstance(accounts, dict) else {},
        "timezone": zernio.get("timezone", "Europe/Rome"),
    }


def save_zernio_config(api_key: str = None, accounts: dict = None, timezone: str = None, *,
                        profile: str = DEFAULT_ZERNIO_PROFILE, label: str = None) -> bool:
    """Merge-update one Zernio profile's settings as one locked read-modify-write."""
    with _CONFIG_LOCK:
        raw = _read_raw_config()
        current = dict(_read_zernio_profile_entry(raw, profile))
        if api_key is not None:
            if api_key == "":
                current.pop("api_key", None)
            else:
                current["api_key"] = api_key
        if accounts is not None:
            merged = current.get("accounts") or {}
            if not isinstance(merged, dict):
                merged = {}
            for key, value in accounts.items():
                if value in (None, ""):
                    merged.pop(key, None)
                else:
                    merged[key] = value
            current["accounts"] = merged
        if timezone is not None:
            current["timezone"] = timezone
        if label is not None:
            current["label"] = label
        if profile == DEFAULT_ZERNIO_PROFILE:
            raw[ZERNIO_CONFIG_NAMESPACE] = current
        else:
            profiles = raw.get(ZERNIO_PROFILES_NAMESPACE) or {}
            if not isinstance(profiles, dict):
                profiles = {}
            profiles[profile] = current
            raw[ZERNIO_PROFILES_NAMESPACE] = profiles
        return _write_raw_config(raw)


def zernio_config_status(profile: str = DEFAULT_ZERNIO_PROFILE) -> dict:
    cfg = load_zernio_config(profile=profile)
    api_key = cfg.get("api_key", "")
    masked = f"{api_key[:6]}...{api_key[-4:]}" if api_key and len(api_key) > 10 else ""
    return {
        "configured": bool(api_key),
        "api_key_masked": masked,
        "accounts": cfg.get("accounts", {}),
        "timezone": cfg.get("timezone", "Europe/Rome"),
    }


def list_zernio_profiles() -> list[dict]:
    """Every configured Zernio profile, ``"default"`` always first."""
    raw = _read_raw_config()
    default_entry = _read_zernio_profile_entry(raw, DEFAULT_ZERNIO_PROFILE)
    result = [{
        "id": DEFAULT_ZERNIO_PROFILE,
        "label": default_entry.get("label") or "Default",
        "configured": bool(default_entry.get("api_key")),
    }]
    profiles = raw.get(ZERNIO_PROFILES_NAMESPACE) or {}
    if isinstance(profiles, dict):
        for profile_id in sorted(profiles):
            entry = profiles[profile_id]
            if not isinstance(entry, dict):
                entry = {}
            result.append({
                "id": profile_id,
                "label": entry.get("label") or profile_id,
                "configured": bool(entry.get("api_key")),
            })
    return result


def create_zernio_profile(profile: str, label: str = None) -> bool:
    """Register a new, empty Zernio profile. Rejects ``"default"`` (already
    exists implicitly) and duplicate/invalid ids."""
    if profile == DEFAULT_ZERNIO_PROFILE:
        raise ValueError("'default' already exists and cannot be recreated")
    if not _PROFILE_ID_RE.match(profile or ""):
        raise ValueError(
            "profile id must match ^[a-z0-9_-]{1,32}$")
    with _CONFIG_LOCK:
        raw = _read_raw_config()
        profiles = raw.get(ZERNIO_PROFILES_NAMESPACE) or {}
        if not isinstance(profiles, dict):
            profiles = {}
        if profile in profiles:
            raise ValueError(f"profile already exists: {profile}")
        if len(profiles) >= MAX_ZERNIO_PROFILES:
            raise ValueError(f"maximum of {MAX_ZERNIO_PROFILES} profiles reached")
        entry = {}
        if label:
            entry["label"] = label
        profiles[profile] = entry
        raw[ZERNIO_PROFILES_NAMESPACE] = profiles
        return _write_raw_config(raw)


def delete_zernio_profile(profile: str) -> bool:
    """Remove a non-default profile. Returns False if it did not exist."""
    if profile == DEFAULT_ZERNIO_PROFILE:
        raise ValueError("the 'default' profile cannot be deleted")
    with _CONFIG_LOCK:
        raw = _read_raw_config()
        profiles = raw.get(ZERNIO_PROFILES_NAMESPACE) or {}
        if not isinstance(profiles, dict) or profile not in profiles:
            return False
        del profiles[profile]
        raw[ZERNIO_PROFILES_NAMESPACE] = profiles
        return _write_raw_config(raw)


def list_caption_presets() -> list[dict]:
    """Every saved caption preset, sorted by id for a stable display order."""
    raw = _read_raw_config()
    presets = raw.get(CAPTION_PRESETS_NAMESPACE) or {}
    if not isinstance(presets, dict):
        return []
    result = []
    for preset_id in sorted(presets):
        entry = presets[preset_id]
        if not isinstance(entry, dict):
            continue
        result.append({
            "id": preset_id,
            "label": entry.get("label") or preset_id,
            "text": entry.get("text", ""),
        })
    return result


def save_caption_preset(preset_id: str, label: str, text: str) -> bool:
    """Create or update (upsert by id) a saved caption template."""
    if not _CAPTION_PRESET_ID_RE.match(preset_id or ""):
        raise ValueError("preset id must match ^[a-z0-9_-]{1,40}$")
    label = (label or "").strip()[:_CAPTION_PRESET_LABEL_MAX]
    if not label:
        raise ValueError("label must not be blank")
    text = (text or "")[:_CAPTION_PRESET_TEXT_MAX]
    with _CONFIG_LOCK:
        raw = _read_raw_config()
        presets = raw.get(CAPTION_PRESETS_NAMESPACE) or {}
        if not isinstance(presets, dict):
            presets = {}
        if preset_id not in presets and len(presets) >= MAX_CAPTION_PRESETS:
            raise ValueError(f"maximum of {MAX_CAPTION_PRESETS} caption presets reached")
        presets[preset_id] = {"label": label, "text": text}
        raw[CAPTION_PRESETS_NAMESPACE] = presets
        return _write_raw_config(raw)


def delete_caption_preset(preset_id: str) -> bool:
    """Remove a saved caption preset. Returns False if it did not exist."""
    with _CONFIG_LOCK:
        raw = _read_raw_config()
        presets = raw.get(CAPTION_PRESETS_NAMESPACE) or {}
        if not isinstance(presets, dict) or preset_id not in presets:
            return False
        del presets[preset_id]
        raw[CAPTION_PRESETS_NAMESPACE] = presets
        return _write_raw_config(raw)


def _normalize_incoming_keys(data: dict) -> dict:
    if not data:
        return {}
    out = dict(data)
    if "HUGGINGFACE_TOKEN" in out and not out.get("HF_TOKEN"):
        out["HF_TOKEN"] = out.pop("HUGGINGFACE_TOKEN")
    else:
        out.pop("HUGGINGFACE_TOKEN", None)
    return out


def load_persistent_config() -> dict:
    config = {
        "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY", ""),
        "GEMINI_MODEL": os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        "YOUTUBE_COOKIES": os.environ.get("YOUTUBE_COOKIES", ""),
        "HF_TOKEN": os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or "",
        "DEEPGRAM_API_KEY": os.environ.get("DEEPGRAM_API_KEY", ""),
        "ELEVENLABS_API_KEY": os.environ.get("ELEVENLABS_API_KEY", ""),
        "TRANSCRIPTION_PROVIDER": os.environ.get("TRANSCRIPTION_PROVIDER", "deepgram"),
        "TWITCH_CLIENT_ID": os.environ.get("TWITCH_CLIENT_ID", ""),
        "TWITCH_CLIENT_SECRET": os.environ.get("TWITCH_CLIENT_SECRET", ""),
        "CLIPPYME_MAX_DOWNLOAD_HEIGHT": os.environ.get("CLIPPYME_MAX_DOWNLOAD_HEIGHT", "1080"),
    }
    raw = _read_raw_config()
    config.update({key: value for key, value in raw.items() if key in VALID_CONFIG_KEYS})
    return config


def save_persistent_config(new_config: dict) -> bool:
    """Persist core keys without racing the separate Zernio namespace."""
    with _CONFIG_LOCK:
        raw = _read_raw_config()
        normalized = _normalize_incoming_keys(new_config)
        sanitized = {
            key: normalized.get(key) for key in VALID_CONFIG_KEYS if key in normalized
        }
        for key, value in sanitized.items():
            if value in (None, ""):
                raw.pop(key, None)
            else:
                raw[key] = value
        if not _write_raw_config(raw):
            return False
        for key, value in sanitized.items():
            if value in (None, ""):
                os.environ.pop(key, None)
                if key == "HF_TOKEN":
                    os.environ.pop("HUGGINGFACE_TOKEN", None)
            else:
                os.environ[key] = str(value)
                if key == "HF_TOKEN":
                    os.environ["HUGGINGFACE_TOKEN"] = str(value)
        return True
