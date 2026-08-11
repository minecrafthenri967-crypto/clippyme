# CLAUDE.md

Guidance for Claude Code when working in this repository. Current-state only —
design history and rationale live in `docs/` (see the pointers at the bottom).

## Project

ClippyMe is a self-hosted AI video platform that turns long-form videos
(YouTube or local uploads) into viral 9:16 vertical shorts. Fork of OpenShorts.
Backend: FastAPI + a subprocess video pipeline. Frontend: React 18 + Vite 6 +
Tailwind v4.

## Repo layout

Python backend is src-layout under `src/clippyme/` (`pip install -e .`):

- `api/` — `app.py` (thin FastAPI layer: job-lifecycle routes, middleware,
  static mounts, lifespan), `config_routes.py` (the config-family `APIRouter`:
  keys/cookies/fonts/logo/zernio/models — routes that touch no job runtime
  state, `include_router`ed by app.py), `schemas.py` (Pydantic request models),
  `security.py` (trusted-origin/rate limit/API-token gates).
- `domain/` — endpoint logic. `clip_resolve.py` (shared `resolve_clip()`: job
  dir → latest metadata → clip entry → path, used by every per-clip endpoint),
  `job_submission.py` (`submit_job()` + queue-full rollback),
  `job_runner.py` (`make_run_job()` — the per-job subprocess loop),
  `job_actions.py` (cancel/stop bodies), `job_journal.py` (crash-safe queue
  journal + startup recovery), `job_worker.py` (queue dispatch + retention
  cleanup), `job_results.py` (`build_main_cmd`, partial/final result loaders),
  `job_artifacts.py` (atomic metadata IO; `save_job_campaign`/`load_job_campaign`
  tag a job with the Zernio profile it was submitted under — a small sidecar
  next to the metadata, not a field inside it, since the cv2-bound pipeline
  subprocess never needs to know), `job_control.py` (status machine +
  psutil process-tree suspend/resume), `publish_service.py` (Zernio publish
  flow), `compose.py` (layer pipeline), `clip_endpoints.py` (smart-cut runner,
  history restore), `smartcut.py` (impure orchestrator: ffmpeg/auto-editor
  render, ffprobe, per-clip locks, `smart_cut`) + `smartcut_ops.py` (pure,
  host-tested: filler index, drop-range math, `analyze_silences`, v3 timeline
  builder — re-exported by smartcut.py for back-compat), `subtitles.py`,
  `hooks.py`, `logo.py`, `banner.py` (attribution banner: platform logo +
  handle, `suggest_banner` URL parsing, lazy-cairosvg raster, `attach`
  letterbox positioning), `live_monitor.py` (`LiveMonitorRegistry` +
  per-platform strategies: multi-channel Kick/Twitch/YouTube monitor, live +
  vod modes, global `picked_slots` publish spacing, state in
  `data/live_monitor.json`; durable auto-resume via `resume_on_start`;
  runtime config updates `POST /api/live-monitor/{id}/config` (allow-listed
  fields, apply to future clips); start-time `catchup: backfill|live_only`;
  publishing pause/resume `POST /api/live-monitor/{id}/publishing` with a
  persisted pending queue that auto-drains on resume/restart; after a
  confirmed Zernio publish the clip's artifacts are deleted and its metadata
  entry marked `deleted_after_publish` — positions in `shorts` stay stable,
  consumers pass `original_index`; each monitor's `cfg["zernio_profile"]`
  (default `"default"`) selects which named Zernio account it publishes
  through — `picked_slots`/the publish `asyncio.Lock` are scoped PER PROFILE
  in the registry, not global, so a rate-limited campaign account can't stall
  an unrelated one; a pre-profile `data/live_monitor.json` migrates its flat
  `picked_slots` list into the `"default"` bucket transparently on load; each
  monitor also carries an optional free-text `label` — purely cosmetic (not
  an identity field, so it's runtime-renamable unlike `zernio_profile`) for
  telling monitors apart in the dashboard list once several run across
  different campaigns/Zernio profiles at once),
  `grade.py`, `clip_qa.py`, `clip_edit_ai.py`, `player_image.py` (player-image
  library CRUD + normalized name matching + the timed overlay render, mirrors
  `logo.py`'s shape), `player_detect.py` (the single-shot Gemini call
  detecting spoken athlete names, mirrors `clip_edit_ai.py`'s size but reuses
  `gemini_parser`'s JSON-repair chain + `gemini_request`'s model-fallback
  instead of a bespoke parser), `history_service.py` (`scan_history` surfaces
  each job's tagged `zernioProfile` — "default" for untagged jobs — so an
  external approval bot scoped to ONE campaign can filter `GET /api/history`
  to only its own clips instead of every bot posting every clip),
  `encode.py` (single source of x264 settings for every render pass; a
  delivered clip stacks ~5 generations, so encodes that only FEED another
  pass — source slice, reframe master, every compose layer — use
  `x264_intermediate_crf()` (default 14, clamped to never exceed the delivery
  `x264_crf()` of 18) rather than the delivery CRF),
  `errors.py` (domain exceptions mapped to HTTP by one app-level handler).
- `pipeline/` — `main.py` (CLI orchestrator), `reframe.py` (orchestrator:
  scene analysis, frame strategies, render loops, `process_video_to_vertical`),
  `reframe_track.py` (pure tracking classes — host-tested, no cv2),
  `reframe_detect.py` (YOLO/MediaPipe detectors), `reframe_ops.py` (pure
  camera math), `cut_ops.py` (clip-edge snapping primitives + the
  `snap_clips_to_transcript`/`compute_neighbor_bounds` batch orchestration),
  `run_ops.py` (pure entrypoint helpers: `resolve_output_dir`,
  `build_cut_command`), `gemini_request.py` (prompt template + pricing +
  prompt/cost/retry-classification — the pure half of `get_viral_clips`; the
  per-word payload is TOON-encoded (`encode_words_toon`, ~50% smaller than
  JSON) while the response contract stays JSON),
  `media_probe.py` (ffprobe + silencedetect wrappers), `texttiling_ops.py`
  (no-AI topic-segmentation fallback), `deepgram_transcribe.py`,
  `elevenlabs_transcribe.py`, `gemini_service.py`, `gemini_parser.py`,
  `scene_detection.py`, `download.py`, `postprocess.py`, `diarization.py`,
  `hardware.py`, `transcribe_cache.py`. `main.py` imports cv2/torch at the
  top → NOT host-importable: pure logic goes in the modules above, never
  inline in `main.py` (it re-imports moved names for back-compat).
- `netutil.py` — bounded DNS resolution (daemon thread + timeout) shared by
  the SSRF guards in `download.py` / `social_publisher.py`; never mutate
  `socket.setdefaulttimeout` (it doesn't even apply to `getaddrinfo`).
- `integrations/` — `social_publisher.py` (Zernio client + SmartScheduler),
  `auto_editor_updater.py` (auto-editor binary self-update),
  `kick_client.py` (Kick channel/VOD JSON via curl_cffi, Cloudflare profile
  rotation), `twitch_client.py` (Helix app-token client: streams/users/videos),
  `youtube_feed.py` (UULF long-form RSS polling — Shorts structurally
  excluded).
- `storage/` — `config_store.py` (persisted config in `data/config.json`;
  Zernio settings support named profiles — `profile="default"` reproduces the
  original single-account `zernio` key byte-for-byte, any other profile id
  reads/writes a sibling `zernio_profiles` namespace, so a multi-campaign
  install can hold several independent Zernio accounts side by side; saved
  caption presets — e.g. one per seller in a multi-account campaign, with its
  mandatory hashtags/mention pre-written — live in a `caption_presets`
  namespace, upserted by id, and fill the Publish caption field with one
  click via `PublishModal`).

A second, independent package lives under `src/clipper_pro/` — the AI-Clipper
Pro pipeline. It is CLI-driven (`clipper-pro` / `python -m clipper_pro`), one
subcommand per phase, and does **not** import the FastAPI app or the ClippyMe
job runtime; it reuses `clippyme.pipeline.download` for the URL allow-list and
will port `cut_ops` / `reframe_*` maths rather than re-deriving them. Phases:
`ingest` (done — download + mono-16 kHz FLAC extraction), `transcribe` (done —
provider abstraction over the repo's Deepgram/ElevenLabs backends), `rank`
(done — 5-axis rubric behind DeepSeek-V3/Gemini providers, SQLite prompt cache,
reusing `gemini_request.encode_words_toon` and `gemini_parser`'s JSON-repair
chain), `cut` (done — thin adapter over `cut_ops.snap_clips_to_transcript`; the
cascade maths is NOT duplicated), `reframe` (done — offline speaker timeline +
200 ms camera lead + Savitzky-Golay smoothing, all pure/host-tested; only
`reframe/detect.py` needs cv2/MediaPipe), `render` (done — RDP-simplified
trajectory as a piecewise-linear `crop=x` expression plus optional burned-in
karaoke captions and an optional whole-clip text hook, one ffmpeg pass per
clip),
`export` (done — scored draft report as JSON + Markdown twins from one assembled
document). **All seven phases are implemented**; `clipper-pro --help` is the
current surface. Two front ends drive it and must not
drift: `clipper_pro/pipeline.py` owns the per-phase orchestration
(`run_phase(phase, work_dir, source=, options=)` + the workspace loaders), and
both `cli.py` (argv → `PhaseOptions`) and `web/` (HTTP body → `PhaseOptions`)
are thin translators over it. `clipper-pro web` serves a localhost-only UI
(`web/app.py` routes, `web/runs.py` pure run state, `web/worker.py` background
thread with stderr tee, one self-contained `web/static/index.html`); it binds
loopback, rejects cross-origin requests, serves clips by index with a
realpath containment check, and defaults runs to `~/clipper-pro-runs` — never
the CWD, since a WSL checkout under `/mnt/c` is where ffmpeg's faststart
rewrite hits a Windows file lock. `clipper-pro watch` (`watch/`) is a **driver,
not a phase**: it polls YouTube uploads feeds (reusing
`clippyme.integrations.youtube_feed`, so Shorts stay structurally excluded) and
calls `run_phase` per new upload — one workspace per video, named after the
video id so a retry resumes. `watch/state_ops.py` is pure/host-tested and owns
the three money-safety rules: first sight of a channel adopts the ~15 uploads
already in the feed **without processing them** (`catchup=live_only`, the
default; `backfill` is the explicit opt-in that bills per video), a failing
video is retried at most `max_attempts` times and then left alone, and phases
already recorded in a run's manifest are skipped so a retry after a render
failure does not re-bill transcription and ranking. State is
`<runs-dir>/watch-state.json`, written atomically (0o600) after every video;
a video id from a feed is re-validated before it becomes a directory name. The
watcher deliberately does **not** publish. Cross-phase
data contracts are the dataclasses in `clipper_pro/types.py`; per-run state is
a workspace directory + manifest (`clipper_pro/workspace.py`) — each phase
records its artifact there, so the next one needs no repeated paths. Same
purity rule as the pipeline: `*_ops.py` modules are stdlib-only and host-tested,
the modules beside them do the I/O. Env knobs are `CLIPPER_PRO_*`, documented in
`.env.example`. Tests live in `tests/clipperpro/` (spelled without the
underscore so the test package cannot shadow the real one).

⚠️ Caption timebase: phase 6 seeks with `-ss` AFTER `-i`, and with output
seeking the filter graph still sees each frame's ORIGINAL timestamp. ASS events
must therefore be written in **source time**, not clip-relative — a
clip-relative document makes captions vanish exactly `clip.start` seconds in.
`render/captions.py` filters the words itself and passes `clip_start=0.0` so the
generator does not rebase. The `ass=` filter goes LAST in the graph (after
crop→scale) because the ASS declares PlayRes 1080x1920, the delivery frame.
Captions reuse `clippyme.domain.subtitles.generate_ass_karaoke` (semantic line
breaks, six presets) scaled by `DEFAULT_FONT_SCALE` — the presets are sized
~3% of frame height, short-form captions want ~5%. ⚠️ That scale is a RATIO to
the shared presets, not an independent size: changing `SUBTITLE_PRESETS`
sizes moves clipper-pro's delivered captions too, so the two move together
(the presets went 35-43 → 52-64, and the scale 2.4 → 1.6 to hold the same
delivered size). `tests/clipperpro/test_captions.py` pins the resulting
percentage band and fails if only one side is edited.

⚠️ Text hooks (`render/hooks_ops.py` pure + `render/hooks.py` writer) follow the
same source-time rule, and three product constraints that are NOT negotiable
without asking: the single ASS event spans the **whole clip** (ClippyMe's own
hook overlay stops at 4s; here a looping viewer must still see it), `HOOK_POSITIONS`
offers **no centre option** (after the 9:16 crop the centre is the speaker's
face), and the border is a **setting** — `boxed_light`/`boxed_dark` use ASS
`BorderStyle=3` (the outline colour paints an opaque slab), `outline` a thick
stroke, `shadow` neither. The hook filter is appended after the caption filter so
it wins where they overlap, and its text comes from `Candidate.hook_text`, which
phase 3's ranking call returns alongside the scores — no second API bill.

⚠️ Overlap policy spans two phases: phase 3's dedupe tolerates a modest overlap
between clips (measured against the shorter one), and phase 4 must NOT veto it —
each clip renders to its own file, so shared footage is not a rendering problem.
Phase 4 only warns when snapping *added* overlap (`overlap_growth`), which would
mean the sentence stage's neighbour clamp failed. An earlier phase-4 hard check
on any overlap put the two phases in contradiction and aborted valid runs.

⚠️ Phase 2 capability asymmetry: **Deepgram Nova-3 does not tag audio events**
(laughter/applause) — only ElevenLabs Scribe does. `transcribe/base.py` declares
this per provider rather than assuming it; `require_events=True` turns a missing
capability into an error instead of an empty list that reads as "no laughter"
when it means "nobody listened for any".

Frontend lives entirely in `dashboard/src/redesign/` (`main.jsx` renders
`RedesignApp`). Shared hooks in `dashboard/src/hooks/` (incl.
`useManualTrim.js` — the modal's trim state machine), pure logic in
`dashboard/src/lib/` (incl. `applyEdit.js` — the reprocess orchestration,
`seedClipParams.js`, `trimSelection.js`, `bulkApply.js`, `taste.js`).
Subtitle/logo/grade controls are SHARED between the Create recipe and the
EditClipModal via `subtitleControls.jsx` / `layerControls.jsx` (hookStyle.jsx
pattern: fully-controlled `value` + `onChange(partial)`, per-surface `variant`
chrome, defaults resolved by thin adapters) — never re-clone these controls
per surface. `captions.jsx` is the modal shell; tab bodies live in
`editTabs.jsx` with state lifted in the shell (tabs are conditionally
rendered).
Logo placement is free-drag, not a preset list: `logoPositionEditor.jsx`
renders the logo over a user-uploaded still (screenshot in Create, a clip
frame in the edit modal) and lets it be dragged/resized, storing
`{position: {x, y}, scale}` — `x`/`y` are the same normalized
`(main_w-overlay_w)*x` fraction `domain/logo.py`'s `logo_overlay_xy` uses, so
the frontend never needs to duplicate ffmpeg's own placement math. The old
`_POSITIONS` keyword presets (`top-right` etc.) and `_LOGO_SIZE_MAP` (`S/M/L`)
still resolve server-side so old recipes/history keep rendering unchanged —
only the editor stopped writing them. `GET /api/config/logo/image` serves the
raw uploaded PNG so the editor can render the real logo, not a placeholder box.

## Commands

```bash
docker compose up --build            # primary run (backend :8000, frontend :5175)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build  # prod frontend (nginx)

# Backend host tests (fast, no CV stack) + lint
pip install -e ".[host-tests]" && pip install pytest ruff
pytest -m "not integration" -q
ruff check src/clippyme tests --select E9,F63,F7,F82

# Heavy CV/ML integration tests (Docker only)
docker compose run --rm -u root backend sh -lc "pip install -q pytest && pytest -m integration"

# Frontend (Vitest + jsdom + testing-library)
cd dashboard && npm ci && npm test && npm run lint && npm run build
```

CI (`.github/workflows/ci.yml`): backend host suite (with report-only
coverage) + ruff bug-class rules + blocking `pip-audit`; frontend lint +
**test** (with coverage) + build; the Docker integration job runs on main
pushes or `workflow_dispatch`, pre-building the backend image with GHA layer
caching (tagged `clippyme-backend` so compose reuses it).

## Architecture

**Job lifecycle**: `POST /api/process|/api/batch` → `build_main_cmd` →
`submit_job` (in-memory `jobs` dict + `asyncio.Queue`) → `process_queue`
dispatch (semaphore, `MAX_CONCURRENT_JOBS`) → `run_job` spawns
`python -m clippyme.pipeline.main` as a subprocess and polls partial results
every 2s. Statuses: `queued → processing ⇄ paused → {completed, failed,
cancelled, stopped}` (`job_control.py` owns the guards). `stopped` keeps
finished clips; `cancelled` rmtree's everything.

**Job journal**: every status transition writes `data/jobs_journal.json`
(atomic, ACTIVE jobs only — never env secrets/Popen/logs). On startup,
`lifespan` recovery re-enqueues `queued` jobs, restores interrupted jobs whose
final result reached disk, and marks the rest `failed` (killing orphaned
pipeline trees via psutil with an argv-match guard). **`failed` is reused
deliberately**: the frontend poller terminates only on
`completed|stopped|cancelled|failed` — an unknown status polls forever.

**Pipeline (per job)**: yt-dlp download → transcription → PySceneDetect →
Gemini viral detection (5-level JSON-repair fallback chain in
`gemini_parser.py`; TextTiling topic-split as the no-AI fallback; whole-video
render as the last resort) → per-clip edge snapping (word → sentence →
waveform-silence, `cut_ops.py`) → 9:16 reframe → Ken Burns zoom (folded into
the master encode) → EBU R128 loudnorm → cover frame. The 16:9 source slice
per clip is preserved on disk (`source_*.mp4`) to enable post-hoc reframe
switching. Clip files are named from the sanitized Gemini viral title
(`run_ops.clip_output_basename`: Windows-forbidden chars/reserved names
handled, always suffixed `_clip_{i+1}`); the basename is persisted per clip
as `clip_filename` in metadata (re-dumped atomically per cut iteration) and
every consumer resolves through `clip_resolve.clip_filename_for`
(clip_filename → video_url → positional legacy fallback).

**Peak moment (cold-open teaser source)**: the SAME Gemini viral-detection call
that returns `viral_hook_text` also returns an optional `peak_start`/`peak_end`
per clip — the strongest 1-3s inside it (punchline, big reaction, payoff line),
in the same ABSOLUTE source seconds as `start`/`end`. No second API call, so no
extra bill. It exists to build a teaser that plays BEFORE the clip's real start
(the "show the payoff first, then rewind" format).
⚠️ The peak is OPTIONAL and every layer must treat it that way — a missing or
nonsensical peak costs the teaser, NEVER the clip. `ViralClip._coerce_peak_timestamp`
collapses unparseable values to `None` instead of raising (unlike `start`/`end`,
where malformed input rightly rejects the clip), and `_validate_peak_window`
CLEARS an out-of-range window rather than raising or clamping it — a window we
had to reshape is no longer the moment Gemini identified. The prompt asks for
1-3s while `MIN/MAX_PEAK_DURATION` (0.8-10s) tolerate near-misses, mirroring
`ViralClip`'s own 10-75s band vs. the 15-60s the prompt requests. The prompt
also explicitly instructs Gemini to emit `null` when no moment stands out —
a teaser opening on a flat moment performs worse than no teaser.
⚠️ `cut_ops.drop_peak_outside_clip` re-checks containment AFTER
`snap_clips_to_transcript` moves the edges: the peak is validated against
Gemini's ORIGINAL edges, and while snapping usually WIDENS them (harmless), the
waveform-silence refine can nudge `start` FORWARD past the peak
(`tests/pipeline/test_cut_ops.py` pins both directions). Out-of-range → dropped,
not clamped, same "skip, never guess" rule the player-image overlay follows.

**Transcription**: `TRANSCRIPTION_PROVIDER` = `deepgram` (default, Nova-3
REST) | `elevenlabs` (Scribe; audio-event tags feed the Gemini prompt) |
`whisper` (local). Both cloud providers silently fall back to Faster-Whisper
on any failure. All paths transcribe an extracted mono-16kHz FLAC, not the
video. Transcripts are cached 7 days under `data/cache/` keyed by URL hash.

**Cold-open teaser** (`domain/teaser.py`, toggle `"teaser"`, default OFF): replays
the clip's peak moment for ~2s BEFORE the clip plays from its real start ("show
the payoff, then rewind"). Source is the `peak_start`/`peak_end` window from the
viral-detection call (see above). One ffmpeg pass, one encode generation: teaser
and body are trimmed out of the SAME input and concatenated, with a short fade
on the teaser's tail — on **audio too**, since a hard mid-word audio cut at the
jump back is the most jarring part of the transition.
⚠️ This is the ONLY compose layer that changes the clip's DURATION, so every
later timed layer shifts by exactly the teaser's length. `_apply_teaser` returns
`(path, offset)` and `_compose_layers_impl` threads that offset into the hook's
visible window (`4 + offset`, so the teaser is covered AND the clip's own start
still gets its full 4s) and into the player-image overlay's start time. **A new
timed layer added after the teaser must consume that offset or it will fire at
the wrong moment.**
⚠️ It sits AFTER Smart Cut (which is transcript-driven on clip-relative times —
prepending footage first would desync it and make it chew the duplicated
footage) and BEFORE the hook (the teaser is the first thing anyone sees, so the
hook copy belongs on it — peak footage + hook text together is the whole point).
The window is remapped through Smart Cut's kept spans exactly like the
player-image timestamp, and a peak that was cut away — or only partially
survived — skips the teaser rather than showing a fragment.

**Compose** (`POST /api/compose/{job}/{clip}`): layers render in the order
**Grade → Subtitles → Smart Cut → Teaser → Hook → Logo → Player Image → Banner**.
Do NOT reorder — subtitles are burned before Smart Cut so their absolute timing can't
drift; grade runs first so overlays keep authored colour; logo sits above hook;
the player-image "flash" overlay (an athlete's photo, timed to the moment
their name is detected — `player_image.py` + `player_detect.py`) sits above
the brand hook/logo but strictly below the attribution banner, which always
stays topmost; the attribution banner (`banner.py`: platform logo + handle,
`attach` mode pins it under the letterbox band when `reframe_mode ==
disabled`) renders topmost as a separate pass. Grade+subtitles and hook+logo
are pass-fused (one encode each) when possible; player-image is its own
separate pass (never fused, matching banner's own "correctness over one saved
encode generation" precedent). Serialised per clip via `clip_locks.clip_lock`.

**Player image overlay**: fully-automatic — a small Gemini call
(`player_detect.detect_player_mentions`, its own JSON-repair/model-fallback
reusing `gemini_parser`/`gemini_request`) scans a clip's transcript for
spoken athlete names and returns `{player_name, timestamp, confidence}`
mentions; `player_image.match_player_image` does a normalized exact-name
match against a user-uploaded image library (`data/player_images/`, one PNG
per player). Detection runs ONCE per clip and is cached into the clip's
metadata (`clip_info["player_mentions"]`, via the new
`job_artifacts.set_clip_field` — a fresh-read-then-write helper generalized
from `record_clip_publish` so a slow write can't clobber a sibling clip's
entry) — every later compose/preview/publish re-matches for free but never
re-bills Gemini. If Smart Cut actually rendered (`compose.py` tracks whether
`_apply_smartcut` returned a changed file, not just whether the toggle was
on), the detected timestamp is remapped through
`smartcut_ops.remap_time_through_kept_segments` — a pure function that walks
`analyze_silences()`'s ordered kept-segments list to translate an original-time
moment into its Smart-Cut output-time position, or `None` if that moment was
cut away (the overlay is skipped, never guessed). The toggle
(`"player_image"`) defaults OFF everywhere, including Live Monitor's
`build_monitor_compose` recipe, since it needs the image library populated
first.
**Per-job compose recipe**: because the pipeline renders RAW and every layer
is compose-time, a consumer that composes has to know WHICH layers. The
submitting recipe (Create-tab toggles + per-layer params) therefore rides
`POST /api/process|/api/batch`'s `compose` field, is stored in the SAME
`campaign.json` sidecar as the Zernio profile (one atomic write —
`save_job_campaign(dir, profile, compose=...)`), and comes back from
`GET /api/history` as `composeRecipe`. `hook_params.text` is deliberately
absent: hook text is per clip, the recipe holds job-wide style. Every
consumer prefers the stored recipe over its own defaults — the Discord
approval bot most importantly, which used to compose from its own env vars,
so a hook configured WITH a background at a drawn position reached Discord
(and then TikTok) with none, at the bot's `HOOK_POSITION`. A `None` recipe
(old job, or a caller that sent none) means "no stored preference" and every
consumer keeps its prior behaviour.
⚠️ Live Monitor jobs have their OWN, separate recipe path — `build_monitor_compose`
(`domain/live_monitor.py`), not the Create-tab's `buildJobComposeRecipe` — because
a monitor's clips are auto-published without a human choosing per-job options.
Its start form and the running-monitor Settings drawer each carry a "Customize
subtitles" AND a "Customize hook style" drawer (the latter reuses
`hookStyle.jsx`'s shared `HookStyleControls`, style-only — no text, no
position; position stays the hardcoded `'top'` the letterboxed layout is
designed around); both feed `compose.subtitle_params`/`compose.hook_params`.
`LiveMonitor._compose_override()` folds the monitor's own dedicated `banner`
config (its Auto/Off/Custom picker, a SEPARATE `LiveMonitorStartRequest` field
from `compose`) into the same override dict `build_monitor_compose` reads —
before this, `cfg["banner"]` was validated and persisted but nothing
downstream ever read it back, so choosing "Off" or "Custom" silently kept
auto-deriving the banner from platform+channel. `_new_job_dir` now persists a
full recipe too (`_persisted_compose_recipe()`, built via `build_monitor_compose`
itself so the two can't drift, with a placeholder hook text stripped before
storage) — before this, a monitor job's `composeRecipe` was always `None`, so
the Discord approval bot fell back to its own bare env-var defaults for every
Live-Monitor-sourced clip even after the recipe machinery above shipped.
⚠️ The stream-layout editor's hook/subtitle guides are dragged on the 9:16
PREVIEW, so they are heights on the DELIVERED frame and travel as 0..1
fractions in `hook.position` / `subtitles.position` (see
`hooks.parse_hook_position_fraction`). Read them with `??`, never `||` — 0 is
a legitimate top-edge value a falsy check silently replaces with a keyword.

The pipeline renders clips RAW — every layer is compose-time. The dashboard
auto-composes on job completion (`lib/autoCompose.js` plans, `RedesignApp`
feeds the plan to the same bounded runner as bulk-apply), so the preview
already shows the Create recipe; download/publish compose too, so a clip is
never uploaded raw. Compose writes a SEPARATE `composed_clip_N` file and never
touches the raw clip or the preserved `source_*` slice, which is what keeps
post-hoc editing and reframe switching working.
Hook overlay shows only the first 4s of the clip, EXCEPT
`reframe_mode == disabled` where it stays for the whole clip.

**Reframe**: four user modes — `auto` (face tracking + per-scene strategy),
`subject` (FrameShift weighted-interest crop; legacy alias `object`),
`gaming` (facecam+gameplay split-screen), `disabled` (letterbox). Comfort mode
is default-on: within a scene the camera never moves (`collapse_scene_targets`),
zoom locks per scene. The output aspect is an explicit
`process_video_to_vertical(..., aspect_ratio=)` parameter passed by `main.py`
per job — there is no module-global. Post-hoc mode switching
(`POST /api/reframe/{job}/{clip}`) spawns `main.py --reframe-only` on the
preserved source slice. Camera/decision math lives in `reframe_ops.py`/
`reframe_track.py` (pure, host-tested) — add new reframe logic there, not in
the cv2-bound modules.
⚠️ `gaming` mode's facecam is detected ONCE up front (`_detect_gaming_facecam`
samples ~12 frames, runs the existing face detector, and calls
`reframe_ops.detect_static_facecam_region` — a face present in most samples at
a near-constant position, not a normal subject a camera would pan to follow),
never re-tracked per frame like `auto`'s cameraman. No confident static region
→ silent fallback to `auto` for that clip (reassigns `reframe_mode` itself, so
the rest of the function — comfort mode, global-smooth gating — behaves
exactly as a normal `auto` job). This is a heuristic, not a guarantee — verify
each `gaming` job's actual layout rather than assuming detection succeeded.
A user who knows their own layout can skip detection entirely by drawing a
rectangle over their own screenshot of the stream (dashboard: Create tab's
Gaming section, `facecamBoxPicker.jsx` — the screenshot itself never leaves
the browser, only the resulting `{x,y,w,h}` 0..1-fraction box is sent).
`--gaming-facecam-x/-y/-w/-h` (all four required together — a partial box is
ambiguous and falls back to detection) pin the facecam directly via
`reframe_ops.resolve_facecam_box_from_fractions`, which
`process_video_to_vertical` uses instead of `_detect_gaming_facecam` whenever
`gaming_facecam_box` is set (the default `None` still runs detection). Being
fraction-based, the box is resolution-independent — the screenshot's own
pixel size doesn't need to match the source video's, only its aspect ratio.
`create_gaming_frame` stacks the
detected/manual region (expanded to the top zone's aspect via
`expand_box_to_aspect`, never cropped smaller) over the bottom "gameplay"
zone. That bottom crop defaults to HORIZONTALLY CENTRED over the source's
full height, but `gaming_gameplay_box` (same drawn-fraction shape as the
facecam box, `--gaming-gameplay-x/y/w/h`) overrides it — the default assumes
the action sits mid-frame and that every row is game, which stops holding
once a layout bakes in a taskbar, chat panel or webcam strip. There is
deliberately NO detector counterpart: "where is the game" has no visual
signature the way a face does, so it is drawn or defaulted, never guessed.
The split sits at `reframe_ops.gaming_facecam_fraction()`
(env `REFRAME_GAMING_FACECAM_FRACTION`, default 0.45, clamped 0.15-0.75) —
in `reframe_ops` rather than cv2-bound `reframe.py` because `domain/hooks.py`
reads the same number for the `seam` hook position (`gaming_seam_overlay_y`
centres the hook box ON the cut, so hook and split can't drift apart). The
fraction also sets the top zone's ASPECT, so raising it keeps more of the
source around the streamer instead of stretching the same crop; a per-job
`--gaming-split` overrides the env so a saved layout carries its own
proportions.

**Stream layouts** (`config_store`'s `stream_layouts` namespace,
`GET/POST /api/config/stream-layouts`, `DELETE .../{id}`): one saved template
per streamer/game, mirroring the `caption_presets` shape. Holds the two SOURCE
regions (`facecam`, `gameplay`) and the three OUTPUT heights (`split`,
`hook_y`, `subtitle_y`), all as 0..1 fractions so a template drawn over a
1080p screenshot applies unchanged to a 4K source of the same aspect ratio.
Every geometry field is independently optional — pinning only the gameplay
region must not force drawing a facecam box too; absent fields fall back to
the pipeline's own defaults. A box running off the frame is REJECTED, not
clamped: silently shrinking one renders something the user never drew.
The dashboard editor is `redesign/streamLayoutEditor.jsx` — two panels,
because the two halves differ in kind: the left one draws source regions on
the uploaded screenshot, the right one is a live 9:16 preview assembled from
exactly those crops where the split/hook/subtitle guides are dragged. Those
three are positions on the DELIVERED frame and have no meaning on the source
screenshot, which is why they are not drawn on the left. The preview's
geometry lives in `lib/layoutGeometry.js` (pure, tested) and MIRRORS
`expand_box_to_aspect` — a preview that quietly disagrees with the renderer is
worse than no preview. The screenshot never leaves the browser
(`URL.createObjectURL`); only fractions are sent.
Hook and subtitle positions accept either a keyword or a 0..1 fraction
(`hooks.parse_hook_position_fraction`, `subtitles.parse_position_fraction`);
keywords keep their historical margins so existing recipes render unchanged.
⚠️ ASS `MarginV` is measured from the BOTTOM edge, so
`subtitles.margin_v_for_fraction` inverts a top-measured fraction and
subtracts half the font size to convert "bottom of the text" into "centre of
the text" — the sign is pinned by a test because getting it backwards puts
captions at the wrong end of the frame.
⚠️ `REFRAME_GLOBAL_METHOD=kalman|l2` only runs with `REFRAME_STATIC_AUTO=0`;
the default static-auto policy never reaches the trajectory smoother.
⚠️ Output canvas sizing has a CEILING and a FLOOR, set independently:
`CLIPPYME_MAX_DOWNLOAD_HEIGHT` (`download.py`) only caps how much of a
source's available resolution gets downloaded — useless once the source
itself is already below the cap. `reframe_ops.compute_output_dimensions`
(env `CLIPPYME_MIN_OUTPUT_SHORT_EDGE`, default 1080) is the floor: the render
canvas matches the downloaded source's native height 1:1 when that already
clears the floor, otherwise it's upscaled (Lanczos, via the same
scale-aware `_resize_to_output` every reframe strategy funnels through) so
the delivered clip's short edge never falls under a platform-safe minimum.
Without this floor a plain 1920x1080 landscape source's native 9:16 crop is
only 608x1080 — well under TikTok/Instagram's own recommended minimum,
regardless of how high the download-quality ceiling is set.
⚠️ Correct DIMENSIONS are not sharpness. Zoom tightens the crop, so it spends
real source pixels: at the old fixed `max_zoom=1.6` a 1080p source's 9:16 crop
shrank from 607px to 379px of real width before being stretched to a 1080px
canvas (2.85x). `reframe_ops.max_zoom_for_upscale` (env `CLIPPYME_MAX_UPSCALE`,
default 2.0) caps zoom to an enlargement budget — `SmoothedCameraman.max_zoom`
resolves it once and BOTH the streaming tracker and `build_smoothed_trajectory`
must use that value, never a literal 1.6. The cap binds on low-res sources and
is a no-op on 4K. When even the widest crop exceeds the budget, sharpness is
bounded by the SOURCE and no encode setting recovers it; `reframe.py` logs the
real-pixel→canvas factor per clip so this is visible rather than guessed at.
⚠️ `download.py`'s player-client retry chain (`tv+tv_embedded`/`web_safari`
fallback past a bot-check wall) is YouTube-extractor-specific
(`extractor_args={"youtube": ...}`) — yt-dlp ignores it for the `twitch:vod`/
`kick` extractors, so `download_youtube_video` collapses the chain to one
"default" attempt via `_is_youtube_url(url)` for those hosts instead of
uselessly repeating the identical request. `live_monitor.py`'s backfill path
(`build_backfill_cmd`/`_download_vod_range`, missed-segment recovery for
Twitch/Kick live monitors) is a SEPARATE, thinner yt-dlp invocation than
`download_youtube_video` — it now threads the same cookies/`YTDLP_PROXY`/
format-ladder/User-Agent (`download.DEFAULT_USER_AGENT`, shared so the two
paths can't drift) through, since a subscriber-only VOD or a bot-check 403
there used to fail outright with no recourse, permanently losing that missed
window once the next stream starts and `_schedule_backfill` discards a prior
session's unresolved windows.

**Smart Cut**: transcript-driven silence/filler removal rendered via a
hand-built auto-editor v3 JSON timeline (ffmpeg concat fallback if the binary
is missing), plus an audio-threshold polish pass. Manual trims arrive as
`drop_ranges` ([[start,end], …] clip-relative) and ride compose + publish.
The auto-editor binary is NOT a pip dep — Dockerfile downloads it; an opt-in
24h self-update loop refreshes it (`AUTO_EDITOR_AUTO_UPDATE=1`).

**Publish**: Zernio (TikTok/Instagram/YouTube). `publish_service.publish_clip_flow`
optionally re-composes first so uploads match the preview; `SmartScheduler`
picks Italian-prime-time slots with anti-collision. Zernio error bodies pass
through verbatim (the frontend parses per-platform 429 daily limits).

## Code rules

- **Thin handlers**: validate → call a `clippyme.domain.*` helper → return
  JSON. A handler growing past ~25 lines of logic gets extracted. Domain
  modules never import FastAPI — they raise `errors.ClippyMeError` subclasses
  (`ValidationError` 400, `NotFoundError` 404, `ConflictError` 409) mapped by
  one app-level handler.
- **Per-clip endpoints resolve through `clip_resolve.resolve_clip()`** — do
  not re-implement the metadata/filename fallback chain.
- **Pure logic is extracted to host-testable modules** (no cv2/torch imports)
  so it runs in `pytest -m "not integration"`. cv2/ML code is verified only by
  the Docker integration suite.
- **Back-compat re-exports**: `reframe.py` re-exports the moved
  track/detect names; `main.py` re-exports the reframe API. Keep them when
  moving code.
- **Atomic writes** for anything on disk that a crash could corrupt
  (`job_artifacts.save_job_metadata` pattern: tmp + `os.replace`, 0o600).
- **Frontend**: `RedesignApp.jsx` owns only top-level state wiring; side
  effects go in `hooks/`, pure logic in `lib/`, visuals in `redesign/`
  components. UI primitives are hand-rolled in `primitives.jsx` (no shadcn
  CLI). Component tests colocate as `*.test.jsx` (Vitest + jsdom).
- **Security**: `job_id` regex-validated everywhere; config/state endpoints
  require a trusted origin or private-network client; `SafeStaticFiles`
  blocks `*_metadata.json` and `source_*` from the `/videos` mount; secrets
  never enter the job journal; `tmp/` is gitignored and must never be
  committed. Pre-commit secret scan: `git config core.hooksPath .githooks`.
  With `TRUST_PROXY=1`, `client_ip` reads the **last** `X-Forwarded-For`
  hop (the shipped nginx APPENDS via `$proxy_add_x_forwarded_for` — the
  first hop is client-forgeable); keep append+last-hop in sync.

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/process` · `/api/batch` | Submit single video / up to 20 URLs (optional `zernio_profile`, default `"default"` — tags the job's campaign for a per-campaign Discord approval bot) |
| GET | `/api/status/{job_id}` | Poll job progress |
| POST | `/api/pause|resume|stop|cancel/{job_id}` | Job control (stop keeps clips, cancel discards) |
| POST | `/api/compose/{job_id}/{clip_index}` | Compose toggled layers |
| POST | `/api/smartcut/{job_id}/{clip_index}` | Smart Cut (optional `drop_ranges` body) |
| GET | `/api/transcript/{job_id}/{clip_index}` | Clip-relative transcript for manual trim |
| POST | `/api/edit-ai/{job_id}/{clip_index}` | NL instruction → Gemini → `drop_ranges` |
| POST | `/api/reframe/{job_id}/{clip_index}` | Switch reframe mode post-hoc |
| POST | `/api/publish/{job_id}/{clip_index}` | Upload + schedule via Zernio |
| GET/POST/DELETE | `/api/config*` | Keys, cookies, logo, fonts, Zernio (trusted clients) |
| GET/POST | `/api/config/zernio/profiles` | List / create named Zernio profiles (`?profile=` on the routes above selects one, default `"default"`) |
| PATCH/DELETE | `/api/config/zernio/profiles/{profile_id}` | Rename a profile's label / delete a non-default profile |
| GET/POST | `/api/config/caption-presets` | List / upsert (by id) saved caption templates (e.g. one per seller in a multi-account campaign) |
| DELETE | `/api/config/caption-presets/{preset_id}` | Delete a saved caption preset |
| GET/POST | `/api/config/player-images` | List / upload (named by player) an athlete photo for the player-image compose overlay |
| DELETE | `/api/config/player-images/{name}` | Delete a player image |
| GET | `/api/history` · POST `/api/history/{id}/restore` · DELETE `/api/history/{id}` | Past jobs |

## Configuration

API keys, Gemini model, transcription provider and cookies are managed from
the dashboard Settings tab (persisted in `data/config.json`, git-ignored).
The full operational env-var reference (REFRAME_*, AE_*, CLIPPYME_*,
DEEPGRAM_*, ELEVENLABS_*, ZERNIO_*, server knobs) lives in `.env.example`
(commented, with defaults) and the README table — keep those two in sync when
adding a knob. `GEMINI_MODEL` defaults to `gemini-3.5-flash`; per-job override
via `--model` / `ProcessRequest.model` (regex-validated against argv
injection).

## Docs pointers

- `docs/*-analysis.md` — 14 comparative analyses of the OSS projects ideas
  were ported from (reframe smoothers, ClipsAI TextTiling, flycut manual trim,
  VideoLingo subtitle splitting, …) with adopt/reject rationale.
- `docs/fable5-improvement-log.md` — audit-driven fix log with verification
  evidence per change.
- `docs/reframe-improvements-research.md` — the comfort-mode research and
  measured A/B numbers.
- `docs/architecture-history.md` — summary of major refactors (what moved
  where and why); the pre-rewrite CLAUDE.md is in git history.
