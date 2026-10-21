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


def marker(size=(600, 400)):
    img = Image.new("RGB", size, "white")
    img.paste((255, 0, 0), (0, 0, 60, 40))          # red block marks top-left
    return img


def test_orientation_rotation():
    assert reframe.orientation_rotation({"orientation": "landscape", "portrait_rotation": 270}) == 0
    assert reframe.orientation_rotation({"orientation": "portrait", "portrait_rotation": 270}) == 270


def test_saved_round_trip():
    img = marker()
    assert reframe.to_saved(img, 0).size == (600, 400)
    for r in (90, 270):
        saved = reframe.to_saved(img, r)
        assert saved.size == (400, 600)
        assert reframe.to_device(saved, r).tobytes() == img.tobytes()
    assert reframe.to_saved(img, 90).tobytes() != reframe.to_saved(img, 270).tobytes()


def test_enhance_contrast_softens():
    img = Image.new("RGB", (2, 1)); img.putpixel((1, 0), (255, 255, 255))
    out = reframe.ImageProcessor.enhance(img, brightness_factor=1.0, contrast=0.5, color_factor=1.0)
    assert out.getpixel((0, 0))[0] > 0 and out.getpixel((1, 0))[0] < 255


def test_dither_settings_fills_defaults():
    k = reframe.dither_settings({"saturation": 0.4})
    assert k["saturation"] == 0.4 and k["contrast"] == 0.85
    assert set(k) == {"saturation", "brightness_factor", "color_factor", "contrast",
                      "dithering_method", "bayer_size", "threshold_scale"}


def test_resize_crops_instead_of_squashing():
    img = Image.new("RGB", (800, 1200), "white"); img.paste((0, 0, 0), (200, 400, 600, 800))
    out = reframe.ImageProcessor.resize_image(img)
    assert out.size == (600, 400)
    assert out.getpixel((300, 60)) == (0, 0, 0)       # a squashed square would end at y≈133


def test_text_screens_are_device_sized_and_upright():
    assert reframe.EMPTY_ROLL_TEXT == "Take your first picture and wait for it to appear on the screen."
    for r in (0, 90, 270):
        screen = reframe.render_text_screen(reframe.EMPTY_ROLL_TEXT, r)
        assert screen.size == (600, 400)
        upright = reframe.to_saved(screen, r).convert("L").point(lambda v: 255 - v)
        top, bottom = upright.getbbox()[1], upright.getbbox()[3]
        assert top < upright.height - bottom              # text sits nearer the top when upright
    qr = reframe.render_dashboard_qr_image({"primary_url": "http://x.local", "fallback_url": "http://10.0.0.2"}, 90)
    assert qr.size == (600, 400)
