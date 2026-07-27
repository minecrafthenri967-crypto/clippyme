"""Channel watcher: catch-up policy, retry bookkeeping, durability, the loop.

The pipeline phases themselves are covered by their own modules' tests. What
matters here is everything that decides *whether* to spend money — the first-poll
catch-up rule, the retry cap, and the resume-from-manifest skip — plus the
guarantee that one bad video or one unreachable channel does not stop the watch.
"""

import json
import os
import stat

import pytest

from clipper_pro import watch as watch_module
from clipper_pro.config import WATCH_CATCHUP_MODES, Settings
from clipper_pro.errors import ClipperProError, ValidationError, WatchError
from clipper_pro.watch import WatchConfig, process_video, run_watch
from clipper_pro.watch import feed as feed_module
from clipper_pro.watch import state as state_module
from clipper_pro.watch.state_ops import (
    CATCHUP_MODES,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    adopt_backlog,
    init_state,
    is_new_channel,
    parse_channels,
    record_result,
    run_dir_for,
    select_pending,
    summarize,
    validate_video_id,
)
from clipper_pro.workspace import init as workspace_init
from clipper_pro.workspace import record_artifact

CHANNEL = "UC" + "a" * 22
OTHER_CHANNEL = "UC" + "b" * 22


def video(video_id):
    return {"id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}"}


VID_A = "aaaaaaaaaaa"
VID_B = "bbbbbbbbbbb"
VID_C = "ccccccccccc"


class TestParseChannels:
    def test_splits_on_commas_and_whitespace(self):
        assert parse_channels("@one, @two  @three") == ["@one", "@two", "@three"]

    def test_deduplicates_preserving_order(self):
        assert parse_channels("@b,@a,@b") == ["@b", "@a"]

    def test_empty_input_yields_nothing(self):
        assert parse_channels("") == []
        assert parse_channels(None) == []


class TestVideoIdValidation:
    def test_accepts_a_youtube_id(self):
        assert validate_video_id(VID_A) == VID_A

    @pytest.mark.parametrize(
        "bad", ["", "short", "../../etc/passwd", "aaaaaaaaaa/", "a" * 12]
    )
    def test_rejects_anything_else(self, bad):
        with pytest.raises(ValidationError):
            validate_video_id(bad)

    def test_run_dir_cannot_escape_the_runs_root(self, tmp_path):
        # The id picks a directory name, so a traversal attempt must not merely
        # be normalised away — it must be refused.
        with pytest.raises(ValidationError):
            run_dir_for(str(tmp_path), "../../escape")

    def test_run_dir_is_named_after_the_video(self, tmp_path):
        assert run_dir_for(str(tmp_path), VID_A) == str(tmp_path / VID_A)


class TestSelectPending:
    def test_unseen_videos_are_pending(self):
        state = init_state()
        assert select_pending(state, CHANNEL, [video(VID_A)]) == [video(VID_A)]

    def test_finished_videos_are_not_reprocessed(self):
        state = init_state()
        record_result(state, CHANNEL, VID_A, status=STATUS_OK)
        assert select_pending(state, CHANNEL, [video(VID_A)]) == []

    def test_adopted_videos_are_not_processed(self):
        state = init_state()
        adopt_backlog(state, CHANNEL, [video(VID_A)])
        assert select_pending(state, CHANNEL, [video(VID_A)]) == []

    def test_a_failure_is_retried_while_under_the_cap(self):
        state = init_state()
        record_result(state, CHANNEL, VID_A, status=STATUS_FAILED)
        assert select_pending(state, CHANNEL, [video(VID_A)], max_attempts=3) == [
            video(VID_A)
        ]

    def test_a_repeated_failure_is_eventually_left_alone(self):
        state = init_state()
        for _ in range(3):
            record_result(state, CHANNEL, VID_A, status=STATUS_FAILED)
        assert select_pending(state, CHANNEL, [video(VID_A)], max_attempts=3) == []

    def test_the_backlog_is_processed_oldest_first(self):
        # The feed hands over newest-first; a backfill reads better chronological.
        state = init_state()
        feed = [video(VID_C), video(VID_B), video(VID_A)]
        assert [v["id"] for v in select_pending(state, CHANNEL, feed)] == [
            VID_A, VID_B, VID_C
        ]

    def test_malformed_feed_entries_are_ignored(self):
        state = init_state()
        assert select_pending(state, CHANNEL, [{"id": "../etc"}, {"id": ""}]) == []


