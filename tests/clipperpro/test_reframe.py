"""Phase 5: speaker timeline, lead shift, crop geometry, smoothing, orchestration.

Every decision phase 5 makes is covered here without cv2. Only
``clipper_pro.reframe.detect`` needs the heavy runtime, and it makes no decisions
— it answers "which face is speaker 1", which the Docker integration suite
verifies.
"""

import json
from itertools import pairwise

import pytest

from clipper_pro import reframe as phase5
from clipper_pro.config import Settings
from clipper_pro.errors import ToolFailureError, ValidationError
from clipper_pro.reframe.camera_ops import (
    build_keyframes,
    crop_window,
    smooth_keyframes,
    smoothing_window,
)
from clipper_pro.reframe.plan import CameraPlan
from clipper_pro.reframe.speaker_ops import apply_lead, segment_at, speaker_segments
from clipper_pro.transcribe.base import TranscriptResult
from clipper_pro.types import (
    CameraKeyframe,
    Candidate,
    SourceMedia,
    SpeakerSegment,
    Word,
)


def _words(spec):
    """spec: [(text, start, end, speaker), ...]"""
    return [Word(t, s, e, speaker=sp) for t, s, e, sp in spec]


@pytest.fixture
def workspace(tmp_path):
    from clipper_pro.workspace import init

    root = tmp_path / "run"
    init(str(root))
    return str(root)


class TestSpeakerSegments:
    def test_merges_consecutive_words_of_one_speaker(self):
        words = _words([("a", 0.0, 0.3, 0), ("b", 0.4, 0.7, 0), ("c", 0.8, 1.1, 0)])
        segments = speaker_segments(words)
        assert len(segments) == 1
        assert (segments[0].start, segments[0].end, segments[0].speaker) == (0.0, 1.1, 0)

    def test_splits_on_a_speaker_change(self):
        words = _words([("a", 0.0, 0.3, 0), ("b", 0.4, 0.7, 1)])
        segments = speaker_segments(words)
        assert [s.speaker for s in segments] == [0, 1]

    def test_splits_on_a_long_pause_by_the_same_speaker(self):
        words = _words([("a", 0.0, 0.3, 0), ("b", 5.0, 5.3, 0)])
        assert len(speaker_segments(words, gap_tolerance=0.8)) == 2

    def test_short_pauses_do_not_split(self):
        # Otherwise the natural gaps between words would make the camera twitch
        # on every breath.
        words = _words([("a", 0.0, 0.3, 0), ("b", 0.9, 1.2, 0)])
        assert len(speaker_segments(words, gap_tolerance=0.8)) == 1

    def test_unlabelled_transcript_yields_one_segment_per_run(self):
        words = _words([("a", 0.0, 0.3, None), ("b", 0.4, 0.7, None)])
        segments = speaker_segments(words)
        assert len(segments) == 1 and segments[0].speaker is None

    def test_empty_input(self):
        assert speaker_segments([]) == []

    def test_zero_width_turn_gets_usable_width(self):
        segments = speaker_segments(_words([("a", 3.0, 3.0, 0)]))
        assert segments[0].end > segments[0].start

    def test_negative_gap_tolerance_is_rejected(self):
        with pytest.raises(ValidationError, match="gap_tolerance"):
            speaker_segments([], gap_tolerance=-1.0)


