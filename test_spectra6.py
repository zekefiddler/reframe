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


def test_next_photo_id_steps_older_and_wraps():
    ids = ["00001", "00002", "00003"]
    assert reframe.next_photo_id(ids, None) == "00002"
    assert reframe.next_photo_id(ids, "empty") == "00002"
    assert reframe.next_photo_id(ids, "00003") == "00002"
    assert reframe.next_photo_id(ids, "00002") == "00001"
    assert reframe.next_photo_id(ids, "00001") == "00003"
    assert reframe.next_photo_id(["00001", "00003"], "00002") == "00001"   # current was deleted
    assert reframe.next_photo_id(["00001"], "00001") == "00001"
    assert reframe.next_photo_id([], None) is None


def test_rotation_sidecar(tmp_path):
    fm = reframe.FileManager(str(tmp_path / "p"), str(tmp_path / "d"))
    Image.new("RGB", (8, 8)).save(tmp_path / "p" / "00000.jpg")
    assert fm.get_rotation("00000") == 0
    fm.set_rotation("00000", 270)
    assert fm.get_rotation("00000") == 270
    assert fm.photo_ids() == ["00000"] and len(fm.list_all_photos()) == 1
    fm.delete_photo("00000")
    assert not (tmp_path / "p" / "00000.json").exists()


class FakeEPD:
    def __init__(self): self.frames = []
    def display(self, buf, fast_refresh=False): self.frames.append(bytes(buf))
    def Clear(self, color=0x11, fast_refresh=False): self.frames.append("clear")
    def sleep(self): pass


class FakeCamera:
    def __init__(self): self.settings = merge_defaults({})
    def capture_image_with_metadata(self, path, fast_mode=False):
        img = Image.new("RGB", (1200, 800), (40, 90, 160)); img.paste((255, 0, 0), (0, 0, 200, 120))
        pid = os.path.splitext(os.path.basename(path))[0]
        return {"success": True, "photo_id": pid, "original_path": path, "processed_path": None}, img
    def update_activity_time(self): pass
    def is_timeout_enabled(self): return False


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setattr(reframe, "SAVE_PATH", str(tmp_path / "photos"))
    monkeypatch.setattr(reframe, "PROCESSED_PATH", str(tmp_path / "dithered"))
    monkeypatch.setattr(reframe, "PANEL_STATE_PATH", str(tmp_path / "panel_showing"))
    return make_system(tmp_path)


def make_system(tmp_path):
    display = reframe.EInkDisplay(str(tmp_path / "settings.json"))
    display.epd, display._initialized = FakeEPD(), True
    return reframe.CameraSystem(eink_display=display, camera_manager=FakeCamera())


def settle(s):
    for t in (getattr(s, "_save_thread", None), s.eink_display._display_thread):
        if t: t.join(10)


def frames(s): return s.eink_display.epd.frames


def snap(s, orientation="landscape"):
    s.camera_manager.settings["display"]["orientation"] = orientation
    pid = s.capture_photo_api()["photo_id"]; settle(s); return pid


def test_portrait_rotates_files_not_panel(system):
    a = snap(system); b = snap(system, "portrait")
    assert frames(system)[0] == frames(system)[1]
    assert Image.open(os.path.join(reframe.SAVE_PATH, f"{b}.jpg")).size == (800, 1200)
    assert Image.open(os.path.join(reframe.PROCESSED_PATH, f"{b}_dithered.png")).size == (400, 600)
    assert system.file_manager.get_rotation(a) == 0 and system.file_manager.get_rotation(b) == 90
    assert system.panel_showing == b


def test_mixed_orientations_show_like_capture(system):
    a = snap(system); b = snap(system, "portrait")
    system.camera_manager.settings["display"]["orientation"] = "landscape"
    for pid, captured in ((a, frames(system)[0]), (b, frames(system)[1])):
        assert system.show_photo(pid)["success"]; settle(system)
        assert frames(system)[-1] == captured


def test_show_photo_redithers_with_saved_settings(system):
    pid = snap(system)
    os.remove(os.path.join(reframe.PROCESSED_PATH, f"{pid}_dithered.png"))
    saved = {"saturation": 0.3, "contrast": 1.0}
    system.camera_manager.settings["processing"].update(saved)
    system.show_photo(pid); settle(system)
    original = reframe.ImageProcessor.resize_image(Image.open(os.path.join(reframe.SAVE_PATH, f"{pid}.jpg")))
    expected, _ = reframe.ImageProcessor.dither_to_display_buffer(original, **reframe.dither_settings(saved))
    default, _ = reframe.ImageProcessor.dither_to_display_buffer(original, **reframe.dither_settings({}))
    assert frames(system)[-1] == bytes(expected) != bytes(default)