class TestCatchup:
    def test_a_channel_is_new_only_until_it_is_recorded(self):
        state = init_state()
        assert is_new_channel(state, CHANNEL)
        adopt_backlog(state, CHANNEL, [])
        assert not is_new_channel(state, CHANNEL)

    def test_adopting_marks_everything_skipped(self):
        state = init_state()
        assert adopt_backlog(state, CHANNEL, [video(VID_A), video(VID_B)]) == 2
        videos = state["channels"][CHANNEL]["videos"]
        assert {v["status"] for v in videos.values()} == {STATUS_SKIPPED}

    def test_adopting_does_not_overwrite_a_known_result(self):
        state = init_state()
        record_result(state, CHANNEL, VID_A, status=STATUS_OK)
        assert adopt_backlog(state, CHANNEL, [video(VID_A)]) == 0
        assert state["channels"][CHANNEL]["videos"][VID_A]["status"] == STATUS_OK

    def test_config_mirrors_the_modes(self):
        assert WATCH_CATCHUP_MODES == CATCHUP_MODES


class TestRecordResult:
    def test_attempts_accumulate(self):
        state = init_state()
        record_result(state, CHANNEL, VID_A, status=STATUS_FAILED)
        record_result(state, CHANNEL, VID_A, status=STATUS_FAILED)
        assert state["channels"][CHANNEL]["videos"][VID_A]["attempts"] == 2

    def test_an_unknown_status_is_refused(self):
        with pytest.raises(ValidationError):
            record_result(init_state(), CHANNEL, VID_A, status="maybe")

    def test_a_bogus_channel_id_is_refused(self):
        with pytest.raises(ValidationError):
            record_result(init_state(), "not-a-channel", VID_A, status=STATUS_OK)

    def test_summarize_counts_across_channels(self):
        state = init_state()
        record_result(state, CHANNEL, VID_A, status=STATUS_OK)
        record_result(state, OTHER_CHANNEL, VID_B, status=STATUS_FAILED)
        assert summarize(state) == {STATUS_OK: 1, STATUS_FAILED: 1, STATUS_SKIPPED: 0}


class TestStateFile:
    def test_a_missing_file_starts_fresh(self, tmp_path):
        assert state_module.load(str(tmp_path / "nope.json")) == init_state()

    def test_roundtrip(self, tmp_path):
        path = str(tmp_path / "watch-state.json")
        state = init_state()
        record_result(state, CHANNEL, VID_A, status=STATUS_OK, clips=4)
        state_module.save(path, state)
        assert state_module.load(path)["channels"][CHANNEL]["videos"][VID_A]["clips"] == 4

    def test_written_private(self, tmp_path):
        path = str(tmp_path / "watch-state.json")
        state_module.save(path, init_state())
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_corrupt_state_is_an_error_not_a_fresh_start(self, tmp_path):
        # Starting over silently would re-process every video in every feed.
        path = tmp_path / "watch-state.json"
        path.write_text("{not json")
        with pytest.raises(ValidationError):
            state_module.load(str(path))

    def test_a_future_schema_is_refused(self, tmp_path):
        path = tmp_path / "watch-state.json"
        path.write_text(json.dumps({"schema_version": 999, "channels": {}}))
        with pytest.raises(ValidationError):
            state_module.load(str(path))


