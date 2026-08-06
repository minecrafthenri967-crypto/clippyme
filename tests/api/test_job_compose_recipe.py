"""The per-job compose recipe: POST /api/process -> sidecar -> GET /api/history.

The pipeline renders clips RAW; every layer is compose-time. Before this
existed, each consumer invented its own recipe from its own defaults — most
visibly the Discord approval bot, which composed from its own env vars, so a
hook configured WITH a background at a position drawn in the layout editor
reached Discord (and then TikTok) with no background at the bot's own
position. Persisting the submitting recipe with the job is what makes "the
settings are actually applied" true for every consumer, not just the tab that
set them.
"""
import pytest
from pydantic import ValidationError

from clippyme.api.schemas import BatchRequest, ProcessRequest, validate_compose_recipe
from clippyme.domain.job_artifacts import load_job_compose_recipe, save_job_campaign

_RECIPE = {
    "toggles": {"hook": True, "subtitles": True, "grade": False},
    "hook_params": {"position": 0.42, "size": "S", "bg_enabled": True, "bg_color": "#FF0000"},
    "subtitle_params": {"position": 0.8, "preset": "hormozi_bold", "align": "center"},
    "logo_params": {"position": "top-right", "size": "M"},
    "grade_params": {"preset": "none"},
    "banner_params": {"enabled": False},
    "player_image_params": {"position": "center", "size": "M"},
}


def test_validate_compose_recipe_accepts_the_full_shape():
    assert validate_compose_recipe(_RECIPE) == _RECIPE


def test_validate_compose_recipe_allows_none_and_partial():
    assert validate_compose_recipe(None) is None
    assert validate_compose_recipe({"toggles": {"hook": True}}) == {"toggles": {"hook": True}}


@pytest.mark.parametrize("bad", [
    {"unknown_layer_params": {}},          # key outside the allow-list
    {"toggles": {"not_a_layer": True}},    # toggle outside _ALLOWED_TOGGLES
    {"toggles": {"hook": "yes"}},          # non-boolean toggle
    {"hook_params": "not-an-object"},      # param block must be an object
    {"logo_params": {"position": {"x": {"deep": 1}}}},  # nesting stops at one level
    {"hook_params": {"text": "x" * 5000}},              # scalar bounds still apply
])
def test_validate_compose_recipe_rejects_malformed_input(bad):
    with pytest.raises(ValueError):
        validate_compose_recipe(bad)


def test_validate_compose_recipe_accepts_the_free_drag_logo_position():
    # logoPositionEditor.jsx stores the dragged placement as {"x": .., "y": ..}
    # (see logo.parse_logo_position_xy). A scalars-only rule 400s every submit
    # that has the logo enabled at a dragged position — i.e. the logo feature
    # would be unusable rather than merely unpersisted.
    recipe = {
        "toggles": {"logo": True},
        "logo_params": {"position": {"x": 0.9, "y": 0.05}, "scale": 0.2},
    }
    assert validate_compose_recipe(recipe) == recipe


def test_process_request_accepts_a_dragged_logo_position():
    req = ProcessRequest.model_validate({
        "url": "https://www.youtube.com/watch?v=abc12345678",
        "compose": {"toggles": {"logo": True},
                    "logo_params": {"position": {"x": 0.9, "y": 0.05}}},
    })
    assert req.compose["logo_params"]["position"] == {"x": 0.9, "y": 0.05}


def test_process_request_carries_and_validates_the_recipe():
    req = ProcessRequest.model_validate(
        {"url": "https://www.youtube.com/watch?v=abc12345678", "compose": _RECIPE}
    )
    assert req.compose == _RECIPE


def test_process_request_defaults_the_recipe_to_none():
    req = ProcessRequest.model_validate({"url": "https://www.youtube.com/watch?v=abc12345678"})
    assert req.compose is None


def test_process_request_rejects_a_malformed_recipe():
    with pytest.raises(ValidationError):
        ProcessRequest.model_validate(
            {"url": "https://www.youtube.com/watch?v=abc12345678",
             "compose": {"toggles": {"nope": True}}}
        )


def test_batch_request_carries_the_recipe_too():
    req = BatchRequest.model_validate(
        {"urls": ["https://www.youtube.com/watch?v=abc12345678"], "compose": _RECIPE}
    )
    assert req.compose == _RECIPE


def test_recipe_survives_the_sidecar_round_trip(tmp_path):
    # What /api/process persists is exactly what /api/history hands back, so a
    # fraction position drawn in the layout editor reaches the bot unchanged.
    save_job_campaign(str(tmp_path), "ebay_live", compose=_RECIPE)
    loaded = load_job_compose_recipe(str(tmp_path))
    assert loaded["hook_params"]["position"] == 0.42
    assert loaded["hook_params"]["bg_enabled"] is True
    assert loaded["subtitle_params"]["position"] == 0.8
