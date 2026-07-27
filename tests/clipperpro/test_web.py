"""The local web UI: run bookkeeping, the background worker, and the HTTP layer."""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from clipper_pro.errors import ToolFailureError, ValidationError
from clipper_pro.pipeline import PHASES, PhaseOptions
from clipper_pro.web.app import create_app
from clipper_pro.web.runs import (
    MAX_LOG_LINES,
    RunRecord,
    RunRegistry,
    clip_path,
    default_runs_root,
    load_report,
    new_run_id,
    slugify,
    workdir_for,
)
from clipper_pro.web.worker import execute_run


class TestSlugify:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("My Talk", "My-Talk"),
            ("a/b\\c", "a-b-c"),
            ("Why I Quit! (2024) 🎬", "Why-I-Quit-2024"),
            ("....", "run"),
            ("", "run"),
            (None, "run"),
        ],
    )
    def test_produces_a_filesystem_safe_fragment(self, raw, expected):
        assert slugify(raw) == expected

    def test_truncates(self):
        assert len(slugify("x" * 200)) <= 40

    def test_never_yields_a_path_separator(self):
        assert "/" not in slugify("../../etc/passwd")


class TestRunNaming:
    def test_ids_are_unique(self):
        assert len({new_run_id() for _ in range(50)}) == 50

    def test_ids_sort_chronologically(self):
        # Time-prefixed so a directory listing reads in run order.
        first = new_run_id()
        second = new_run_id()
        assert first[:8] == second[:8]  # same date prefix
        assert len(first.split("-")) == 3

    def test_runs_default_to_the_home_directory(self):
        # Not the CWD: on WSL a checkout under /mnt/c is exactly where ffmpeg's
        # faststart rewrite hits a Windows file lock.
        assert default_runs_root().startswith(os.path.expanduser("~"))

    def test_workdir_includes_a_source_hint(self):
        path = workdir_for("/runs", "20260727-100000-abc123", "https://youtu.be/xyz")
        assert path.startswith("/runs/20260727-100000-abc123")
        assert "xyz" in path

    def test_workdir_without_a_usable_hint_is_just_the_id(self):
        assert workdir_for("/runs", "id1", "") == "/runs/id1"

    def test_a_hostile_source_cannot_escape_the_runs_root(self):
        path = workdir_for("/runs", "id1", "../../etc/passwd")
        assert os.path.dirname(path) == "/runs"


class TestRunRecord:
    def _record(self):
        return RunRecord.create("id1", "https://x/y", "/w/id1")

    def test_starts_with_every_phase_pending(self):
        record = self._record()
        assert [p.name for p in record.phases] == list(PHASES)
        assert all(p.status == "pending" for p in record.phases)
        assert record.progress == 0.0

    def test_progress_tracks_completed_phases(self):
        record = self._record()
        record.phases[0].status = "done"
        record.phases[1].status = "done"
        assert record.progress == pytest.approx(2 / 7)

    def test_log_is_bounded(self):
        # A server left running for days must not accumulate unbounded memory.
        record = self._record()
        for i in range(MAX_LOG_LINES + 120):
            record.append_log(f"line {i}")
        assert len(record.log) == MAX_LOG_LINES
        assert record.log[-1] == f"line {MAX_LOG_LINES + 119}"

    def test_blank_log_lines_are_dropped(self):
        record = self._record()
        record.append_log("   ")
        record.append_log("")
        assert record.log == []

    def test_to_dict_can_omit_the_log(self):
        record = self._record()
        record.append_log("hello")
        assert "log" in record.to_dict()
        assert "log" not in record.to_dict(include_log=False)


class TestRunRegistry:
    def test_create_registers_a_run(self, tmp_path):
        registry = RunRegistry(str(tmp_path))
        record = registry.create("https://x/y")
        assert registry.get(record.id) is record
        assert record.work_dir.startswith(str(tmp_path))

    def test_list_is_newest_first(self, tmp_path):
        registry = RunRegistry(str(tmp_path))
        first = registry.create("a")
        second = registry.create("b")
        assert [r.id for r in registry.list()] == [second.id, first.id]

    def test_active_finds_an_unfinished_run(self, tmp_path):
        registry = RunRegistry(str(tmp_path))
        record = registry.create("a")
        assert registry.active() is record
        registry.update(record.id, status="done")
        assert registry.active() is None

    def test_unknown_run_is_none(self, tmp_path):
        assert RunRegistry(str(tmp_path)).get("nope") is None
        assert RunRegistry(str(tmp_path)).snapshot("nope") is None


