"""Filesystem helpers for locating and relocating job artifacts written by main.py."""
import glob
import json
import logging
import os
import shutil
from typing import Tuple

logger = logging.getLogger("clippyme")


def find_job_metadata_path(job_id: str, output_dir: str) -> str:
    """Return the path to a job's ``*_metadata.json`` file.

    Raises ``FileNotFoundError`` if no metadata file exists.
    """
    job_dir = os.path.join(output_dir, job_id)
    matches = glob.glob(os.path.join(job_dir, "*_metadata.json"))
    if not matches:
        raise FileNotFoundError(f"Metadata not found for job {job_id}")
    # Newest-by-mtime, consistent with job_results._pick_latest_metadata. A bare
    # glob[0] is filesystem-order dependent, so when a job dir holds >1 metadata
    # file (e.g. a reprocess) smartcut/reframe could operate on a different file
    # than the user sees.
    return max(matches, key=os.path.getmtime)


def load_job_metadata(job_id: str, output_dir: str) -> Tuple[str, dict]:
    """Load a job's metadata JSON.

    Returns ``(metadata_path, data)``. Raises ``FileNotFoundError`` if the
    metadata file does not exist.
    """
    metadata_path = find_job_metadata_path(job_id, output_dir)
    with open(metadata_path, "r") as f:
        return metadata_path, json.load(f)


def save_job_metadata(metadata_path: str, data: dict) -> None:
    """Persist a job's metadata JSON back to disk atomically.

    Writes to a ``<metadata_path>.tmp`` sibling and ``os.replace()``s it
    into place so a crash / SIGKILL mid-write cannot leave the caller with
    a half-written or truncated JSON file (which would then fail to parse
    on the next load and silently wipe the job from the dashboard).
    """
    tmp_path = metadata_path + ".tmp"
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp_path, metadata_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


_CAMPAIGN_FILENAME = "campaign.json"


