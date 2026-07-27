"""Phase 5's speaker locator against the real cv2 + MediaPipe runtime.

Run in the backend image only::

    docker compose run --rm -u root backend sh -lc \
        "pip install -q pytest && pytest -m integration -k reframe_detect"

``test_reframe_detect.py`` covers every decision this module makes, with fakes.
What fakes cannot cover is whether the assumptions those fakes encode are still
true of the real runtime, which is exactly where this module has broken before:

* it imported ``detect_faces``, a name that does not exist in
  ``clippyme.pipeline.reframe_detect`` — every call raised ``ImportError``;
* it unpacked each detector result as a 4-sequence, but
  ``detect_face_candidates`` returns ``[{"box": [...], "score": …}]`` — slicing a
  dict raises ``TypeError``.

Both bugs are invisible to a fake and obvious here. So these tests assert the
*contract*, not the tracking quality: MediaPipe finds no face in a synthetic
test pattern, and shipping a real face as a fixture is not worth it — the
per-face maths is already covered on the host.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

pytestmark = pytest.mark.integration

from clipper_pro.reframe.detect import _face_box, locate_speakers
from clipper_pro.types import SpeakerSegment

_WIDTH, _HEIGHT, _FPS, _SECONDS = 640, 360, 30, 4


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> str:
    """A real, seekable h264 file — no faces in it, which is fine here."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on PATH")
    path = tmp_path_factory.mktemp("detect") / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"testsrc=size={_WIDTH}x{_HEIGHT}:rate={_FPS}:duration={_SECONDS}",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return str(path)


def test_the_mediapipe_helpers_exist_by_the_names_detect_imports():
    """The regression guard for the ``detect_faces`` ImportError.

    The host suite asserts this from source; here the real module is imported,
    so a name that exists but cannot load also fails.
    """
    from clippyme.pipeline.reframe_detect import (  # noqa: F401
        compute_mouth_aspect_ratio,
        detect_face_candidates,
    )


def test_real_detector_results_all_normalise(video):
    """Whatever ``detect_face_candidates`` returns, ``_face_box`` must accept it.

    The regression guard for the dict-vs-sequence ``TypeError``. On a synthetic
    pattern the detector usually finds nothing, so this only proves the shape
    when it does find something — the assertion that matters is that no result
    is ever rejected.
    """
    import cv2

    from clippyme.pipeline.reframe_detect import detect_face_candidates

    capture = cv2.VideoCapture(video)
    assert capture.isOpened()
    try:
        ok, frame = capture.read()
    finally:
        capture.release()
    assert ok and frame is not None

    faces = detect_face_candidates(frame) or []
    assert isinstance(faces, list)
    for face in faces:
        assert _face_box(face) is not None, f"detector result not understood: {face!r}"


def test_locate_speakers_returns_a_mapping_and_never_raises(video):
    """End-to-end on a real capture: a face-less video is "no positions", not an error."""
    positions = locate_speakers(
        video,
        [SpeakerSegment(0.0, 2.0, 1), SpeakerSegment(2.0, 4.0, 2)],
        samples_per_segment=6,
    )

    assert isinstance(positions, dict)
    for label, center in positions.items():
        assert label in (1, 2)
        assert 0.0 <= center <= _WIDTH


def test_locate_speakers_tolerates_a_missing_file():
    """An unopenable capture degrades to centred crops rather than failing phase 5."""
    assert locate_speakers("/nonexistent/video.mp4", [SpeakerSegment(0.0, 3.0, 1)]) == {}


def test_real_capture_supports_the_seeking_the_locator_relies_on(video):
    """The fakes assume fps > 0 and frame-index seeking; confirm both for real."""
    import cv2

    capture = cv2.VideoCapture(video)
    assert capture.isOpened()
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        assert fps > 0, "locate_speakers returns {} when fps is unknown"
        assert fps == pytest.approx(_FPS, abs=1.0)

        capture.set(cv2.CAP_PROP_POS_FRAMES, round(2.0 * fps))
        ok, frame = capture.read()
        assert ok and frame is not None, "mid-file seek must land on a decodable frame"
        assert frame.shape[:2] == (_HEIGHT, _WIDTH)
    finally:
        capture.release()