class TestRehydrate:
    """Restarting the server must not look like the user's clips vanished."""

    def _finished_run_on_disk(self, root, name, artifacts):
        work = root / name
        work.mkdir(parents=True)
        (work / "workspace.json").write_text(
            json.dumps({"schema_version": 1, "artifacts": artifacts})
        )
        return work

    def _all_seven(self):
        return {p: {"phase": p} for p in PHASES}

    def test_adopts_a_completed_run(self, tmp_path):
        self._finished_run_on_disk(
            tmp_path, "20260727-100000-abc123-video",
            {**self._all_seven(),
             "ingest": {"phase": "ingest", "source": {"url": "https://youtu.be/x"}}},
        )
        registry = RunRegistry(str(tmp_path))
        assert registry.rehydrate() == 1

        record = registry.get("20260727-100000-abc123")
        assert record is not None
        assert record.status == "done"
        assert record.source == "https://youtu.be/x"
        assert record.progress == 1.0

    def test_an_interrupted_run_comes_back_as_failed(self, tmp_path):
        # Not as "running": nothing is going to finish it, and a UI showing a
        # spinner forever is worse than an honest failure.
        self._finished_run_on_disk(
            tmp_path, "20260727-100000-abc123",
            {"ingest": {"phase": "ingest"}, "transcribe": {"phase": "transcribe"}},
        )
        registry = RunRegistry(str(tmp_path))
        registry.rehydrate()
        record = registry.get("20260727-100000-abc123")
        assert record.status == "failed"
        assert "interrupted" in record.error
        assert record.phase("ingest").status == "done"
        assert record.phase("rank").status == "pending"

    def test_is_newest_first(self, tmp_path):
        for name in ("20260101-100000-aaaaaa", "20260727-100000-bbbbbb"):
            self._finished_run_on_disk(tmp_path, name, self._all_seven())
        registry = RunRegistry(str(tmp_path))
        registry.rehydrate()
        assert registry.list()[0].id == "20260727-100000-bbbbbb"

    def test_does_not_duplicate_an_in_memory_run(self, tmp_path):
        registry = RunRegistry(str(tmp_path))
        record = registry.create("https://youtu.be/x")
        os.makedirs(record.work_dir, exist_ok=True)
        with open(os.path.join(record.work_dir, "workspace.json"), "w") as fh:
            json.dump({"artifacts": {"ingest": {"phase": "ingest"}}}, fh)

        assert registry.rehydrate() == 0
        assert len(registry.list()) == 1

    def test_ignores_directories_that_are_not_workspaces(self, tmp_path):
        (tmp_path / "not-a-run").mkdir()
        (tmp_path / "stray.txt").write_text("x")
        assert RunRegistry(str(tmp_path)).rehydrate() == 0

    def test_a_corrupt_manifest_is_skipped_not_fatal(self, tmp_path):
        work = tmp_path / "20260727-100000-abc123"
        work.mkdir()
        (work / "workspace.json").write_text("{not json")
        assert RunRegistry(str(tmp_path)).rehydrate() == 0

    def test_a_missing_runs_root_is_fine(self, tmp_path):
        assert RunRegistry(str(tmp_path / "nope")).rehydrate() == 0


