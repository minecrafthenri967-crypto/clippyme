"""Host tests for clippyme.domain.player_image's pure library/matching half.

The render half (player_image_filter_chain / add_player_image_to_video) is
added in a later step alongside its own tests.
"""
import os

import pytest

from clippyme.domain import player_image as pi


@pytest.fixture
def tmp_library(tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "PLAYER_IMAGES_DIR", str(tmp_path))
    return tmp_path


def _touch(dir_path, name):
    open(os.path.join(dir_path, f"{name}.png"), "wb").close()


def test_list_player_images_empty_dir(tmp_library):
    assert pi.list_player_images() == []


def test_list_player_images_missing_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "PLAYER_IMAGES_DIR", str(tmp_path / "does_not_exist"))
    assert pi.list_player_images() == []


def test_list_player_images_returns_sorted_basenames(tmp_library):
    _touch(tmp_library, "LeBron James")
    _touch(tmp_library, "Aaron Judge")
    assert pi.list_player_images() == ["Aaron Judge", "LeBron James"]


def test_list_player_images_ignores_non_png(tmp_library):
    _touch(tmp_library, "LeBron James")
    open(os.path.join(tmp_library, "notes.txt"), "w").close()
    assert pi.list_player_images() == ["LeBron James"]


# --- _normalize_name ---------------------------------------------------------

def test_normalize_name_lowercases_and_collapses_whitespace():
    assert pi._normalize_name("  LeBron   James  ") == "lebron james"


def test_normalize_name_strips_punctuation():
    assert pi._normalize_name("De'Aaron Fox Jr.") == "de aaron fox jr"


def test_normalize_name_strips_accents():
    assert pi._normalize_name("Ronald Acuña") == "ronald acuna"


def test_normalize_name_empty_string():
    assert pi._normalize_name("") == ""
    assert pi._normalize_name(None) == ""


# --- match_player_image ------------------------------------------------------

def test_match_player_image_exact():
    assert pi.match_player_image("LeBron James", ["LeBron James", "Stephen Curry"]) == "LeBron James"


def test_match_player_image_case_and_punctuation_insensitive():
    assert pi.match_player_image("lebron   james!!", ["LeBron James"]) == "LeBron James"


def test_match_player_image_accent_insensitive():
    assert pi.match_player_image("ronald acuna", ["Ronald Acuña"]) == "Ronald Acuña"


def test_match_player_image_no_match_returns_none():
    assert pi.match_player_image("Nobody Special", ["LeBron James"]) is None


def test_match_player_image_empty_library_returns_none():
    assert pi.match_player_image("LeBron James", []) is None


def test_match_player_image_empty_name_returns_none():
    assert pi.match_player_image("", ["LeBron James"]) is None
    assert pi.match_player_image(None, ["LeBron James"]) is None


def test_match_player_image_defaults_to_list_player_images(tmp_library):
    _touch(tmp_library, "LeBron James")
    assert pi.match_player_image("lebron james") == "LeBron James"


# --- _PLAYER_NAME_RE (path-safety contract used by config_routes.py) --------

@pytest.mark.parametrize("name", [
    "LeBron James", "De'Aaron Fox", "Jr. Smith", "Ronald Acuña", "A",
])
def test_player_name_re_accepts_real_names(name):
    assert pi._PLAYER_NAME_RE.match(name)


@pytest.mark.parametrize("name", [
    "", "../../etc/passwd", "..", "/etc/passwd", " leading space",
    ".hidden", "a" * 81,
])
def test_player_name_re_rejects_unsafe_or_invalid_names(name):
    assert not pi._PLAYER_NAME_RE.match(name)


# --- player_image_filter_chain (pure geometry, no ffmpeg) -------------------

def test_filter_chain_clamps_scale_below_range():
    chain, _, _ = pi.player_image_filter_chain(1000, scale=0.01)
    assert f"scale={int(1000 * pi._SCALE_MIN)}:-1" in chain


def test_filter_chain_clamps_scale_above_range():
    chain, _, _ = pi.player_image_filter_chain(1000, scale=5.0)
    assert f"scale={int(1000 * pi._SCALE_MAX)}:-1" in chain


def test_filter_chain_clamps_opacity():
    chain, _, _ = pi.player_image_filter_chain(1000, opacity=5.0)
    assert "aa=1.000" in chain
    chain, _, _ = pi.player_image_filter_chain(1000, opacity=-1.0)
    assert "aa=0.000" in chain


def test_filter_chain_position_delegates_to_logo_overlay_xy():
    from clippyme.domain.logo import logo_overlay_xy

    _, x_expr, y_expr = pi.player_image_filter_chain(1000, margin=0.04, position="bottom-left")
    expected_x, expected_y = logo_overlay_xy("bottom-left", int(1000 * 0.04))
    assert (x_expr, y_expr) == (expected_x, expected_y)


def test_filter_chain_default_position_is_center():
    from clippyme.domain.logo import logo_overlay_xy

    _, x_expr, y_expr = pi.player_image_filter_chain(1000)
    expected_x, expected_y = logo_overlay_xy("center", int(1000 * 0.04))
    assert (x_expr, y_expr) == (expected_x, expected_y)


# --- add_player_image_to_video: file-existence guards (no ffmpeg needed) ---

def test_add_player_image_missing_video_raises(tmp_path):
    img = tmp_path / "img.png"
    img.write_bytes(b"x")
    with pytest.raises(FileNotFoundError):
        pi.add_player_image_to_video(str(tmp_path / "nope.mp4"), str(img),
                                      str(tmp_path / "out.mp4"), start=1.0)


def test_add_player_image_missing_image_raises(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    with pytest.raises(FileNotFoundError):
        pi.add_player_image_to_video(str(video), str(tmp_path / "nope.png"),
                                      str(tmp_path / "out.mp4"), start=1.0)