class TestFeedAdapter:
    def test_a_network_failure_becomes_a_retryable_error(self, monkeypatch):
        monkeypatch.setattr(
            feed_module, "fetch_feed",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 503")),
        )
        with pytest.raises(WatchError):
            feed_module.fetch_videos(CHANNEL)

    def test_an_oversized_feed_is_a_validation_error(self, monkeypatch):
        monkeypatch.setattr(
            feed_module, "fetch_feed",
            lambda *a, **k: (_ for _ in ()).throw(ValueError("feed exceeded size cap")),
        )
        with pytest.raises(ValidationError):
            feed_module.fetch_videos(CHANNEL)

    def test_a_bad_channel_id_never_reaches_the_network(self):
        with pytest.raises(ValidationError):
            feed_module.fetch_videos("not-a-channel")

    def test_an_unresolvable_channel_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            feed_module, "resolve_channel_id",
            lambda c: (_ for _ in ()).throw(ValueError("no such channel")),
        )
        with pytest.raises(ValidationError):
            feed_module.resolve_channel("@ghost")


# --- the loop ---------------------------------------------------------------


@pytest.fixture
def runs_root(tmp_path):
    return str(tmp_path / "runs")


@pytest.fixture
def calls(monkeypatch):
    """Record which phases ran, with a stub that always succeeds."""
    recorded = []

    def fake_run_phase(phase, work_dir, *, source=None, options=None):
        recorded.append((phase, work_dir, source))
        os.makedirs(work_dir, exist_ok=True)
        return {"phase": phase, "clips": 2}

    monkeypatch.setattr(watch_module, "run_phase", fake_run_phase)
    return recorded


def config_for(runs_root, **kwargs):
    kwargs.setdefault("channels", ("@chan",))
    kwargs.setdefault("phases", ("ingest", "render"))
    kwargs.setdefault("once", True)
    return WatchConfig(runs_root=runs_root, **kwargs)


def drive(runs_root, feed_items, *, monkeypatch, **kwargs):
    """Run one watch cycle against a stubbed feed, returning the summary."""
    monkeypatch.setattr(feed_module, "resolve_channel", lambda c: CHANNEL)
    monkeypatch.setattr(feed_module, "fetch_videos", lambda cid, **k: feed_items)
    return run_watch(config_for(runs_root, **kwargs), log=lambda m: None)


class TestWatchConfig:
    def test_channels_are_required(self, runs_root):
        with pytest.raises(ValidationError):
            WatchConfig(channels=(), runs_root=runs_root)

    def test_an_unknown_catchup_mode_is_refused(self, runs_root):
        with pytest.raises(ValidationError):
            WatchConfig(channels=("@c",), runs_root=runs_root, catchup="everything")

    def test_an_unknown_phase_is_refused(self, runs_root):
        with pytest.raises(ValidationError):
            WatchConfig(channels=("@c",), runs_root=runs_root, phases=("polish",))


class TestFirstPoll:
    def test_live_only_processes_nothing(self, runs_root, calls, monkeypatch):
        summary = drive(runs_root, [video(VID_A), video(VID_B)], monkeypatch=monkeypatch)
        assert calls == []
        assert summary["totals"][STATUS_SKIPPED] == 2

    def test_backfill_processes_the_lot(self, runs_root, calls, monkeypatch):
        summary = drive(
            runs_root, [video(VID_A), video(VID_B)],
            monkeypatch=monkeypatch, catchup="backfill",
        )
        assert [c[0] for c in calls] == ["ingest", "render", "ingest", "render"]
        assert summary["processed_ok"] == 2

    def test_the_source_url_only_reaches_ingest(self, runs_root, calls, monkeypatch):
        drive(runs_root, [video(VID_A)], monkeypatch=monkeypatch, catchup="backfill")
        assert calls[0][2] == f"https://www.youtube.com/watch?v={VID_A}"
        assert calls[1][2] is None

    def test_each_video_gets_its_own_workspace(self, runs_root, calls, monkeypatch):
        drive(
            runs_root, [video(VID_A), video(VID_B)],
            monkeypatch=monkeypatch, catchup="backfill",
        )
        assert {os.path.basename(c[1]) for c in calls} == {VID_A, VID_B}