def test_show_photo_ignored_while_busy(system):
    pid = snap(system); before = list(frames(system))
    system.eink_display._display_busy = True
    assert system.show_photo(pid) == {"success": False, "error": "display_busy"}
    assert frames(system) == before


def test_clear_is_async_and_sets_busy(system):
    gate = threading.Event()
    system.eink_display.epd.Clear = lambda **kw: gate.wait(5)
    assert system.eink_display.clear_display()["success"]
    assert system.eink_display.is_busy()
    gate.set(); settle(system)
    assert not system.eink_display.is_busy()


def test_clear_route_not_shadowed_by_photo_route():
    paths = [r.path for r in reframe._create_fastapi_routes().routes]
    assert paths.index("/api/display/clear") < paths.index("/api/display/{photo_id}")


def test_empty_roll_drawn_once_across_restarts(system, tmp_path):
    assert system.panel_tick(time.monotonic()); settle(system)
    assert system.panel_showing == "empty" and len(frames(system)) == 1
    restarted = make_system(tmp_path)
    assert not restarted.panel_tick(time.monotonic())
    assert frames(restarted) == []


def test_empty_roll_not_drawn_over_qr_or_clear(system):
    for screen in ("qr", "clear"):
        system._set_panel_showing(screen)
        assert not system.panel_tick(time.monotonic())
    assert frames(system) == []


def test_next_steps_back_wraps_and_shutter_resets(system):
    p1, p2, p3 = snap(system), snap(system), snap(system)
    shown = []
    for _ in range(3):
        system.next_photo(); settle(system); shown.append(system.panel_showing)
    assert shown == [p2, p1, p3]
    p4 = snap(system)
    system.next_photo(); settle(system)
    assert system.panel_showing == p3 and p4 > p3


def test_next_ignored_while_busy(system):
    snap(system); p2 = snap(system)
    system.eink_display._display_busy = True
    assert system.next_photo()["error"] == "display_busy" and system.panel_showing == p2


def test_slideshow_waits_full_interval_after_shutter(system):
    snap(system); snap(system)
    assert not system.panel_tick(system.last_panel_change + 3600)        # interval 0 = off
    system.camera_manager.settings["display"]["slideshow_interval_seconds"] = 60
    assert not system.panel_tick(system.last_panel_change + 59)
    assert system.panel_tick(system.last_panel_change + 60)


def test_delete_all_ends_on_empty_roll(system):
    ids = [snap(system), snap(system)]
    system.eink_display._display_busy = True                             # mid-refresh during the deletes
    for pid in ids: system.delete_photo_api(pid)
    system.eink_display._display_busy = False
    assert system.panel_tick(time.monotonic()); settle(system)
    assert system.panel_showing == "empty"


def test_shutter_press_ignored_while_busy(system):
    system.eink_display._display_busy = True
    assert reframe.handle_button_press(system, "shutter")["error"] == "display_busy"
    assert system.file_manager.photo_ids() == [] and frames(system) == []


def test_shutter_and_next_presses(system):
    reframe.handle_button_press(system, "shutter"); settle(system)
    first = system.panel_showing
    reframe.handle_button_press(system, "shutter"); settle(system)
    assert system.panel_showing != first
    reframe.handle_button_press(system, "next"); settle(system)
    assert system.panel_showing == first


class FakePicam:
    def __init__(self): self.configs = []
    def stop(self): pass
    def start(self): pass
    def set_controls(self, controls): pass
    def configure(self, config): pass
    def create_still_configuration(self, **kwargs):
        self.configs.append(kwargs); return {}


@pytest.mark.parametrize("flip", [False, True])
def test_rotate_180_sets_camera_transform(monkeypatch, flip):
    class Transform:
        def __init__(self, hflip=0, vflip=0): self.flips = (hflip, vflip)
    monkeypatch.setitem(sys.modules, "libcamera", types.SimpleNamespace(Transform=Transform))
    cm = object.__new__(reframe.CameraManager)
    cm.settings = merge_defaults({"camera": {"rotate_180": flip}}); cm.picam2 = FakePicam()
    cm.configure_camera()
    transform = cm.picam2.configs[0].get("transform")
    assert (transform.flips == (1, 1)) if flip else transform is None


def test_dashboard_has_spectra6_controls(dashboard):
    from fastapi.testclient import TestClient
    html = TestClient(dashboard.app).get("/").text
    for element_id in ("autofocus-mode", "rotate-180", "contrast", "orientation", "portrait-rotation", "slideshow-minutes"):
        assert f'id="{element_id}"' in html
    assert "/api/settings/defaults" in html


