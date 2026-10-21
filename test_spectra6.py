import os, sys, types, threading, time
sys.modules.setdefault("picamera2", types.SimpleNamespace(Picamera2=None))
import pytest
from PIL import Image
import reframe, settings_defaults
from settings_defaults import merge_defaults


def test_spectra6_defaults():
    d = merge_defaults({})
    assert d["processing"]["contrast"] == 0.85
    assert d["processing"]["saturation"] == 0.75 and d["processing"]["color_factor"] == 1.6
    assert d["display"]["orientation"] == "landscape" and d["display"]["portrait_rotation"] == 90
    assert d["display"]["slideshow_interval_seconds"] == 0
    assert d["camera"]["rotate_180"] is False and d["camera"]["autofocus_mode"] == 2
    assert d["system"]["auto_timeout_enabled"] is False
    assert d["system"]["show_dashboard_qr_on_wifi_connect"] is False


def test_merge_keeps_saved_and_fills_missing():
    d = merge_defaults({"processing": {"saturation": 0.4}})
    assert d["processing"]["saturation"] == 0.4 and d["processing"]["contrast"] == 0.85


def test_merge_never_aliases_defaults():
    merge_defaults({})["extensions"]["arena"]["access_token"] = "leak"
    assert merge_defaults({})["extensions"]["arena"]["access_token"] == ""


def test_camera_manager_settings_are_merged(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"display": {"orientation": "portrait"}}')
    cm = object.__new__(reframe.CameraManager); cm.settings_path = str(path)
    s = cm.load_settings()
    assert s["display"]["orientation"] == "portrait" and s["processing"]["contrast"] == 0.85
    cm.settings_path = str(tmp_path / "missing.json")
    assert cm.load_settings() == merge_defaults({})


@pytest.fixture(scope="module")
def dashboard():
    import dashboard as d
    return d


def test_defaults_validate(dashboard):
    dashboard.validate_settings(merge_defaults({}))


@pytest.mark.parametrize("section,key,value", [
    ("display", "orientation", "sideways"),
    ("display", "portrait_rotation", 180),
    ("display", "slideshow_interval_seconds", -1),
    ("processing", "contrast", 0.1),
    ("camera", "rotate_180", "yes"),
])
def test_validation_rejects(dashboard, section, key, value):
    s = merge_defaults({}); s[section][key] = value
    with pytest.raises(dashboard.SettingsValidationError):
        dashboard.validate_settings(s)


def test_defaults_endpoint(dashboard):
    from fastapi.testclient import TestClient
    assert TestClient(dashboard.app).get("/api/settings/defaults").json() == merge_defaults({})
