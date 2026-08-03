"""Regression tests for request-schema/domain contract mismatches."""
import pytest
from pydantic import ValidationError

from clippyme.api.schemas import (
    BatchRequest, LiveMonitorPublishingRequest, LiveMonitorStartRequest,
    LiveMonitorStopRequest, ProcessRequest, ReframeRequest,
)


_MONITOR_BASE = {
    "slug": "somechannel",
    "platforms": [{"platform": "tiktok", "accountId": "account-1"}],
}


def test_process_language_is_normalized_and_allowlisted():
    request = ProcessRequest(url="https://upload.invalid/local", language=" it ")
    assert request.language == "it"

    with pytest.raises(ValidationError):
        ProcessRequest(url="https://upload.invalid/local", language="../../bad")


def test_batch_rejects_all_blank_urls_after_cleaning():
    with pytest.raises(ValidationError):
        BatchRequest(urls=["", "   "])


def test_batch_language_is_rejected_at_api_boundary():
    with pytest.raises(ValidationError):
        BatchRequest(urls=["https://example.com/video"], language="not-a-language")


def test_process_zernio_profile_defaults_and_is_bounded():
    assert ProcessRequest(url="https://upload.invalid/local").zernio_profile == "default"
    assert ProcessRequest(
        url="https://upload.invalid/local", zernio_profile="dja",
    ).zernio_profile == "dja"
    with pytest.raises(ValidationError):
        ProcessRequest(url="https://upload.invalid/local", zernio_profile="Not Valid!")


def test_process_accepts_gaming_reframe_mode():
    request = ProcessRequest(url="https://upload.invalid/local", reframe_mode="gaming")
    assert request.reframe_mode == "gaming"


def test_batch_accepts_gaming_reframe_mode():
    request = BatchRequest(urls=["https://example.com/video"], reframe_mode="gaming")
    assert request.reframe_mode == "gaming"


def test_reframe_request_accepts_gaming_mode():
    assert ReframeRequest(reframe_mode="gaming").reframe_mode == "gaming"


_BOX = {"x": 0.6, "y": 0.05, "w": 0.35, "h": 0.3}


def test_process_gaming_facecam_box_defaults_to_none_and_accepts_a_valid_box():
    request = ProcessRequest(url="https://upload.invalid/local")
    assert request.gaming_facecam_box is None
    request = ProcessRequest(
        url="https://upload.invalid/local", reframe_mode="gaming", gaming_facecam_box=_BOX,
    )
    assert request.gaming_facecam_box.x == 0.6
    assert request.gaming_facecam_box.w == 0.35
    with pytest.raises(ValidationError):
        ProcessRequest(url="https://upload.invalid/local", gaming_facecam_box={"x": 1.5, "y": 0, "w": 0.1, "h": 0.1})
    with pytest.raises(ValidationError):
        # x + w > 1
        ProcessRequest(url="https://upload.invalid/local", gaming_facecam_box={"x": 0.9, "y": 0, "w": 0.5, "h": 0.1})


def test_batch_gaming_facecam_box_defaults_to_none_and_accepts_a_valid_box():
    request = BatchRequest(urls=["https://example.com/video"])
    assert request.gaming_facecam_box is None
    request = BatchRequest(
        urls=["https://example.com/video"], reframe_mode="gaming", gaming_facecam_box=_BOX,
    )
    assert request.gaming_facecam_box.x == 0.6
    assert request.gaming_facecam_box.h == 0.3


def test_reframe_request_gaming_facecam_box_defaults_to_none_and_accepts_a_valid_box():
    request = ReframeRequest(reframe_mode="gaming")
    assert request.gaming_facecam_box is None
    request = ReframeRequest(reframe_mode="gaming", gaming_facecam_box=_BOX)
    assert request.gaming_facecam_box.y == 0.05


def test_batch_zernio_profile_defaults_and_is_bounded():
    assert BatchRequest(urls=["https://example.com/video"]).zernio_profile == "default"
    assert BatchRequest(
        urls=["https://example.com/video"], zernio_profile="ebay_live",
    ).zernio_profile == "ebay_live"
    with pytest.raises(ValidationError):
        BatchRequest(urls=["https://example.com/video"], zernio_profile="Not Valid!")


def test_live_monitor_start_preserves_runtime_domain_fields():
    request = LiveMonitorStartRequest(
        **_MONITOR_BASE,
        delete_after_publish=False,
        max_clips=9,
        timezone="Europe/Rome",
    )
    payload = request.model_dump()
    assert payload["delete_after_publish"] is False
    assert payload["max_clips"] == 9
    assert payload["timezone"] == "Europe/Rome"


def test_live_monitor_start_label_defaults_blank_and_is_bounded():
    assert LiveMonitorStartRequest(**_MONITOR_BASE).label == ""
    request = LiveMonitorStartRequest(**_MONITOR_BASE, label="eBay Live")
    assert request.model_dump()["label"] == "eBay Live"
    with pytest.raises(ValidationError):
        LiveMonitorStartRequest(**_MONITOR_BASE, label="x" * 81)


@pytest.mark.parametrize("max_clips", [0, 51])
def test_live_monitor_start_bounds_max_clips(max_clips):
    with pytest.raises(ValidationError):
        LiveMonitorStartRequest(**_MONITOR_BASE, max_clips=max_clips)


def test_live_monitor_start_rejects_unknown_timezone():
    with pytest.raises(ValidationError):
        LiveMonitorStartRequest(**_MONITOR_BASE, timezone="Mars/Olympus_Mons")


def test_live_monitor_publishing_requires_a_strict_boolean():
    assert LiveMonitorPublishingRequest(enabled=False).enabled is False
    with pytest.raises(ValidationError):
        LiveMonitorPublishingRequest(enabled="false")



def test_live_monitor_stop_id_is_path_safe():
    assert LiveMonitorStopRequest(monitor_id="youtube:0123456789abcdefabcd").monitor_id
    with pytest.raises(ValidationError):
        LiveMonitorStopRequest(monitor_id="youtube:https://example.com/channel")