def test_unreadable_photo_is_skipped_not_fatal(system):
    p1, p2, p3 = snap(system), snap(system), snap(system)
    with open(os.path.join(reframe.SAVE_PATH, f"{p2}.jpg"), "r+b") as f:
        f.truncate(200)                                                 # power cut mid-save
    os.remove(os.path.join(reframe.PROCESSED_PATH, f"{p2}_dithered.png"))
    assert reframe.handle_button_press(system, "next")["success"]; settle(system)
    assert system.panel_showing == p1
    system.camera_manager.settings["display"]["slideshow_interval_seconds"] = 60
    system._set_panel_showing(p3)
    assert system.panel_tick(system.last_panel_change + 60); settle(system)
    assert system.panel_showing == p1


def test_deleting_shown_photo_mid_refresh_moves_to_newest(system):
    p1, p2 = snap(system), snap(system)
    system.eink_display._display_busy = True
    system.delete_photo_api(p2)
    system.eink_display._display_busy = False
    assert system.panel_tick(time.monotonic()); settle(system)
    assert system.panel_showing == p1


def test_failed_refresh_is_redrawn_after_restart(system, tmp_path):
    def broken(buf, fast_refresh=False): raise OSError("SPI write failed")
    system.eink_display.epd.display = broken
    assert system.panel_tick(time.monotonic()); settle(system)
    assert system.panel_showing is None
    restarted = make_system(tmp_path)
    assert restarted.panel_tick(time.monotonic()); settle(restarted)
    assert restarted.panel_showing == "empty"


def test_reused_id_ignores_stale_rotation_sidecar(system, tmp_path):
    system.file_manager.set_rotation("00000", 90)       # portrait save died after the sidecar
    restarted = make_system(tmp_path)
    pid = snap(restarted)                               # landscape, reuses 00000
    assert pid == "00000" and restarted.file_manager.get_rotation(pid) == 0
    captured = frames(restarted)[-1]
    restarted.show_photo(pid); settle(restarted)
    assert frames(restarted)[-1] == captured


def test_reprocess_keeps_portrait_upright(system):
    pid = snap(system, "portrait")
    assert system.reprocess_photo_api(pid, {"saturation": 0.4})["success"]
    png = Image.open(os.path.join(reframe.PROCESSED_PATH, f"{pid}_dithered.png"))
    assert png.size == (400, 600) and png.mode == "P"
    system.show_photo(pid); settle(system)                  # panel uses the reprocessed PNG, back in device coords
    assert frames(system)[-1] == bytes(reframe.ImageProcessor.palette_image_to_buffer(reframe.to_device(png, 90)))


def test_failed_refresh_backs_off_before_retrying(system):
    def broken(buf, fast_refresh=False): raise OSError("SPI write failed")
    system.eink_display.epd.display = broken
    now = time.monotonic()
    assert system.panel_tick(now); settle(system)
    assert not system.panel_tick(now + 1)                 # no retry storm against a dead panel
    assert system.panel_tick(now + reframe.REFRESH_RETRY_SECONDS + 1)


def test_next_skips_a_photo_it_cannot_find(system, monkeypatch):
    p1 = snap(system)
    Image.new("RGB", (8, 8)).save(os.path.join(reframe.SAVE_PATH, "00005.jpg"))
    lookup = system.file_manager.get_photo_info
    monkeypatch.setattr(system.file_manager, "get_photo_info",            # listed, gone by lookup
                        lambda pid: None if pid == "00005" else lookup(pid))
    assert system.next_photo()["success"]; settle(system)
    assert system.panel_showing == p1


def test_truncated_png_falls_back_to_the_original(system):
    pid = snap(system)
    with open(os.path.join(reframe.PROCESSED_PATH, f"{pid}_dithered.png"), "r+b") as f:
        f.truncate(100)                                                 # power cut while saving the PNG
    assert system.show_photo(pid)["success"]; settle(system)
    assert system.panel_showing == pid


def test_refresh_interrupted_by_restart_is_redrawn(system, tmp_path):
    p1, p2 = snap(system), snap(system)
    gate = threading.Event()
    system.eink_display.epd.display = lambda buf, fast_refresh=False: gate.wait(5)
    assert system.show_photo(p1)["success"]                 # service killed while this draws
    restarted = make_system(tmp_path)
    assert restarted.panel_showing is None                  # half-drawn panel: state unknown
    assert restarted.panel_tick(time.monotonic()); settle(restarted)
    assert restarted.panel_showing == p2
    gate.set(); settle(system)
