"""Phase 5's speaker locator — the cv2/MediaPipe half, without cv2.

``clipper_pro.reframe.detect`` imports cv2 and the MediaPipe detectors
*call-scoped*, so the module is importable on a host and its decision logic is
reachable with fakes injected into ``sys.modules``. What the fakes deliberately
reproduce is the **real** detector contract: ``detect_face_candidates`` returns
``[{"box": [x, y, w, h], "score": …}]``, not bare 4-sequences. A fake that
returned tuples would pass while the production path raised ``TypeError``.

``test_import_contract`` closes the other half of that gap by asserting the
names this module imports actually exist in ``clippyme.pipeline.reframe_detect``
— parsed from source, so it needs no CV runtime. The Docker integration suite
(``test_reframe_detect_integration.py``) exercises the real thing end to end.
"""

import ast
import sys
import types
from pathlib import Path

import pytest

from clipper_pro.reframe.detect import (
    DEFAULT_SAMPLES_PER_SEGMENT,
    _face_box,
    locate_speakers,
)
from clipper_pro.types import SpeakerSegment

# --- fakes -----------------------------------------------------------------

_CAP_PROP_FPS = 5
_CAP_PROP_POS_FRAMES = 1


class _Frame:
    """Stands in for a decoded BGR frame; carries only which sample it is."""

    def __init__(self, index: int) -> None:
        self.index = index


class _FakeCapture:
    """Minimal ``cv2.VideoCapture``: honours the seek, hands back indexed frames."""

    def __init__(self, *, fps: float = 30.0, opened: bool = True, unreadable=()):
        self.fps = fps
        self.released = False
        self.seeks: list[int] = []
        self._opened = opened
        self._unreadable = set(unreadable)
        self._pos = 0

    def isOpened(self) -> bool:
        return self._opened

    def get(self, prop):
        assert prop == _CAP_PROP_FPS
        return self.fps

    def set(self, prop, value):
        assert prop == _CAP_PROP_POS_FRAMES
        self._pos = int(value)
        self.seeks.append(self._pos)

    def read(self):
        if self._pos in self._unreadable:
            return False, None
        return True, _Frame(self._pos)

    def release(self):
        self.released = True


def _face(center_x: float, *, width: float = 100.0, y: float = 300.0) -> dict:
    """One detector result in the shape ``detect_face_candidates`` really returns."""
    return {"box": [center_x - width / 2, y, width, width], "score": width * width}


def _install(monkeypatch, capture, *, detect, mar):
    """Inject fake cv2 + fake MediaPipe detectors for the call-scoped imports."""
    cv2 = types.ModuleType("cv2")
    cv2.CAP_PROP_FPS = _CAP_PROP_FPS
    cv2.CAP_PROP_POS_FRAMES = _CAP_PROP_POS_FRAMES
    cv2.VideoCapture = lambda path: capture
    monkeypatch.setitem(sys.modules, "cv2", cv2)

    detectors = types.ModuleType("clippyme.pipeline.reframe_detect")
    detectors.detect_face_candidates = detect
    detectors.compute_mouth_aspect_ratio = mar
    monkeypatch.setitem(sys.modules, "clippyme.pipeline.reframe_detect", detectors)


#: A talking face on the left, a still face on the right.
_TALKER_X, _LISTENER_X = 200.0, 800.0


def _two_faces(frame):
    return [_face(_TALKER_X), _face(_LISTENER_X)]


def _mar_talker_oscillates(frame, box):
    """Left face's mouth oscillates frame to frame; right face's is held open."""
    x, _y, w, _h = box
    if x + w / 2 == pytest.approx(_TALKER_X):
        return 0.2 + 0.3 * (frame.index % 2)
    return 0.6  # wide, but perfectly still — a resting open jaw


# --- the question the module exists to answer ------------------------------


def test_attributes_the_turn_to_the_face_whose_mouth_moves(monkeypatch):
    """A still-but-open mouth must not outvote an oscillating one."""
    capture = _FakeCapture()
    _install(monkeypatch, capture, detect=_two_faces, mar=_mar_talker_oscillates)

    positions = locate_speakers(
        "video.mp4", [SpeakerSegment(0.0, 3.0, 1)], samples_per_segment=6
    )

    assert positions == {1: pytest.approx(_TALKER_X)}