class TestClipPath:
    def _run_with_report(self, tmp_path, clips):
        work = tmp_path / "run"
        (work / "reports").mkdir(parents=True)
        (work / "renders").mkdir(parents=True)
        (work / "reports" / "draft.json").write_text(json.dumps({"clips": clips}))
        return str(work)

    def test_resolves_a_rendered_clip(self, tmp_path):
        work = self._run_with_report(tmp_path, [{"file": "renders/clip_01.mp4"}])
        (tmp_path / "run" / "renders" / "clip_01.mp4").write_bytes(b"\x00")
        assert clip_path(work, 0) == os.path.realpath(
            str(tmp_path / "run" / "renders" / "clip_01.mp4")
        )

    def test_absolute_paths_inside_the_run_are_accepted(self, tmp_path):
        target = tmp_path / "run" / "renders" / "clip_01.mp4"
        work = self._run_with_report(tmp_path, [{"file": str(target)}])
        target.write_bytes(b"\x00")
        assert clip_path(work, 0) == os.path.realpath(str(target))

    def test_a_path_escaping_the_renders_dir_is_refused(self, tmp_path):
        # The report is written by this pipeline, but it is still a file on disk
        # that could have been edited; its paths are claims to verify.
        secret = tmp_path / "secret.txt"
        secret.write_text("private")
        work = self._run_with_report(tmp_path, [{"file": "../../secret.txt"}])
        assert clip_path(work, 0) is None

    def test_an_absolute_path_outside_the_run_is_refused(self, tmp_path):
        secret = tmp_path / "secret.mp4"
        secret.write_bytes(b"\x00")
        work = self._run_with_report(tmp_path, [{"file": str(secret)}])
        assert clip_path(work, 0) is None

    @pytest.mark.parametrize("index", [-1, 1, 99])
    def test_out_of_range_index_is_none(self, tmp_path, index):
        work = self._run_with_report(tmp_path, [{"file": "renders/a.mp4"}])
        (tmp_path / "run" / "renders" / "a.mp4").write_bytes(b"\x00")
        assert clip_path(work, index) is None

    def test_unrendered_clip_is_none(self, tmp_path):
        work = self._run_with_report(tmp_path, [{"file": None}])
        assert clip_path(work, 0) is None

    def test_missing_report_is_none(self, tmp_path):
        assert clip_path(str(tmp_path), 0) is None
        assert load_report(str(tmp_path)) is None

    def test_corrupt_report_is_none(self, tmp_path):
        work = tmp_path / "run"
        (work / "reports").mkdir(parents=True)
        (work / "reports" / "draft.json").write_text("{not json")
        assert load_report(str(work)) is None


class TestExecuteRun:
    def _registry_and_record(self, tmp_path):
        registry = RunRegistry(str(tmp_path))
        return registry, registry.create("https://x/y")

    def test_runs_every_phase_in_order(self, tmp_path):
        registry, record = self._registry_and_record(tmp_path)
        seen = []

        def runner(phase, work_dir, *, source=None, options=None):
            seen.append(phase)
            return {"phase": phase, "clips": 2}

        execute_run(registry, record, PhaseOptions(), phase_runner=runner)
        assert seen == list(PHASES)
        assert record.status == "done"
        assert record.progress == 1.0

    def test_only_ingest_receives_the_source(self, tmp_path):
        registry, record = self._registry_and_record(tmp_path)
        sources = {}

        def runner(phase, work_dir, *, source=None, options=None):
            sources[phase] = source
            return {}

        execute_run(registry, record, PhaseOptions(), phase_runner=runner)
        assert sources["ingest"] == "https://x/y"
        assert all(v is None for k, v in sources.items() if k != "ingest")

    def test_a_failure_stops_the_sequence_and_is_recorded(self, tmp_path):
        registry, record = self._registry_and_record(tmp_path)
        seen = []

        def runner(phase, work_dir, *, source=None, options=None):
            seen.append(phase)
            if phase == "rank":
                raise ToolFailureError("deepseek said no")
            return {}

        execute_run(registry, record, PhaseOptions(), phase_runner=runner)
        # Every later phase depends on this one, so stopping is correct.
        assert seen == ["ingest", "transcribe", "rank"]
        assert record.status == "failed"
        assert "deepseek said no" in record.error
        assert record.phase("rank").status == "failed"
        assert record.phase("cut").status == "pending"

    def test_an_unexpected_exception_is_captured_not_raised(self, tmp_path):
        # A worker thread dying silently would strand the UI at "running".
        registry, record = self._registry_and_record(tmp_path)

        def runner(phase, work_dir, *, source=None, options=None):
            raise RuntimeError("something odd")

        execute_run(registry, record, PhaseOptions(), phase_runner=runner)
        assert record.status == "failed"
        assert "RuntimeError" in record.error

    def test_stderr_from_a_phase_lands_in_the_log(self, tmp_path):
        registry, record = self._registry_and_record(tmp_path)

        def runner(phase, work_dir, *, source=None, options=None):
            if phase == "cut":
                print("   ✂️  clip 1: start 10.00→9.50s", file=__import__("sys").stderr)
            return {}

        execute_run(registry, record, PhaseOptions(), phase_runner=runner)
        assert any("clip 1" in line for line in record.log)

    def test_stderr_is_restored_afterwards(self, tmp_path):
        import sys

        registry, record = self._registry_and_record(tmp_path)
        before = sys.stderr
        execute_run(
            registry, record, PhaseOptions(),
            phase_runner=lambda *a, **k: {},
        )
        assert sys.stderr is before

    def test_stderr_is_restored_even_when_a_phase_explodes(self, tmp_path):
        import sys

        registry, record = self._registry_and_record(tmp_path)
        before = sys.stderr
        execute_run(
            registry, record, PhaseOptions(),
            phase_runner=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert sys.stderr is before

    def test_phase_summaries_are_recorded(self, tmp_path):
        registry, record = self._registry_and_record(tmp_path)

        def runner(phase, work_dir, *, source=None, options=None):
            return {"clips": 3, "saving": "90% smaller", "words": 100, "speakers": [0, 1]}

        execute_run(registry, record, PhaseOptions(), phase_runner=runner)
        assert record.phase("ingest").detail == "90% smaller"
        assert record.phase("transcribe").detail == "100 words, 2 speaker(s)"
        assert record.phase("render").detail == "3 clips"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "runs")))