class TestApplyLead:
    def test_moves_a_transition_earlier(self):
        segments = [SpeakerSegment(0.0, 5.0, 0), SpeakerSegment(5.5, 10.0, 1)]
        shifted = apply_lead(segments, lead=0.2, min_hold=1.0)
        # The camera reaches speaker 1 before speaker 1 is audible.
        assert shifted[1].start == pytest.approx(5.3)

    def test_first_segment_keeps_its_start(self):
        segments = [SpeakerSegment(2.0, 5.0, 0), SpeakerSegment(5.5, 10.0, 1)]
        assert apply_lead(segments)[0].start == 2.0

    def test_last_segment_keeps_its_end(self):
        segments = [SpeakerSegment(0.0, 5.0, 0), SpeakerSegment(5.5, 10.0, 1)]
        assert apply_lead(segments)[-1].end == 10.0

    def test_timeline_is_contiguous(self):
        # Phase 6 must never have to invent a position for a hole.
        segments = [
            SpeakerSegment(0.0, 4.0, 0),
            SpeakerSegment(6.0, 10.0, 1),
            SpeakerSegment(12.0, 16.0, 0),
        ]
        shifted = apply_lead(segments)
        for earlier, later in pairwise(shifted):
            assert earlier.end == later.start

    def test_min_hold_stops_the_camera_jumping_too_soon(self):
        # A fast exchange must not pan faster than a viewer can follow.
        segments = [SpeakerSegment(0.0, 0.4, 0), SpeakerSegment(0.5, 5.0, 1)]
        shifted = apply_lead(segments, lead=0.2, min_hold=1.0)
        assert shifted[1].start == pytest.approx(1.0)

    def test_zero_lead_leaves_transitions_where_they_were(self):
        segments = [SpeakerSegment(0.0, 5.0, 0), SpeakerSegment(5.5, 10.0, 1)]
        shifted = apply_lead(segments, lead=0.0, min_hold=0.0)
        assert shifted[1].start == pytest.approx(5.5)

    def test_squeezed_out_turn_is_dropped_not_inverted(self):
        segments = [
            SpeakerSegment(0.0, 1.0, 0),
            SpeakerSegment(1.1, 1.2, 1),
            SpeakerSegment(1.3, 6.0, 2),
        ]
        shifted = apply_lead(segments, lead=0.2, min_hold=2.0)
        for segment in shifted:
            assert segment.end > segment.start

    def test_empty_input(self):
        assert apply_lead([]) == []

    @pytest.mark.parametrize("kwargs", [{"lead": -0.1}, {"min_hold": -1.0}])
    def test_negative_budgets_are_rejected(self, kwargs):
        with pytest.raises(ValidationError):
            apply_lead([SpeakerSegment(0, 1, 0)], **kwargs)


class TestSegmentAt:
    def test_finds_the_covering_segment(self):
        segments = [SpeakerSegment(0.0, 5.0, 0), SpeakerSegment(5.0, 10.0, 1)]
        assert segment_at(segments, 7.0).speaker == 1

    def test_boundary_belongs_to_the_later_segment(self):
        segments = [SpeakerSegment(0.0, 5.0, 0), SpeakerSegment(5.0, 10.0, 1)]
        assert segment_at(segments, 5.0).speaker == 1

    def test_past_the_end_holds_the_last_segment(self):
        # Better a stale position than no position at all.
        segments = [SpeakerSegment(0.0, 5.0, 0)]
        assert segment_at(segments, 99.0).speaker == 0

    def test_before_the_start_is_none(self):
        assert segment_at([SpeakerSegment(5.0, 10.0, 0)], 1.0) is None

    def test_empty_timeline(self):
        assert segment_at([], 1.0) is None


class TestCropWindow:
    def test_keeps_full_height_and_derives_width(self):
        _x, y, w, h = crop_window(960, 1920, 1080)
        assert (h, y) == (1080, 0.0)
        assert w == pytest.approx(1080 * 9 / 16)

    def test_never_upscales_a_narrow_source(self):
        # A source already narrower than 9:16 must not be blown up.
        _x, _y, w, _h = crop_window(300, 600, 1080)
        assert w <= 600

    def test_centres_on_the_target(self):
        x, _y, w, _h = crop_window(960, 1920, 1080)
        assert x + w / 2 == pytest.approx(960)

    def test_clamps_at_the_left_edge(self):
        x, _y, _w, _h = crop_window(10, 1920, 1080)
        assert x == 0.0

    def test_clamps_at_the_right_edge(self):
        x, _y, w, _h = crop_window(1910, 1920, 1080)
        assert x + w == pytest.approx(1920)

    @pytest.mark.parametrize("args", [(0, 1080), (1920, 0), (-1, 1080)])
    def test_invalid_dimensions_are_rejected(self, args):
        with pytest.raises(ValidationError, match="dimensions"):
            crop_window(100, *args)

    def test_invalid_aspect_is_rejected(self):
        with pytest.raises(ValidationError, match="aspect"):
            crop_window(100, 1920, 1080, aspect=0)


class TestSmoothingWindow:
    def test_scales_with_frame_rate(self):
        # The same visual smoothness at any fps, rather than three results.
        assert smoothing_window(30, 0.35) >= smoothing_window(24, 0.35)

    def test_is_always_odd(self):
        for fps in (24, 25, 30, 50, 60):
            assert smoothing_window(fps, 0.35) % 2 == 1

    def test_has_a_floor_of_three(self):
        assert smoothing_window(30, 0.0) == 3

    def test_zero_fps_is_rejected(self):
        with pytest.raises(ValidationError, match="fps"):
            smoothing_window(0)