class TestSubsequentPolls:
    def test_only_the_new_upload_is_processed(self, runs_root, calls, monkeypatch):
        drive(runs_root, [video(VID_A)], monkeypatch=monkeypatch)  # adopts VID_A
        calls.clear()
        drive(runs_root, [video(VID_B), video(VID_A)], monkeypatch=monkeypatch)
        assert {os.path.basename(c[1]) for c in calls} == {VID_B}

    def test_state_survives_a_restart(self, runs_root, calls, monkeypatch):
        drive(runs_root, [video(VID_A)], monkeypatch=monkeypatch, catchup="backfill")
        calls.clear()
        # A second run_watch is a fresh process as far as the watcher is
        # concerned: it must reload the state rather than start over.
        drive(runs_root, [video(VID_A)], monkeypatch=monkeypatch, catchup="backfill")
        assert calls == []

    def test_the_state_file_lives_beside_the_runs(self, runs_root, calls, monkeypatch):
        summary = drive(runs_root, [video(VID_A)], monkeypatch=monkeypatch)
        assert summary["state_path"] == os.path.join(runs_root, "watch-state.json")
        assert os.path.isfile(summary["state_path"])


class TestFailureHandling:
    def test_a_failing_video_is_recorded_not_raised(
        self, runs_root, monkeypatch
    ):
        def boom(phase, work_dir, *, source=None, options=None):
            raise ClipperProError("deepgram said no")

        monkeypatch.setattr(watch_module, "run_phase", boom)
        summary = drive(
            runs_root, [video(VID_A)], monkeypatch=monkeypatch, catchup="backfill"
        )
        assert summary["processed_failed"] == 1
        assert summary["totals"][STATUS_FAILED] == 1

    def test_an_unexpected_exception_does_not_escape(self, runs_root, monkeypatch):
        def boom(phase, work_dir, *, source=None, options=None):
            raise RuntimeError("ffmpeg segfaulted")

        monkeypatch.setattr(watch_module, "run_phase", boom)
        summary = drive(
            runs_root, [video(VID_A)], monkeypatch=monkeypatch, catchup="backfill"
        )
        assert summary["processed_failed"] == 1

    def test_the_failing_phase_is_named(self, runs_root, monkeypatch):
        def boom(phase, work_dir, *, source=None, options=None):
            if phase == "render":
                raise ClipperProError("no disk space")
            return {"phase": phase}

        monkeypatch.setattr(watch_module, "run_phase", boom)
        monkeypatch.setattr(feed_module, "resolve_channel", lambda c: CHANNEL)
        monkeypatch.setattr(feed_module, "fetch_videos", lambda cid, **k: [video(VID_A)])
        run_watch(
            config_for(runs_root, catchup="backfill"), log=lambda m: None
        )
        state = state_module.load(os.path.join(runs_root, "watch-state.json"))
        assert "render:" in state["channels"][CHANNEL]["videos"][VID_A]["detail"]

    def test_one_video_failing_does_not_stop_the_next(self, runs_root, monkeypatch):
        seen = []

        def flaky(phase, work_dir, *, source=None, options=None):
            seen.append(os.path.basename(work_dir))
            if os.path.basename(work_dir) == VID_A:
                raise ClipperProError("nope")
            return {"phase": phase, "clips": 1}

        monkeypatch.setattr(watch_module, "run_phase", flaky)
        summary = drive(
            runs_root, [video(VID_B), video(VID_A)],
            monkeypatch=monkeypatch, catchup="backfill",
        )
        assert VID_B in seen
        assert summary["processed_ok"] == 1 and summary["processed_failed"] == 1

    def test_an_unreachable_channel_leaves_the_others_alone(
        self, runs_root, calls, monkeypatch
    ):
        monkeypatch.setattr(
            feed_module, "resolve_channel",
            lambda c: CHANNEL if c == "@good" else OTHER_CHANNEL,
        )

        def fetch(channel_id, **kwargs):
            if channel_id == OTHER_CHANNEL:
                raise WatchError("connection reset")
            return [video(VID_A)]

        monkeypatch.setattr(feed_module, "fetch_videos", fetch)
        summary = run_watch(
            config_for(runs_root, channels=("@good", "@bad"), catchup="backfill"),
            log=lambda m: None,
        )
        assert summary["processed_ok"] == 1
        assert {os.path.basename(c[1]) for c in calls} == {VID_A}

    def test_a_channel_that_cannot_be_resolved_stops_startup(
        self, runs_root, monkeypatch
    ):
        # A typo in the channel list should fail loudly at startup, not be
        # discovered a week later.
        monkeypatch.setattr(
            feed_module, "resolve_channel",
            lambda c: (_ for _ in ()).throw(ValidationError("no such channel")),
        )
        with pytest.raises(ValidationError):
            run_watch(config_for(runs_root), log=lambda m: None)