class TestHealth:
    def test_reports_environment_readiness(self, client, monkeypatch):
        monkeypatch.setenv("DEEPGRAM_API_KEY", "dg")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        body = client.get("/api/health").json()
        assert body["keys"]["deepgram"] is True
        assert body["keys"]["gemini"] is False
        assert "deepgram" in body["transcribers"]
        assert body["busy"] is False

    def test_blank_key_counts_as_absent(self, client, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "   ")
        assert client.get("/api/health").json()["keys"]["deepseek"] is False


class TestIndex:
    def test_serves_the_page(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert "AI-Clipper Pro" in res.text

    def test_page_loads_nothing_from_the_network(self, client):
        """Self-contained: works offline and tells no third party what you clip.

        Checks for tags that would actually *fetch* something. An ``http://`` in
        the text is not by itself a request — the inline favicon carries the SVG
        XML namespace, which no browser ever resolves.
        """
        import re

        text = client.get("/").text
        remote = re.findall(
            r"""<(?:script|link|img|iframe)\b[^>]*(?:src|href)\s*=\s*["']https?://[^"']+""",
            text,
            re.IGNORECASE,
        )
        assert remote == []
        # And nothing is pulled in at runtime either.
        assert "importScripts" not in text
        for call in re.findall(r"""fetch\(\s*["'`]([^"'`]*)""", text):
            assert call.startswith("/"), f"non-relative fetch: {call}"


class TestStartRun:
    def _patch_worker(self, client, monkeypatch, fn=None):
        started = []
        monkeypatch.setattr(
            client.app.state.worker, "start",
            fn or (lambda record, options: started.append((record, options))),
        )
        return started

    def test_creates_a_run_and_starts_the_worker(self, client, monkeypatch):
        started = self._patch_worker(client, monkeypatch)
        res = client.post("/api/runs", json={"source": "https://youtu.be/abc"})
        assert res.status_code == 201
        body = res.json()
        assert body["status"] == "queued"
        assert len(body["phases"]) == 7
        assert started[0][0].source == "https://youtu.be/abc"

    def test_options_reach_the_worker(self, client, monkeypatch):
        started = self._patch_worker(client, monkeypatch)
        client.post("/api/runs", json={
            "source": "https://youtu.be/xxx", "rank_provider": "gemini",
            "max_clips": 4, "instructions": "funny bits", "no_silence": True,
        })
        options = started[0][1]
        assert options.rank_provider == "gemini" and options.max_clips == 4
        assert options.instructions == "funny bits" and options.no_silence is True

    def test_centred_defaults_on(self, client, monkeypatch):
        # cv2/MediaPipe is not installed in a plain pip install, so the default
        # must be the mode that works everywhere.
        started = self._patch_worker(client, monkeypatch)
        client.post("/api/runs", json={"source": "https://youtu.be/xxx"})
        assert started[0][1].centred is True

    def test_refuses_a_second_concurrent_run(self, client, monkeypatch):
        self._patch_worker(client, monkeypatch)
        assert client.post("/api/runs", json={"source": "https://youtu.be/aaa"}).status_code == 201
        second = client.post("/api/runs", json={"source": "https://youtu.be/bbb"})
        assert second.status_code == 409
        assert "already in progress" in second.json()["detail"]

    @pytest.mark.parametrize("body", [{}, {"source": ""}, {"source": "x" * 3000}])
    def test_rejects_a_bad_body(self, client, body):
        assert client.post("/api/runs", json=body).status_code == 422

    def test_an_unusable_source_is_refused_before_a_run_exists(self, client):
        # A typo should land next to the input field, not create a run that
        # fails a second later and clutters the history.
        res = client.post("/api/runs", json={"source": "file:///etc/passwd"})
        assert res.status_code == 400
        assert "unsupported source scheme" in res.json()["detail"]
        assert client.get("/api/runs").json()["runs"] == []

    def test_a_missing_local_file_is_refused_up_front(self, client):
        res = client.post("/api/runs", json={"source": "/does/not/exist.mp4"})
        assert res.status_code == 400
        assert "not found" in res.json()["detail"]

    def test_a_local_video_that_exists_is_accepted(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(client.app.state.worker, "start", lambda *a: None)
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00")
        assert client.post("/api/runs", json={"source": str(video)}).status_code == 201

    def test_a_remote_url_is_not_platform_checked_here(self, client, monkeypatch):
        # That policy belongs to the downloader; duplicating it would be a
        # second allow-list to keep in step.
        monkeypatch.setattr(client.app.state.worker, "start", lambda *a: None)
        assert client.post(
            "/api/runs", json={"source": "https://example.com/v.mp4"}
        ).status_code == 201

    def test_out_of_range_options_are_rejected(self, client):
        assert client.post(
            "/api/runs", json={"source": "https://youtu.be/aaa", "max_clips": 999}
        ).status_code == 422
        assert client.post(
            "/api/runs", json={"source": "https://youtu.be/aaa", "crf": 99}
        ).status_code == 422


class TestRunReadback:
    def test_unknown_run_is_404(self, client):
        assert client.get("/api/runs/nope").status_code == 404
        assert client.get("/api/runs/nope/report").status_code == 404
        assert client.get("/api/runs/nope/clips/0/video").status_code == 404

    def test_lists_runs(self, client, monkeypatch):
        monkeypatch.setattr(client.app.state.worker, "start", lambda *a: None)
        client.post("/api/runs", json={"source": "https://youtu.be/aaa"})
        body = client.get("/api/runs").json()
        assert len(body["runs"]) == 1
        assert "log" not in body["runs"][0]  # list view stays small

    def test_report_404s_until_the_run_produces_one(self, client, monkeypatch):
        monkeypatch.setattr(client.app.state.worker, "start", lambda *a: None)
        run_id = client.post("/api/runs", json={"source": "https://youtu.be/aaa"}).json()["id"]
        assert client.get(f"/api/runs/{run_id}/report").status_code == 404

    def test_serves_the_report_and_the_clip_once_present(self, client, monkeypatch):
        monkeypatch.setattr(client.app.state.worker, "start", lambda *a: None)
        run = client.post("/api/runs", json={"source": "https://youtu.be/aaa"}).json()
        work = run["work_dir"]

        os.makedirs(os.path.join(work, "reports"), exist_ok=True)
        os.makedirs(os.path.join(work, "renders"), exist_ok=True)
        video = os.path.join(work, "renders", "clip_01.mp4")
        with open(video, "wb") as fh:
            fh.write(b"\x00" * 64)
        with open(os.path.join(work, "reports", "draft.json"), "w") as fh:
            json.dump({"clips": [{"rank": 1, "title": "A", "file": video}]}, fh)
        with open(os.path.join(work, "reports", "draft.md"), "w") as fh:
            fh.write("# Clip draft")

        assert client.get(f"/api/runs/{run['id']}/report").json()["clips"][0]["title"] == "A"
        assert "Clip draft" in client.get(f"/api/runs/{run['id']}/report.md").text

        res = client.get(f"/api/runs/{run['id']}/clips/0/video")
        assert res.status_code == 200
        assert res.headers["content-type"] == "video/mp4"
        assert len(res.content) == 64
        # Inline, so the page can preview it in a <video> element; an
        # attachment disposition would make the browser save it instead.
        assert "attachment" not in res.headers.get("content-disposition", "")

    def test_the_download_variant_is_an_attachment_with_a_real_name(self, client, monkeypatch):
        monkeypatch.setattr(client.app.state.worker, "start", lambda *a: None)
        run = client.post("/api/runs", json={"source": "https://youtu.be/aaa"}).json()
        work = run["work_dir"]
        os.makedirs(os.path.join(work, "reports"), exist_ok=True)
        os.makedirs(os.path.join(work, "renders"), exist_ok=True)
        video = os.path.join(work, "renders", "clip_01_Best_bit.mp4")
        with open(video, "wb") as fh:
            fh.write(b"\x00")
        with open(os.path.join(work, "reports", "draft.json"), "w") as fh:
            json.dump({"clips": [{"file": video}]}, fh)

        res = client.get(f"/api/runs/{run['id']}/clips/0/video?download=1")
        disposition = res.headers.get("content-disposition", "")
        assert "attachment" in disposition
        # Without this the saved file would be called "video".
        assert "clip_01_Best_bit.mp4" in disposition

    def test_a_clip_outside_the_run_is_not_served(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(client.app.state.worker, "start", lambda *a: None)
        run = client.post("/api/runs", json={"source": "https://youtu.be/aaa"}).json()
        secret = tmp_path / "secret.mp4"
        secret.write_bytes(b"private")

        os.makedirs(os.path.join(run["work_dir"], "reports"), exist_ok=True)
        with open(os.path.join(run["work_dir"], "reports", "draft.json"), "w") as fh:
            json.dump({"clips": [{"file": str(secret)}]}, fh)

        assert client.get(f"/api/runs/{run['id']}/clips/0/video").status_code == 404


class TestCrossOriginGuard:
    def test_rejects_a_request_another_site_told_the_browser_to_send(self, client):
        # Loopback alone does not stop a page on the open web POSTing here.
        res = client.post(
            "/api/runs",
            json={"source": "https://youtu.be/aaa"},
            headers={"Origin": "https://evil.example"},
        )
        assert res.status_code == 403

    def test_allows_the_apps_own_origin(self, client, monkeypatch):
        monkeypatch.setattr(client.app.state.worker, "start", lambda *a: None)
        res = client.post(
            "/api/runs",
            json={"source": "https://youtu.be/aaa"},
            headers={"Origin": "http://localhost:8720"},
        )
        assert res.status_code == 201

    def test_allows_a_request_with_no_origin_at_all(self, client):
        # curl, and the page's own same-origin fetches, send none.
        assert client.get("/api/health").status_code == 200


class TestDomainErrorMapping:
    def test_pipeline_errors_become_400_not_500(self, client, monkeypatch):
        def boom(record, options):
            raise ValidationError("workspace is in a bad state")

        monkeypatch.setattr(client.app.state.worker, "start", boom)
        res = client.post("/api/runs", json={"source": "https://youtu.be/abc"})
        assert res.status_code == 400
        assert "workspace is in a bad state" in res.json()["detail"]


class TestEndToEndThroughTheApp:
    def test_a_stubbed_run_completes_and_serves_its_clips(self, client, monkeypatch):
        """The real worker thread, with only the phases themselves stubbed."""
        def fake_run_phase(phase, work_dir, *, source=None, options=None):
            if phase == "export":
                os.makedirs(os.path.join(work_dir, "reports"), exist_ok=True)
                os.makedirs(os.path.join(work_dir, "renders"), exist_ok=True)
                video = os.path.join(work_dir, "renders", "clip_01.mp4")
                with open(video, "wb") as fh:
                    fh.write(b"\x00" * 32)
                with open(os.path.join(work_dir, "reports", "draft.json"), "w") as fh:
                    json.dump(
                        {"clips": [{"rank": 1, "title": "Best bit", "score": 9.1,
                                    "file": video, "rendered": True}]},
                        fh,
                    )
            return {"phase": phase, "clips": 1}

        monkeypatch.setattr("clipper_pro.web.worker.run_phase", fake_run_phase)

        run = client.post("/api/runs", json={"source": "https://youtu.be/aaa"}).json()
        deadline = time.time() + 10
        while time.time() < deadline:
            state = client.get(f"/api/runs/{run['id']}").json()
            if state["status"] in ("done", "failed"):
                break
            time.sleep(0.05)

        assert state["status"] == "done", state.get("error")
        assert state["progress"] == 1.0
        assert client.get(f"/api/runs/{run['id']}/report").json()["clips"][0]["title"] == "Best bit"
        assert client.get(f"/api/runs/{run['id']}/clips/0/video").status_code == 200