class TestBuildKeyframes:
    def test_emits_one_keyframe_per_frame(self):
        segments = [SpeakerSegment(0.0, 10.0, 0)]
        keyframes = build_keyframes(
            segments, {0: 500.0}, start=0.0, end=2.0, fps=30,
            source_width=1920, source_height=1080,
        )
        assert len(keyframes) == 60

    def test_follows_the_speaker_position(self):
        segments = [SpeakerSegment(0.0, 1.0, 0), SpeakerSegment(1.0, 2.0, 1)]
        keyframes = build_keyframes(
            segments, {0: 400.0, 1: 1500.0}, start=0.0, end=2.0, fps=10,
            source_width=1920, source_height=1080,
        )
        assert keyframes[0].center_x == pytest.approx(400.0)
        assert keyframes[-1].center_x == pytest.approx(1500.0)

    def test_unlocated_speaker_falls_back_to_frame_centre(self):
        # A neutral position beats an arbitrary one.
        segments = [SpeakerSegment(0.0, 2.0, 7)]
        keyframes = build_keyframes(
            segments, {}, start=0.0, end=1.0, fps=10,
            source_width=1920, source_height=1080,
        )
        assert keyframes[0].center_x == pytest.approx(960.0)

    def test_records_the_speaker_on_each_keyframe(self):
        segments = [SpeakerSegment(0.0, 2.0, 3)]
        keyframes = build_keyframes(
            segments, {3: 900.0}, start=0.0, end=1.0, fps=10,
            source_width=1920, source_height=1080,
        )
        assert {kf.speaker for kf in keyframes} == {3}

    def test_inverted_range_is_rejected(self):
        with pytest.raises(ValidationError, match="greater than"):
            build_keyframes([], {}, start=5.0, end=5.0, fps=30,
                            source_width=1920, source_height=1080)


class TestSmoothKeyframes:
    def _jittered(self, fps=30, n=90):
        # A step change plus alternating single-pixel jitter.
        out = []
        for i in range(n):
            base = 400.0 if i < n // 2 else 1200.0
            out.append(
                CameraKeyframe(time=i / fps, x=base + (12 if i % 2 else -12),
                               y=0, width=607, height=1080, speaker=0)
            )
        return out

    def test_removes_per_frame_jitter(self):
        raw = self._jittered()
        smoothed = smooth_keyframes(raw, fps=30, source_width=1920)

        def wobble(frames):
            xs = [f.x for f in frames]
            return sum(abs(b - a) for a, b in pairwise(xs))

        assert wobble(smoothed) < wobble(raw) / 2

    def test_preserves_the_step_it_should_not_flatten(self):
        # Savitzky-Golay keeps an intentional fast pan; a mean filter would turn
        # this snap into a slow drift.
        smoothed = smooth_keyframes(self._jittered(), fps=30, source_width=1920)
        assert smoothed[-1].x - smoothed[0].x > 600

    def test_stays_within_frame(self):
        raw = [
            CameraKeyframe(time=i / 30, x=1920 - 607, y=0, width=607, height=1080)
            for i in range(30)
        ]
        for keyframe in smooth_keyframes(raw, fps=30, source_width=1920):
            assert 0 <= keyframe.x <= 1920 - 607 + 1e-6

    def test_leaves_other_fields_alone(self):
        raw = self._jittered()
        smoothed = smooth_keyframes(raw, fps=30, source_width=1920)
        assert [k.time for k in smoothed] == [k.time for k in raw]
        assert [k.speaker for k in smoothed] == [k.speaker for k in raw]

    def test_too_short_to_smooth_is_returned_unchanged(self):
        raw = [CameraKeyframe(time=0, x=100, y=0, width=607, height=1080)]
        assert smooth_keyframes(raw, fps=30, source_width=1920) == raw