class TestResume:
    def test_phases_already_in_the_manifest_are_skipped(self, tmp_path, calls):
        # This is what stops a retry after a render failure from paying for the
        # transcription and the ranking a second time.
        run_dir = str(tmp_path / VID_A)
        workspace_init(run_dir)
        record_artifact(run_dir, "ingest", {"phase": "ingest"})

        outcome = process_video(
            video(VID_A), run_dir,
            phases=("ingest", "render"), log=lambda m: None,
        )
        assert [c[0] for c in calls] == ["render"]
        assert outcome["status"] == STATUS_OK

    def test_clip_count_comes_from_the_render_phase(self, tmp_path, calls):
        outcome = process_video(
            video(VID_A), str(tmp_path / VID_A),
            phases=("ingest", "render"), log=lambda m: None,
        )
        assert outcome["clips"] == 2


class TestLoop:
    def test_it_polls_repeatedly_until_interrupted(self, runs_root, calls, monkeypatch):
        monkeypatch.setattr(feed_module, "resolve_channel", lambda c: CHANNEL)
        monkeypatch.setattr(feed_module, "fetch_videos", lambda cid, **k: [])

        cycles = {"n": 0}

        def fake_sleep(seconds):
            cycles["n"] += 1
            assert seconds == 900
            if cycles["n"] >= 3:
                raise KeyboardInterrupt

        summary = run_watch(
            config_for(runs_root, once=False), log=lambda m: None, sleep=fake_sleep
        )
        assert summary["cycles"] == 3


class TestSettings:
    def test_watch_knobs_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("CLIPPER_PRO_WATCH_CHANNELS", "@a,@b")
        monkeypatch.setenv("CLIPPER_PRO_WATCH_INTERVAL", "300")
        monkeypatch.setenv("CLIPPER_PRO_WATCH_CATCHUP", "backfill")
        monkeypatch.setenv("CLIPPER_PRO_WATCH_MAX_ATTEMPTS", "5")
        settings = Settings.from_env()
        assert parse_channels(settings.watch_channels) == ["@a", "@b"]
        assert settings.watch_interval == 300
        assert settings.watch_catchup == "backfill"
        assert settings.watch_max_attempts == 5

    def test_defaults_are_conservative(self, monkeypatch):
        for name in (
            "CLIPPER_PRO_WATCH_CHANNELS", "CLIPPER_PRO_WATCH_INTERVAL",
            "CLIPPER_PRO_WATCH_CATCHUP", "CLIPPER_PRO_WATCH_MAX_ATTEMPTS",
        ):
            monkeypatch.delenv(name, raising=False)
        settings = Settings.from_env()
        # live_only is the default that stops a first poll from billing the API
        # once per video already in the feed.
        assert settings.watch_catchup == "live_only"
        assert settings.watch_interval == 900

    def test_a_silly_interval_is_clamped(self, monkeypatch):
        monkeypatch.setenv("CLIPPER_PRO_WATCH_INTERVAL", "1")
        assert Settings.from_env().watch_interval == 60
