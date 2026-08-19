"""poll_new_clips must not let one clip starve every clip behind it.

The loop used to `await _post_clip(...)` one clip at a time, and _post_clip
blocks on a full multi-pass compose. A slow or failing clip therefore held up
every later clip — head-of-line blocking, visible to the user as "only the
first couple of clips ever showed up". Raising DISCORD_COMPOSE_TIMEOUT made
that strictly worse on its own, which is why the bounded-concurrency change
belongs with it.
"""
import asyncio
import importlib.util
import os
import pathlib
import sys

import pytest

_BOT_PATH = pathlib.Path(__file__).resolve().parents[2] / "discordbot" / "bot.py"


@pytest.fixture(scope="module")
def bot():
    os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
    os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
    spec = importlib.util.spec_from_file_location("clippyme_bot_poll_under_test", _BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fake_history(module, jobs):
    """Stub the aiohttp GET /api/history the poll loop performs."""
    class _Resp:
        status = 200

        async def json(self):
            return {"jobs": jobs}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Session:
        def get(self, *a, **k):
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    module.aiohttp.ClientSession = lambda *a, **k: _Session()


def _job(n_clips):
    return [{
        "jobId": "job-1",
        "zernioProfile": "default",
        "composeRecipe": None,
        "clips": [{"title": f"c{i}", "hook_text": "h", "start": 0, "end": 5}
                  for i in range(n_clips)],
    }]


def _run_poll(module, monkeypatch, jobs, post_impl):
    monkeypatch.setattr(module.bot, "get_channel", lambda _cid: object())
    monkeypatch.setattr(module, "_posted", set())
    monkeypatch.setattr(module, "_save_posted", lambda *_a: None)
    monkeypatch.setattr(module, "_post_clip", post_impl)
    _fake_history(module, jobs)
    asyncio.run(module.poll_new_clips())


def test_one_failing_clip_does_not_strand_the_clips_behind_it(bot, monkeypatch):
    seen = []

    async def _post(channel, job_id, idx, clip, total, recipe=None):
        seen.append(idx)
        if idx == 0:
            raise RuntimeError("compose blew up")

    _run_poll(bot, monkeypatch, _job(5), _post)
    # Every clip is still attempted — previously the raise propagated out of
    # the loop and clips 1..4 were never reached at all.
    assert sorted(seen) == [0, 1, 2, 3, 4]


def test_posting_is_bounded_by_DISCORD_POST_CONCURRENCY(bot, monkeypatch):
    monkeypatch.setattr(bot, "DISCORD_POST_CONCURRENCY", 2)
    live = 0
    peak = 0

    async def _post(channel, job_id, idx, clip, total, recipe=None):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0)  # yield so overlap is observable
        live -= 1

    _run_poll(bot, monkeypatch, _job(6), _post)
    assert peak <= 2, f"expected at most 2 concurrent composes, saw {peak}"
    assert peak > 1, "serial execution would defeat the whole point"
