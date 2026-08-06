"""The Discord bot must compose with the JOB's recipe, not its own env defaults.

bot.py is a standalone script (not part of the clippyme package), so it is
imported by path here. It reads its config from the environment at import
time and raises SystemExit without a token/channel, so both are stubbed.

The bug this pins: _build_compose_toggles used to hardcode logo/grade/banner
OFF and build hook_params from the bot's own env (no `style` key at all), so a
clip configured in Create with a logo and a hook background reached Discord —
and then the platform — with neither.
"""
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
    spec = importlib.util.spec_from_file_location("clippyme_discordbot_under_test", _BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_RECIPE = {
    "toggles": {"hook": True, "subtitles": True, "logo": True, "grade": True, "banner": True},
    "hook_params": {"position": 0.42, "size": "S", "bg_enabled": True, "bg_color": "#FF0000"},
    "subtitle_params": {"position": 0.8, "preset": "hormozi_bold"},
    "logo_params": {"position": {"x": 0.9, "y": 0.05}, "scale": 0.2},
    "grade_params": {"preset": "warm"},
    "banner_params": {"enabled": True, "platform": "kick", "handle": "someone"},
    "player_image_params": {"position": "center", "size": "M"},
}


def test_recipe_toggles_win_over_the_bot_env_defaults(bot):
    toggles, _hook, _subs = bot._build_compose_toggles("Wait for it", _RECIPE)
    # logo/grade/banner were unconditionally False before — the whole point.
    assert toggles["logo"] is True
    assert toggles["grade"] is True
    assert toggles["banner"] is True
    assert toggles["subtitles"] is True


def test_recipe_hook_style_survives_and_gets_the_clip_text(bot):
    _toggles, hook, _subs = bot._build_compose_toggles("Wait for it", _RECIPE)
    assert hook["bg_enabled"] is True          # the "no background" complaint
    assert hook["bg_color"] == "#FF0000"
    assert hook["position"] == 0.42            # the drawn guide, not HOOK_POSITION
    assert hook["text"] == "Wait for it"       # text is per clip, filled in here


def test_recipe_subtitle_position_survives(bot):
    _toggles, _hook, subs = bot._build_compose_toggles("x", _RECIPE)
    assert subs["position"] == 0.8
    assert subs["preset"] == "hormozi_bold"


def test_hook_toggle_is_forced_off_on_empty_text(bot):
    # The backend skips an empty hook anyway; keeping the toggle honest makes
    # the callers' `any(toggles.values())` checks agree with reality.
    toggles, hook, _subs = bot._build_compose_toggles("", _RECIPE)
    assert toggles["hook"] is False
    assert hook == {}


def test_no_recipe_falls_back_to_the_env_defaults(bot):
    # An old job (or a caller that sent none) must behave exactly as before.
    toggles, hook, _subs = bot._build_compose_toggles("Wait for it", None)
    assert toggles["logo"] is False
    assert toggles["grade"] is False
    assert toggles["banner"] is False
    assert hook["position"] == bot.HOOK_POSITION


def test_publish_body_forwards_every_layer_param_from_the_recipe(bot, monkeypatch):
    monkeypatch.setattr(bot, "_zernio_accounts", {"tiktok": "acc1"})
    body = bot._build_publish_body("Title", "Wait for it", _RECIPE)
    assert body["compose_first"] is True
    assert body["toggles"]["logo"] is True
    assert body["logo_params"] == _RECIPE["logo_params"]
    assert body["grade_params"] == _RECIPE["grade_params"]
    assert body["banner_params"] == _RECIPE["banner_params"]
    assert body["player_image_params"] == _RECIPE["player_image_params"]
    assert body["hook_params"]["bg_enabled"] is True


def test_publish_body_without_a_recipe_sends_empty_layer_params(bot, monkeypatch):
    monkeypatch.setattr(bot, "_zernio_accounts", {"tiktok": "acc1"})
    body = bot._build_publish_body("Title", "Wait for it", None)
    assert body["logo_params"] == {}
    assert body["grade_params"] == {}
    assert body["banner_params"] == {}