class TestRunReframe:
    def _transcript(self):
        return TranscriptResult(
            words=_words([
                ("a", 0.0, 0.4, 0), ("b", 0.5, 0.9, 0),
                ("c", 5.0, 5.4, 1), ("d", 5.5, 5.9, 1),
            ]),
            language="en",
        )

    def test_produces_a_plan_per_clip(self, workspace):
        plans = phase5.run_reframe(
            [Candidate(0.0, 8.0), Candidate(10.0, 20.0)],
            self._transcript(), SourceMedia(path="/v.mp4", duration=30.0), workspace,
            locate=lambda _p, _s: {0: 400.0, 1: 1500.0},
            source_width=1920, source_height=1080, fps=25,
        )
        assert len(plans) == 2
        assert plans[0].keyframes and plans[1].keyframes

    def test_camera_follows_the_diarized_speakers(self, workspace):
        plans = phase5.run_reframe(
            [Candidate(0.0, 8.0)], self._transcript(),
            SourceMedia(path="/v.mp4", duration=30.0), workspace,
            locate=lambda _p, _s: {0: 400.0, 1: 1500.0},
            source_width=1920, source_height=1080, fps=25,
        )
        xs = [kf.center_x for kf in plans[0].keyframes]
        assert xs[0] < 700 and xs[-1] > 1200  # moved from speaker 0 to speaker 1

    def test_the_move_starts_before_the_new_speaker_is_audible(self, workspace):
        # Speaker 1's first word is at 5.0s; with a 0.2s lead the camera must
        # already be leaving before then.
        plans = phase5.run_reframe(
            [Candidate(0.0, 8.0)], self._transcript(),
            SourceMedia(path="/v.mp4", duration=30.0), workspace,
            settings=Settings(camera_lead=0.2, camera_min_hold=0.0,
                              camera_smooth_seconds=0.0),
            locate=lambda _p, _s: {0: 400.0, 1: 1500.0},
            source_width=1920, source_height=1080, fps=25,
        )
        switched = next(kf for kf in plans[0].keyframes if kf.speaker == 1)
        assert switched.time < 5.0

    def test_no_positions_yields_centred_crops(self, workspace):
        plans = phase5.run_reframe(
            [Candidate(0.0, 4.0)], self._transcript(),
            SourceMedia(path="/v.mp4", duration=30.0), workspace,
            locate=lambda _p, _s: {},
            source_width=1920, source_height=1080, fps=25,
        )
        assert all(kf.center_x == pytest.approx(960.0) for kf in plans[0].keyframes)

    def test_writes_the_reframe_artifact_with_the_timeline(self, workspace):
        phase5.run_reframe(
            [Candidate(0.0, 4.0)], self._transcript(),
            SourceMedia(path="/v.mp4", duration=30.0), workspace,
            locate=lambda _p, _s: {0: 400.0, 1: 1500.0},
            source_width=1920, source_height=1080, fps=25,
        )
        with open(f"{workspace}/analysis/reframe.json") as fh:
            payload = json.load(fh)
        # The shifted turns and resolved positions are recorded so a wrong camera
        # cue can be traced to the turn that caused it.
        assert payload["speaker_segments"]
        assert payload["speaker_positions"] == {"0": 400.0, "1": 1500.0}
        assert len(payload["clips"]) == 1

    def test_crop_is_nine_by_sixteen(self, workspace):
        plans = phase5.run_reframe(
            [Candidate(0.0, 4.0)], self._transcript(),
            SourceMedia(path="/v.mp4", duration=30.0), workspace,
            locate=lambda _p, _s: {}, source_width=1920, source_height=1080, fps=25,
        )
        keyframe = plans[0].keyframes[0]
        assert keyframe.width / keyframe.height == pytest.approx(9 / 16, rel=1e-3)

    def test_no_candidates_is_an_error(self, workspace):
        with pytest.raises(ToolFailureError, match="no clips to reframe"):
            phase5.run_reframe(
                [], self._transcript(), SourceMedia(path="/v.mp4", duration=30.0),
                workspace, locate=lambda _p, _s: {},
                source_width=1920, source_height=1080, fps=25,
            )


class TestCameraPlan:
    def test_round_trips_through_dict(self):
        plan = CameraPlan(
            clip_index=0, start=1.0, end=5.0, source_width=1920,
            source_height=1080, fps=30.0,
            keyframes=[CameraKeyframe(time=1.0, x=0, y=0, width=607, height=1080, speaker=1)],
        )
        assert CameraPlan.from_dict(plan.to_dict()).to_dict() == plan.to_dict()

    def test_inverted_range_is_rejected(self):
        with pytest.raises(ValidationError, match="must exceed start"):
            CameraPlan(clip_index=0, start=5.0, end=1.0, source_width=1920,
                       source_height=1080, fps=30.0)

    def test_non_positive_fps_is_rejected(self):
        with pytest.raises(ValidationError, match="fps"):
            CameraPlan(clip_index=0, start=0.0, end=1.0, source_width=1920,
                       source_height=1080, fps=0.0)


class TestKeyframeType:
    def test_negative_time_is_rejected(self):
        with pytest.raises(ValidationError, match="time"):
            CameraKeyframe(time=-1.0, x=0, y=0, width=10, height=10)

    @pytest.mark.parametrize("size", [(0, 10), (10, 0), (-1, 10)])
    def test_non_positive_size_is_rejected(self, size):
        with pytest.raises(ValidationError, match="positive size"):
            CameraKeyframe(time=0.0, x=0, y=0, width=size[0], height=size[1])