def save_job_campaign(job_output_dir: str, zernio_profile: str) -> None:
    """Tag a job with the Zernio profile (campaign) it was submitted under.

    A small sidecar next to the job's metadata, not a field inside it: the
    pipeline subprocess (main.py) writes/rewrites ``*_metadata.json`` on every
    cut iteration, and threading a new field through its argv/output would mean
    touching the cv2-bound pipeline for something the API layer already knows
    before the subprocess even starts. ``scan_history`` reads this back so
    ``GET /api/history`` can tell an external approval bot (one per campaign)
    which of ITS clips to post, instead of every bot posting every clip.
    """
    path = os.path.join(job_output_dir, _CAMPAIGN_FILENAME)
    tmp_path = path + ".tmp"
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"zernio_profile": zernio_profile}, f)
        os.replace(tmp_path, path)
    except OSError:
        # Best-effort: a job must not fail to submit over this tag. Missing
        # the sidecar just means load_job_campaign falls back to "default".
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def load_job_campaign(job_output_dir: str) -> str:
    """The Zernio profile (campaign) a job was submitted under — "default" if
    untagged (jobs submitted before this existed, or the sidecar is missing)."""
    path = os.path.join(job_output_dir, _CAMPAIGN_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        profile = data.get("zernio_profile")
        return profile if isinstance(profile, str) and profile else "default"
    except (OSError, json.JSONDecodeError, TypeError):
        return "default"


def record_clip_publish(job_id: str, clip_index: int, output_dir: str, record: dict) -> None:
    """Append a publish record onto a clip's metadata entry (atomic).

    Best-effort by design: callers should treat a failure here as non-fatal
    (the publish itself already succeeded) and just log it.
    """
    metadata_path, data = load_job_metadata(job_id, output_dir)
    shorts = data.get("shorts", [])
    if 0 <= clip_index < len(shorts):
        shorts[clip_index].setdefault("published", []).append(record)
        save_job_metadata(metadata_path, data)


def set_clip_field(metadata_path: str, clip_index: int, field: str, value) -> None:
    """Set one field on one clip's metadata entry via a fresh read-modify-write.

    Re-reads ``metadata_path`` from disk immediately before writing — never
    reuses a caller's in-memory snapshot — so a slow caller mutating one
    clip's entry can't clobber a concurrent write to a DIFFERENT clip's entry
    in the same file (e.g. Live Monitor consolidating clip N+1 while clip N
    composes). Same discipline as ``record_clip_publish``, generalized from
    "append to a list field" to "set an arbitrary field". Silently no-ops on
    a missing/unreadable metadata file or an out-of-range ``clip_index`` —
    there is simply nothing to persist, not a caller error.
    """
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    shorts = data.get("shorts", [])
    if 0 <= clip_index < len(shorts):
        shorts[clip_index][field] = value
        save_job_metadata(metadata_path, data)


def _safe_remove(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def delete_clip_artifacts(job_id: str, clip_index: int, output_dir: str,
                          clip_info: dict, clip_path: str) -> None:
    """Remove one published clip's on-disk files: the raw render, its
    preserved 16:9 source slice, cover image, and composed (subtitles/hook)
    output. Marks the entry ``deleted_after_publish`` instead of removing it
    from ``shorts`` — every per-clip endpoint keys clips by list position, so
    dropping an entry would shift every later clip's index.

    Best-effort and silent by design: call this only after a publish has
    already succeeded, so a cleanup hiccup here must never surface as a
    publish failure. Shared by the one-off publish flow and Live Monitor's
    auto-publish so the two don't duplicate (and drift on) this logic.
    """
    try:
        job_dir = os.path.join(output_dir, job_id)
        clip_filename = os.path.basename(clip_path)
        stem = os.path.splitext(clip_filename)[0]
        from clippyme.domain.clip_resolve import composed_clip_basename
        targets = [
            clip_path,
            os.path.join(job_dir, f"source_{clip_filename}"),
            os.path.join(job_dir, f"{stem}_cover.jpg"),
            os.path.join(job_dir, composed_clip_basename(clip_info, clip_index)),
        ]
        for path in targets:
            _safe_remove(path)
        metadata_path, data = load_job_metadata(job_id, output_dir)
        shorts = data.get("shorts", [])
        if 0 <= clip_index < len(shorts):
            shorts[clip_index]["deleted_after_publish"] = True
            save_job_metadata(metadata_path, data)
    except Exception:
        logger.warning("delete_clip_artifacts failed for %s/%d", job_id, clip_index, exc_info=True)


def relocate_root_job_artifacts(job_id: str, job_output_dir: str, output_dir: str) -> bool:
    """Backward-compat rescue.

    If ``main.py`` accidentally wrote metadata/clips into ``output_dir`` root
    (e.g. ``output/<jobid>_...``), move them into ``output/<job_id>/`` so the
    API can find and serve them.
    """
    try:
        os.makedirs(job_output_dir, exist_ok=True)
        pattern = os.path.join(output_dir, f"{job_id}_*_metadata.json")
        meta_candidates = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        if not meta_candidates:
            return False

        # Move the newest metadata and its associated clips.
        metadata_path = meta_candidates[0]
        base_name = os.path.basename(metadata_path).replace("_metadata.json", "")

        # Move metadata
        dest_metadata = os.path.join(job_output_dir, os.path.basename(metadata_path))
        if os.path.abspath(metadata_path) != os.path.abspath(dest_metadata):
            shutil.move(metadata_path, dest_metadata)

        # Move any clips that match the same base_name into the job folder
        clip_pattern = os.path.join(output_dir, f"{base_name}_clip_*.mp4")
        for clip_path in glob.glob(clip_pattern):
            dest_clip = os.path.join(job_output_dir, os.path.basename(clip_path))
            if os.path.abspath(clip_path) != os.path.abspath(dest_clip):
                shutil.move(clip_path, dest_clip)

        # Also move any temp_ clips that might remain
        temp_clip_pattern = os.path.join(output_dir, f"temp_{base_name}_clip_*.mp4")
        for clip_path in glob.glob(temp_clip_pattern):
            dest_clip = os.path.join(job_output_dir, os.path.basename(clip_path))
            if os.path.abspath(clip_path) != os.path.abspath(dest_clip):
                shutil.move(clip_path, dest_clip)

        return True
    except Exception as exc:
        logger.warning("relocate_root_job_artifacts failed for %s: %s", job_id, exc)
        return False