def test_confident_turns_outweigh_ambiguous_ones(monkeypatch):
    """Two turns disagree; the one with more mouth variance decides."""
    loud, quiet = 200.0, 900.0

    def detect(frame):
        return [_face(loud if frame.index < 100 else quiet)]

    def mar(frame, box):
        # Early turn oscillates hard, later turn barely moves.
        span = 0.4 if frame.index < 100 else 0.01
        return 0.3 + span * (frame.index % 2)

    capture = _FakeCapture()
    _install(monkeypatch, capture, detect=detect, mar=mar)

    positions = locate_speakers(
        "video.mp4",
        [SpeakerSegment(0.0, 3.0, 7), SpeakerSegment(4.0, 7.0, 7)],
        samples_per_segment=6,
    )

    # Weighted toward the confident turn, so nearer `loud` than the midpoint.
    assert positions[7] < (loud + quiet) / 2


def test_separate_speakers_get_separate_positions(monkeypatch):
    def detect(frame):
        return [_face(250.0 if frame.index < 100 else 850.0)]

    def mar(frame, box):
        return 0.2 + 0.3 * (frame.index % 2)

    _install(monkeypatch, _FakeCapture(), detect=detect, mar=mar)

    positions = locate_speakers(
        "video.mp4",
        [SpeakerSegment(0.0, 3.0, 1), SpeakerSegment(4.0, 7.0, 2)],
        samples_per_segment=6,
    )

    assert positions == {1: pytest.approx(250.0), 2: pytest.approx(850.0)}


# --- degradation: "no position", never an exception ------------------------


def test_unopenable_capture_yields_no_positions(monkeypatch):
    capture = _FakeCapture(opened=False)
    _install(monkeypatch, capture, detect=_two_faces, mar=_mar_talker_oscillates)

    assert locate_speakers("missing.mp4", [SpeakerSegment(0.0, 3.0, 1)]) == {}


@pytest.mark.parametrize("fps", [0.0, -1.0])
def test_unknown_fps_yields_no_positions(monkeypatch, fps):
    """Without fps there is no way to turn a timestamp into a frame index."""
    capture = _FakeCapture(fps=fps)
    _install(monkeypatch, capture, detect=_two_faces, mar=_mar_talker_oscillates)

    assert locate_speakers("video.mp4", [SpeakerSegment(0.0, 3.0, 1)]) == {}
    assert capture.released


def test_a_failing_detector_degrades_instead_of_raising(monkeypatch):
    def detect(frame):
        raise RuntimeError("mediapipe exploded")

    _install(monkeypatch, _FakeCapture(), detect=detect, mar=_mar_talker_oscillates)

    assert locate_speakers("video.mp4", [SpeakerSegment(0.0, 3.0, 1)]) == {}


def test_a_failing_mar_degrades_instead_of_raising(monkeypatch):
    def mar(frame, box):
        raise RuntimeError("facemesh exploded")

    _install(monkeypatch, _FakeCapture(), detect=_two_faces, mar=mar)

    assert locate_speakers("video.mp4", [SpeakerSegment(0.0, 3.0, 1)]) == {}


def test_unreadable_frames_are_skipped_not_fatal(monkeypatch):
    """Seeking past the end of a truncated file must not kill the phase."""
    capture = _FakeCapture(unreadable=range(10_000))
    _install(monkeypatch, capture, detect=_two_faces, mar=_mar_talker_oscillates)

    assert locate_speakers("video.mp4", [SpeakerSegment(0.0, 3.0, 1)]) == {}


def test_mar_returning_none_is_not_a_sample(monkeypatch):
    """A profile view yields None; three real samples are still required."""
    _install(
        monkeypatch, _FakeCapture(), detect=_two_faces, mar=lambda frame, box: None
    )

    assert locate_speakers("video.mp4", [SpeakerSegment(0.0, 3.0, 1)]) == {}


def test_capture_is_released_on_detector_failure(monkeypatch):
    capture = _FakeCapture()

    def detect(frame):
        raise RuntimeError("boom")

    _install(monkeypatch, capture, detect=detect, mar=_mar_talker_oscillates)
    locate_speakers("video.mp4", [SpeakerSegment(0.0, 3.0, 1)])

    assert capture.released


# --- sampling policy -------------------------------------------------------


def test_segments_shorter_than_the_floor_are_never_sampled(monkeypatch):
    """Too few frames to measure variance; the speaker is located elsewhere."""
    capture = _FakeCapture()
    _install(monkeypatch, capture, detect=_two_faces, mar=_mar_talker_oscillates)

    positions = locate_speakers("video.mp4", [SpeakerSegment(1.0, 1.2, 1)])

    assert positions == {}
    assert capture.seeks == []  # never even decoded


def test_a_track_needs_three_samples_before_variance_is_trusted(monkeypatch):
    """Two points always describe a line, so 2 samples cannot show oscillation."""
    capture = _FakeCapture()
    _install(monkeypatch, capture, detect=_two_faces, mar=_mar_talker_oscillates)

    assert (
        locate_speakers(
            "video.mp4", [SpeakerSegment(0.0, 3.0, 1)], samples_per_segment=2
        )
        == {}
    )


def test_sampling_stays_inside_the_segment(monkeypatch):
    """A turn's samples must not bleed into the neighbouring speaker's face."""
    capture = _FakeCapture(fps=10.0)
    _install(monkeypatch, capture, detect=_two_faces, mar=_mar_talker_oscillates)

    locate_speakers("video.mp4", [SpeakerSegment(2.0, 5.0, 1)], samples_per_segment=6)

    assert capture.seeks, "expected the segment to be sampled"
    assert min(capture.seeks) >= 2.0 * 10.0
    assert max(capture.seeks) < 5.0 * 10.0


def test_default_sample_count_is_modest_enough_to_be_cheap():
    """A ten-minute source must cost seconds, not minutes."""
    assert 4 <= DEFAULT_SAMPLES_PER_SEGMENT <= 30


# --- box normalisation -----------------------------------------------------


def test_face_box_accepts_the_real_detector_shape():
    assert _face_box({"box": [10, 20, 30, 40], "score": 1200}) == (
        10.0,
        20.0,
        30.0,
        40.0,
    )


def test_face_box_accepts_a_bare_sequence():
    assert _face_box((10, 20, 30, 40)) == (10.0, 20.0, 30.0, 40.0)
    assert _face_box([10, 20, 30, 40, 0.9]) == (10.0, 20.0, 30.0, 40.0)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"score": 5},  # dict with no box
        (10, 20),  # too short
        "not a box",
        {"box": None},
        {"box": [1, 2, 3, "x"]},
        (10, 20, 0, 40),  # zero width
        (10, 20, 30, -5),  # negative height
    ],
)
def test_face_box_rejects_what_it_cannot_measure(value):
    assert _face_box(value) is None


# --- the cv2 boundary in run_reframe's default locator ---------------------


def test_default_locator_degrades_when_cv2_is_missing(monkeypatch, capsys):
    """Phase 5 must survive a host with no CV runtime, not crash on it.

    ``detect`` imports cleanly without cv2 (only ``clipper_pro.types`` is
    module-scope), so a guard around the *import* never fires and the
    ``ModuleNotFoundError`` from the call escapes instead. ``run_reframe`` treats
    an empty mapping as "centre every crop", which is the documented behaviour.
    """
    from clipper_pro.reframe import _default_locator

    monkeypatch.setitem(sys.modules, "cv2", None)  # import cv2 -> ImportError

    assert _default_locator("video.mp4", [SpeakerSegment(0.0, 3.0, 1)]) == {}
    assert "centred crop" in capsys.readouterr().err


def test_default_locator_returns_real_positions_when_cv2_is_present(monkeypatch):
    """The happy path still reaches ``locate_speakers`` through the guard."""
    from clipper_pro.reframe import _default_locator

    _install(monkeypatch, _FakeCapture(), detect=_two_faces, mar=_mar_talker_oscillates)

    positions = _default_locator("video.mp4", [SpeakerSegment(0.0, 3.0, 1)])

    assert positions == {1: pytest.approx(_TALKER_X)}


# --- the contract that actually broke -------------------------------------


def _defined_functions(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _names_imported_from(module_path: Path, target: str) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == target
        for alias in node.names
    }


def test_import_contract():
    """Every name ``detect`` pulls from ``reframe_detect`` must exist there.

    Both sides are parsed from source rather than imported, so this runs without
    cv2 — the fast-suite guard against the name drift that made
    ``locate_speakers`` raise ``ImportError`` on every call in the CV image.
    Deriving the expected set from ``detect.py`` itself means a future rename
    cannot slip past by also editing the test's hardcoded list.
    """
    import clipper_pro.reframe.detect as detect_module
    import clippyme.pipeline as clippyme_pipeline

    target = "clippyme.pipeline.reframe_detect"
    wanted = _names_imported_from(Path(detect_module.__file__), target)
    available = _defined_functions(Path(clippyme_pipeline.__file__).parent / "reframe_detect.py")

    assert wanted, "expected detect.py to import the MediaPipe helpers"
    assert wanted <= available, f"missing from {target}: {sorted(wanted - available)}"
