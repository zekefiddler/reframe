# spectra6-cam port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port reFrame to Zeke's build: GPIO buttons, next/slideshow, a landscape/portrait switch, an empty-roll screen, a rotate-180 switch for the camera, no PiSugar, a softer and more colourful default look, and an installer that works for any user.

**Architecture:**
- Keep upstream's two processes: `reframe.py` owns the hardware and serves a localhost API, and `dashboard.py` is the web UI.
- Settings defaults move into one shared module, `settings_defaults.py`.
- The panel always receives a 600×400 landscape image in device coordinates. Portrait only rotates the files written to disk, and each photo records its rotation in a sidecar file.
- Every panel refresh goes through one async path, which sets the busy flag.

**Tech Stack:**
- Python 3 on the Pi (Trixie, Pillow 11.1, numpy 2.2, picamera2 0.3.38, gpiozero/lgpio), FastAPI, and Waveshare `epd4in0e`.
- Tests are pytest on the Mac (`python3` = 3.12, which has Pillow, numpy, fastapi, httpx and qrcode).

**Spec:** `docs/superpowers/specs/2026-10-18-spectra6-cam-port-design.md`. Line references below are against `e86aebc`.

## Global Constraints

- **Networking is untouchable.**
  - `/boot/firmware/config.txt` keeps `dtoverlay=dwc2,dr_mode=peripheral`.
  - `cmdline.txt` keeps `modules-load=dwc2,g_ether g_ether.dev_addr=02:5c:a6:6c:00:01 g_ether.host_addr=02:5c:a6:6c:00:02`.
  - Never disable NetworkManager, tailscaled or `usb-gadget-reconnect.service`.
- **The panel buffer always uses the landscape mapping.** `dither_to_display_buffer` is only ever given 600×400 images in device coordinates. Never rotate the panel buffer for portrait.
- **No startup capture.** Refresh on every press, with no 180 s cool-down.
- **Empty-roll copy, exactly:** `Take your first picture and wait for it to appear on the screen.`
- **Buttons:** shutter on **GPIO5** and next on **GPIO6**, each wired to GND with internal pull-ups. The panel pins (RST17, DC25, CS8, BUSY24, PWR18) don't change.
- **No new pip dependencies.** Remove `smbus2`.
- **Camera Module 3 (imx708), as upstream** (Zeke switched from the v2 on 2026-10-21). Keep upstream's autofocus (`AfMode`, `autofocus_mode`, `_settle_autofocus`) and the HDR helper (`scripts/enable_hdr.sh`, `_enable_camera_hdr`) unchanged.
- **Zeke runs every `sudo`,** in as few batched `ssh -t` commands as possible. Claude never asks for or stores his password.
- **Root surface: no passwordless sudo.** Zeke ruled on 2026-10-21 that the installer adds no sudoers rule, because the helper installs files the login user can write. Updates finish over SSH with `sudo /usr/local/sbin/reframe-apply-update`.
- **Match upstream style:**
  - module-level helpers in `reframe.py`
  - results shaped as `{"success": bool, ...}` dicts
  - `logging.info` messages
  - number inputs in the dashboard, like their siblings (it has no range sliders)
- **PRs go to the fork only:** `gh pr create --repo zekefiddler/reframe --base main`, never to kaloyaan/reframe.

## Review Focus

1. **Orientation changed between shots.** An old landscape photo and a new portrait one must both appear on the panel exactly as they did when taken. (Test: Task 4 `test_mixed_orientations_show_like_capture`.)
2. **Dithered PNG missing** (save failed or was removed). "Show" and next must re-dither from the original with the **saved** settings, never the defaults. (Task 4 `test_show_photo_redithers_with_saved_settings`.)
3. **Next or show pressed while the panel is refreshing.** The press is ignored and the position doesn't move. (Task 5 `test_next_ignored_while_busy`.)
4. **Delete-all, or deleting the photo on the panel.** The panel moves to the newest remaining photo, or to the empty-roll screen, even if the last delete happened mid-refresh. (Task 5 `test_delete_all_ends_on_empty_roll`.)
5. **Service restart or crash loop** (`Restart=always`). No capture and no repeated refresh: the empty-roll screen is drawn once across restarts. (Task 5 `test_empty_roll_drawn_once_across_restarts`.)

---

### Task 1: One source of settings defaults, plus the new keys

**Files:**
- Create: `settings_defaults.py`
- Modify: `reframe.py:340-374` (`CameraManager.load_settings`)
- Modify: `dashboard.py:146-219` (`validate_settings`), `dashboard.py:221-339` (`SettingsManager`), and add one route next to `@app.get("/api/settings")` (`dashboard.py:3343`)
- Delete: `settings.example.json`, along with its copy step in `install.sh:214-219` and the line mentioning it in `README.md:80`
- Test: `test_spectra6.py` (create)

**Interfaces:**
- Produces:
  - `settings_defaults.DEFAULT_SETTINGS: dict`
  - `settings_defaults.merge_defaults(saved: dict | None) -> dict`: a deep copy of the defaults with `saved` deep-merged over it. It never aliases `DEFAULT_SETTINGS`.
  - `GET /api/settings/defaults`: returns `merge_defaults({})`.
- Default values:
  - Same as upstream except:
    - `camera.rotate_180: false` (`camera.autofocus_mode` stays at 2)
    - `processing.saturation: 0.75`
    - `processing.color_factor: 1.6`
    - `processing.contrast: 0.85`
    - `display.orientation: "landscape"`
    - `display.portrait_rotation: 90`
    - `display.slideshow_interval_seconds: 0`
    - `system.auto_timeout_enabled: false`
    - `system.show_dashboard_qr_on_wifi_connect: false`
  - The remaining upstream keys keep their values: `exports`, `extensions.arena`, `system.camera_name`, `display.auto_display/display_timeout/fast_refresh`, and `bayer_size` 4.
- Validation:
  - `processing.contrast` is a number from 0.3 to 2.
  - `display.orientation` is `landscape` or `portrait`.
  - `display.portrait_rotation` is 90 or 270.
  - `display.slideshow_interval_seconds` is an integer from 0 to 86400.
  - `camera.rotate_180` is a boolean.
  - Keep the `autofocus_mode` check. Extra keys left in an old `settings.json` are ignored.

- [ ] **Step 1: Write the failing tests.** The top of `test_spectra6.py` stubs out the hardware:

```python
import os, sys, types, threading
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
```

- [ ] **Step 2: Run.** `cd ~/repos/reframe && python3 -m pytest test_spectra6.py -q`. Expected: FAIL with `ModuleNotFoundError: settings_defaults`.
- [ ] **Step 3: Implement.**
  - Write `settings_defaults.py` using `copy.deepcopy`.
  - `CameraManager.load_settings` returns `merge_defaults(json.load(f))`, or `merge_defaults({})` on `FileNotFoundError`/`JSONDecodeError`.
  - In `SettingsManager`: set `self.default_settings = merge_defaults({})`, make `_merge_with_defaults` call `merge_defaults` (keep the legacy QR-key migration ahead of it), and have `load_settings`'s fallback return `merge_defaults({})`.
  - Add the new checks to `validate_settings`.
  - Delete `settings.example.json` and its install step. `SettingsManager._ensure_settings_file` already writes the defaults on first start.
- [ ] **Step 4: Run.** Same command. Expected: all pass.
- [ ] **Step 5: Commit.** `git add -A settings_defaults.py reframe.py dashboard.py test_spectra6.py install.sh README.md settings.example.json && git commit -m "feat: shared settings defaults with spectra6-cam keys"`

---

### Task 2: Image helpers for contrast, cropping, orientation and text screens

**Files:**
- Modify: `reframe.py`:
  - `:291-320` (`render_dashboard_qr_image`)
  - `:612-634` and `:751-784` (the two dither paths)
  - `:787-846` (`dither_to_display_buffer`)
  - `:849-861` (`resize_image`)
  - `:955-975` (`process_photo_with_settings`)
- Test: `test_spectra6.py`

**Interfaces:**
- Produces (module level in `reframe.py`):
  - `EMPTY_ROLL_TEXT = "Take your first picture and wait for it to appear on the screen."`
  - `orientation_rotation(display_settings: dict) -> int`: 0 for landscape, otherwise `portrait_rotation`.
  - `to_saved(image, rotation: int) -> Image`, which is `image.rotate(rotation, expand=True)` and returns the image itself for 0. `to_device(image, rotation: int) -> Image` is its inverse, `rotate(-rotation, expand=True)`.
  - `render_text_screen(text: str, rotation: int) -> Image`: an RGB image, always 600×400 in device coordinates. Draw black text, word-wrapped (`textwrap`), with `ImageFont.load_default(size=28)`, on a white canvas: 600×400 when rotation is 0, otherwise 400×600. Then return `to_device(canvas, rotation)`.
  - `render_dashboard_qr_image(access_info, rotation: int = 0) -> Image`: also always 600×400 in device coordinates. For portrait, lay the QR out on top (250 px, at x=75) with the text under it, then apply `to_device`.
  - `dither_settings(processing: dict | None) -> dict`: the seven keyword arguments `saturation, brightness_factor, color_factor, contrast, dithering_method, bayer_size, threshold_scale`, filled in from `merge_defaults({})["processing"]`.
  - `ImageProcessor.enhance(image, brightness_factor, contrast, color_factor) -> Image`: the order is Brightness, then Contrast, then Color. Both dither paths call it in place of their duplicated enhance code.
  - `apply_dithering`, `apply_ordered_dithering` and `dither_to_display_buffer` each gain a keyword `contrast=1.0`. Call sites pass `**dither_settings(...)`.
  - `ImageProcessor.resize_image` uses `ImageOps.fit(image, size, resample)` in place of `resize`. That centre-crops to the target aspect and never squashes. Keep the 2× `reduce` fast path.
  - `process_photo_with_settings(original_path, output_path, processing_settings, rotation=0)`: apply `to_device`, then resize, then dither with `dither_settings`. Save `to_saved(dithered, rotation)`.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run.** `python3 -m pytest test_spectra6.py -q`. Expected: the new tests fail with `AttributeError`.
- [ ] **Step 3: Implement** the interfaces above. Draw the text from a top margin of 40 px, so the "upright" test has something to assert.
- [ ] **Step 4: Run.** Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -am "feat: contrast step, crop-not-squash, orientation and text-screen helpers"`

---

### Task 3: Photo store: rotation sidecars, ids, and next-photo order

**Files:**
- Modify: `reframe.py:1020-1142` (`FileManager`), plus one module-level function
- Test: `test_spectra6.py`

**Interfaces:**
- Produces:
  - `next_photo_id(ids: list[str], current: str | None) -> str | None`. `ids` is ascending. A `current` that isn't in `ids`, or isn't a photo id (`None`, `"empty"`), counts as "the newest is showing". The function returns the largest id that is less than `current`, wrapping to the newest, or `None` when `ids` is empty. A deleted `current` still steps to the next older photo.
  - `FileManager.photo_ids() -> list[str]`: the stems of image files in `save_path`, ascending.
  - `FileManager.get_rotation(photo_id) -> int` and `set_rotation(photo_id, rotation: int) -> None`. The sidecar is `photos/<id>.json`, containing `{"rotation": 90}`. When the sidecar is missing, the rotation is 0, and 0 is never written. `delete_photo` also removes the sidecar.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run.** Expected: FAIL (`AttributeError: next_photo_id`).
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run.** Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -am "feat: per-photo rotation sidecar and next-photo order"`

---

### Task 4: One async panel path; capture and show respect orientation

**Files:**
- Modify: `reframe.py`:
  - `:1157-1363` (`EInkDisplay`)
  - `:1366-1380` (`CameraSystem.__init__`)
  - `:1509-1614` (capture, display, reprocess, delete)
  - `:1691-1703` and `:1842-1848` (route order)
- Test: `test_spectra6.py`

**Interfaces:**
- Consumes: everything from Tasks 2 and 3.
- Produces:
  - `EInkDisplay._refresh_async(action: Callable[[epd], None], label: str) -> bool`. This is the body of `display_buffer_async`, generalised. It returns False, without starting anything, when the panel is busy. `display_buffer_async(buffer) -> bool` wraps it.
  - `clear_display()` and `display_dashboard_qr(access_info, rotation=0)` both go through `_refresh_async` and return `{"success": False, "error": "display_busy"}` when busy.
  - `CameraSystem.__init__(settings_path="settings.json", eink_display=None, camera_manager=None)`. The new `camera_manager` argument is for tests.
  - Panel state on `CameraSystem`:
    - `panel_showing: str | None`, which is a photo id or `"empty"`. It's persisted in `PANEL_STATE_PATH = os.path.join(RUNTIME_PATH, "panel_showing")`, read in `__init__` and written on each dispatched refresh. Write it atomically (temp file + `os.replace`, as `update_diagnostics` does). `capture_photo_api` sets it only when it actually dispatched a refresh (that is, when `auto_display` is on).
    - `last_panel_change: float` (monotonic).
  - `ImageProcessor.palette_image_to_buffer(image) -> bytearray`: pull steps 2 and 3 out of `dither_to_display_buffer` (rotate 600×400 to the panel, then the `DITHER_TO_HW` index map and nibble packing). Both `dither_to_display_buffer` and the PNG path below use it, so a saved PNG produces byte-identical frames to the capture.
  - `CameraSystem.panel_buffer_for(photo_id) -> bytearray | None`:
    - With a dithered PNG: `to_device(png, rotation)`. If that is 600×400 in mode `P`, use `palette_image_to_buffer`.
    - Otherwise, re-dither from the original: `to_device(original, rotation)`, then `resize_image`, then `dither_to_display_buffer(..., **dither_settings(saved processing))`.
    - It returns None when the photo isn't found.
  - `CameraSystem.show_photo(photo_id) -> dict`: builds the buffer and dispatches it async. When the refresh starts, it sets `panel_showing` and `last_panel_change`. It returns `{"success": False, "error": "display_busy" | "not_found"}` when it can't.
  - `capture_photo_api`:
    - The panel buffer is unchanged from upstream: 600×400 device coordinates.
    - The background save writes the sidecar first (`set_rotation` with `orientation_rotation(display settings)`), then `to_saved(original)` as JPEG and `to_saved(dithered)` as PNG.
    - It sets `panel_showing = photo_id` and exposes the thread as `self._save_thread`.
  - `display_photo_api(photo_id)` is now `show_photo`. `EInkDisplay.display_photo_by_id` is deleted. Its only caller was `display_photo_api`; `grep -n display_photo_by_id` confirms that.
  - `reprocess_photo_api` passes `rotation=file_manager.get_rotation(photo_id)` (through `reprocess_photo_by_id`).
  - Register `/api/display/clear` before `/api/display/{photo_id}`.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run.** Expected: the new tests fail (`TypeError: unexpected keyword 'camera_manager'`).
- [ ] **Step 3: Implement** the interfaces above. The `api_display` route keeps its `_operation_lock`, but the refresh no longer happens under it.
- [ ] **Step 4: Run.** Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -am "feat: async panel path; orientation-aware capture, show and reprocess"`

---

### Task 5: Next, slideshow and the empty-roll screen

**Files:**
- Modify: `reframe.py`: `CameraSystem`, and routes after `/api/capture` (`:1679-1689`)
- Test: `test_spectra6.py`

**Interfaces:**
- Consumes: from Task 4, `show_photo`, `panel_showing`, `last_panel_change` and `_refresh_async`.
- Produces:
  - `CameraSystem.next_photo() -> dict`: returns `display_busy` when busy, without moving. Otherwise it calls `show_photo(next_photo_id(file_manager.photo_ids(), panel_showing))`. With an empty roll it returns `{"success": False, "error": "empty_roll"}`.
  - `CameraSystem.show_empty_roll_if_needed() -> bool`: does nothing if photos exist or `panel_showing == "empty"`. Otherwise it dispatches `render_text_screen(EMPTY_ROLL_TEXT, orientation_rotation(display settings))` and sets `panel_showing = "empty"`.
  - `CameraSystem.panel_tick(now: float) -> bool`. It runs every second from a daemon thread, `start_panel_ticker()`, under `_operation_lock` with a non-blocking acquire. It skips while busy. With an empty roll it calls `show_empty_roll_if_needed()`. Otherwise, if `slideshow_interval_seconds > 0` and `now - last_panel_change >= interval`, it calls `next_photo()`. It returns True if it dispatched a refresh.
  - `delete_photo_api(photo_id)`: when the deleted id was `panel_showing` and photos remain, show the newest. An empty roll is left to `panel_tick`, so the last delete of a delete-all still lands even mid-refresh.
  - `POST /api/next` on the localhost API, the same as the GPIO6 press. It's used for on-device checks before the buttons are wired.

- [ ] **Step 1: Write the failing tests**

```python
def test_empty_roll_drawn_once_across_restarts(system, tmp_path):
    assert system.panel_tick(time.monotonic()); settle(system)
    assert system.panel_showing == "empty" and len(frames(system)) == 1
    restarted = make_system(tmp_path)
    assert not restarted.panel_tick(time.monotonic())
    assert frames(restarted) == []

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
```

Add `import time` to the test imports.

- [ ] **Step 2: Run.** Expected: FAIL (`AttributeError: panel_tick`).
- [ ] **Step 3: Implement** the interfaces above.
- [ ] **Step 4: Run.** Expected: all pass.
- [ ] **Step 5: Commit.** `git commit -am "feat: next photo, slideshow ticker, empty-roll screen"`

---

### Task 6: Hardware glue: rotate_180, GPIO buttons, startup

**Files:**
- Modify: `reframe.py`:
  - `:154-156` (button diagnostics)
  - `:467-509` (`CameraManager.configure_camera`)
  - `:1868-2017` (`main`)
  - `:2069-2073` (Ctrl-C)
- Modify: `requirements.txt` (remove `smbus2`)
- Leave alone: the autofocus code and the HDR helper (see Global Constraints).

**Interfaces:**
- Constants: `SHUTTER_PIN = 5`, `NEXT_PIN = 6`. They go next to the other HARDWARE constants.
- `CameraManager.configure_camera` passes `transform=Transform(hflip=1, vflip=1)` to `create_still_configuration` when `camera.rotate_180` is set. It imports `from libcamera import Transform` inside the function, so tests never import libcamera.
- `main()`:
  1. Keep upstream's `_enable_camera_hdr()` call before the camera opens. Build the `EInkDisplay` and call `prepare_async` when `auto_display` is on.
  2. Create `CameraSystem` with no startup capture.
  3. `_notify_systemd_ready("Camera ready")`.
  4. Start the API.
  5. `start_dashboard_qr_monitor()`, `start_timeout_monitor()` and `start_panel_ticker()`.
  6. Run a poll loop over `gpiozero.Button(SHUTTER_PIN, pull_up=True, bounce_time=0.05)` and `Button(NEXT_PIN, ...)`. It acts on the press edge:
     - Shutter: ignored and logged when busy. Otherwise `capture_photo_api()` under `_operation_lock`.
     - Next: `next_photo()` under `_operation_lock`.

  Drop the long-press logic, smbus and `bus.close()`.
- `record_service_failure`: the button branch matches `("gpiozero", "lgpio")`, with the message `"Buttons unavailable. Check the shutter (GPIO5) and next (GPIO6) wiring."`.
- Ctrl-C: delete the `epd4in0e.epdconfig.module_exit(cleanup=True)` line. `main()`'s `finally` already puts the panel to sleep.

- [ ] **Step 1: Make the change.**
- [ ] **Step 2: Verify.** `python3 -m py_compile reframe.py && python3 -m pytest test_spectra6.py -q && ! grep -nE "smbus|epd4in0e\.epdconfig" reframe.py requirements.txt && grep -c "_enable_camera_hdr()" reframe.py`. Expected: the compile succeeds, all tests pass, the first grep prints nothing, and the HDR call is still there.
- [ ] **Step 3: Commit.** `git add -A && git commit -m "feat: rotate_180, GPIO5/6 buttons, no startup photo"`

---

### Task 7: Dashboard controls

**Files:**
- Modify: `dashboard.py`:
  - settings HTML: the camera, processing and system sections (keep the autofocus select at `:1709-1717`)
  - `populateSettingsForm` `:2474-2510`
  - `saveSettings` `:2601-2650`
  - `resetSettings` `:2669-2710`
- Test: `test_spectra6.py`

**Interfaces:**
- Consumes: `GET /api/settings/defaults` (Task 1).
- Inputs, using the same `setting-group` markup as their neighbours:
  - Camera: `rotate-180` (a select, true/false). Help text: "Turn on if the camera is mounted upside down in the case."
  - Processing: `contrast` (number, step 0.05, from 0.3 to 2). Help text: "Below 1 softens contrast before dithering."
  - System: `orientation` (select, landscape/portrait); `portrait-rotation` (select, 90/270), with the help text "Which way is up when you hold it in portrait."; and `slideshow-minutes` (number, 0 to 1440, where 0 = off), saved as `display.slideshow_interval_seconds = minutes * 60`.
- `resetSettings` fetches `/api/settings/defaults`, sets `extensions.arena.access_token_clear = true` and posts it, replacing the hardcoded object.

- [ ] **Step 1: Write the failing test**

```python
def test_dashboard_has_spectra6_controls(dashboard):
    from fastapi.testclient import TestClient
    html = TestClient(dashboard.app).get("/").text
    for element_id in ("autofocus-mode", "rotate-180", "contrast", "orientation", "portrait-rotation", "slideshow-minutes"):
        assert f'id="{element_id}"' in html
    assert "/api/settings/defaults" in html
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** `python3 -m pytest test_spectra6.py -q`. Then check the page in a browser:
  1. Start it with `python3 dashboard.py &`.
  2. Open `http://127.0.0.1:8000`, then open settings, change contrast to 0.9, save, reopen, and check that 0.9 stuck. Do the same for slideshow minutes.
  3. Kill the server.
  4. Run `git checkout -- settings.json 2>/dev/null; rm -f settings.json`. `settings.json` is gitignored.

  Expected: the tests pass, and the values survive the round trip.
- [ ] **Step 5: Commit.** `git commit -am "feat: dashboard controls for orientation, slideshow, contrast, rotate 180"`

---

### Task 8: An installer that works for any user, without PiSugar

**Files:**
- Modify: `install.sh`, `scripts/reframe-apply-update`, `scripts/reframe-python`
- Leave untouched: `systemd/*.service`, which keep `cam` and `/home/cam/reframe` as placeholders and are rendered at install time. Also leave `scripts/reframe-rtc-sync` and the two RTC units in the repo, unused.

**Interfaces:**
- `/etc/default/reframe` is root-owned, mode 0644, and written by `install.sh`. It holds `REFRAME_USER=`, `REFRAME_GROUP=` and `REFRAME_REPO=`.
- `reframe-apply-update`:
  - Defaults to `cam`/`cam`/`/home/cam/reframe`, then sources `/etc/default/reframe` if it exists.
  - The user's home comes from `getent passwd`.
  - `render_unit NAME` runs `sed -e "s|/home/cam/reframe|$REFRAME_REPO|g" -e "s|^User=cam$|User=$REFRAME_USER|" -e "s|^Group=cam$|Group=$REFRAME_GROUP|"` into `/etc/systemd/system/NAME` (mode 0644).
  - It renders the three reframe units.
  - It drops the RTC and pisugar lines, and both `timedatectl` lines. It keeps `enable_hdr.sh` in `required_files` and its chmod. NTP is already on, and the timezone stays America/New_York.
  - It keeps the venv/pip step as `runuser -u "$REFRAME_USER"`.
  - `required_files` lists only the files it still installs.
- `install.sh`:
  - Refuses to run as root. Drops the `cam` user and path checks.
  - apt installs `git python3-pip python3-venv python3-picamera2 python3-spidev python3-gpiozero python3-lgpio netcat-openbsd v4l-utils` (`v4l2-ctl` is what the HDR helper runs).
  - Drops `do_i2c`, the PiSugar section, and the RTC section and timezone. Keeps the enable_hdr chmod.
  - Keeps SPI, `camera_auto_detect`, the boot trims, groups and the service trims.
  - Writes `/etc/default/reframe`, installs the helper (no sudoers rule), then runs `sudo /usr/local/sbin/reframe-apply-update`. That one script now builds the venv and installs the units.
  - Enables only `reframe`, `reframe-dashboard` and `reframe-dashboard-proxy`.
- `reframe-python`: `REPO_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"`, with `VENV_PYTHON="$REPO_DIR/.venv/bin/python"`.

- [ ] **Step 1: Make the change.**
- [ ] **Step 2: Verify the rendering offline.**

```bash
bash -n install.sh && bash -n scripts/reframe-apply-update && sh -n scripts/reframe-python
d=$(mktemp -d); for u in reframe reframe-dashboard reframe-dashboard-proxy; do
  sed -e "s|/home/cam/reframe|/home/youmeandbirdie/reframe|g" -e "s|^User=cam$|User=youmeandbirdie|" -e "s|^Group=cam$|Group=youmeandbirdie|" systemd/$u.service > $d/$u.service; done
! grep -n "cam\b\|/home/cam" $d/*.service | grep -v youmeandbirdie
grep -n "^User=\|^Group=\|ExecStart=" $d/*.service
! grep -nE "pisugar|rtc|timedatectl|do_i2c|cam ALL" install.sh scripts/reframe-apply-update
grep -n "enable_hdr" install.sh scripts/reframe-apply-update
```

  Expected: the syntax checks pass, the negative greps print nothing, and the last grep still finds the enable_hdr lines. The proxy unit keeps `User=root`, and the other two show `User=youmeandbirdie` with paths under `/home/youmeandbirdie/reframe`. Also check by eye that the `sed` in `render_unit` matches the one used here.
- [ ] **Step 3: Commit.** `git commit -am "feat: installer for any user; drop PiSugar, RTC and UTC"`

---

### Task 9: Deploy and on-device checks (Zeke as the eyes and fingers)

**Files:** none (device work)

- [ ] **Step 1 (Claude):** Push the branch: `git push -u origin feat/spectra6-cam-port`. Run `python3 -m pytest test_spectra6.py -q` first; it should pass.
- [ ] **Step 2 (Zeke, sudo batch 1):** `ssh -t spectra6-cam 'sudo apt install -y git'`
- [ ] **Step 3 (Claude):** Over `ssh spectra6-cam`, run `git clone -b feat/spectra6-cam-port https://github.com/zekefiddler/reframe ~/reframe && mkdir -p ~/boot-before && cp /boot/firmware/config.txt /boot/firmware/cmdline.txt ~/boot-before/`
- [ ] **Step 4 (Zeke, sudo batch 2):** `ssh -t spectra6-cam 'cd ~/reframe && ./install.sh'`. Answer **N** to "Reboot now?".
- [ ] **Step 5 (Claude):** Run `diff ~/boot-before/config.txt /boot/firmware/config.txt; diff ~/boot-before/cmdline.txt /boot/firmware/cmdline.txt`.
  - Expected: only reFrame's trims show up (`disable-bt`, audio, `quiet loglevel=3`, `console=serial0` removed).
  - `grep -c "dtoverlay=dwc2,dr_mode=peripheral" config.txt` should print 1.
  - `cmdline.txt` should still contain `modules-load=dwc2,g_ether` and both `g_ether.*` args.
  - Stop if anything else changed.
- [ ] **Step 6 (Zeke):** `ssh -t spectra6-cam 'sudo reboot'`.
- [ ] **Step 7 (Claude):** Check every network path and the services.
  - `ssh spectra6-cam true` (Tailscale)
  - From the Mac: `ping -c2 10.55.2.1` and `ssh spectra6-cam-usb true` (USB)
  - `ping -c2 spectra6-cam.local` (Wi-Fi)
  - `systemctl is-active reframe reframe-dashboard reframe-dashboard-proxy usb-gadget-reconnect tailscaled`
  - `curl -s localhost/api/settings | head -c 300`
  - `journalctl -u reframe -b | grep -i hdr`
  - Expected: all paths answer, the services are `active` (the gadget oneshot shows `inactive`/exited 0), settings JSON comes back, and the log says `HDR enabled on /dev/...`.
- [ ] **Step 8 (Zeke, one checklist):** Open `http://100.99.67.104/` and report on each item:
  1. Does the panel show *Take your first picture…* reading upright in landscape?
  2. Press the dashboard's shutter. Does the photo appear in about 20 s, in focus, uncropped and round things round?
  3. Take 2 more photos. Claude then runs `curl -X POST localhost:8077/api/next` three times, a refresh apart. Does the panel step back twice, then wrap to the newest?
  4. Set orientation to portrait and take a photo. On the Mac, does the gallery copy stand upright? If it's upside down, switch the portrait rotation to 270 and retake.
  5. Set the slideshow to 1 minute. Does it advance about a minute after the last refresh? Then set it back to 0.
  6. Tone: compare the new photo with the spike. Adjust contrast, saturation and colour factor until you like it. Claude will copy the values you settle on into `DEFAULT_SETTINGS` as a follow-up commit.
  7. Download one original. Claude compares it with `sha256sum` against `~/reframe/photos/`.
  8. Once the buttons are wired (GPIO5/GPIO6 to GND): does shutter take a photo, does next step back, and is a press during a refresh ignored?
- [ ] **Step 9 (Claude):** Fix anything the checklist turns up, test-first where it can be tested off-device, then push and pull on the Pi.
- [ ] **Step 10 (Claude):** Record the outcome in a comment on Epaper-Screens #3 (`gh issue comment 3 --repo zekefiddler/Epaper-Screens`).

---

### Task 10: PR and self-review

- [ ] **Step 1:** Run `gh pr create --repo zekefiddler/reframe --base main --head feat/spectra6-cam-port`. The title is "spectra6-cam port". The body covers what changed, the on-device results and `Refs zekefiddler/Epaper-Screens#3`.
- [ ] **Step 2:** Run `/review-pr` and `/security-review`. Fix every Critical and Important finding and push.
- [ ] **Step 3:** Poll until Copilot's review has landed:
  - `gh pr view <n> --repo zekefiddler/reframe --json reviews --jq '[.reviews[].author.login]'`
  - `gh api repos/zekefiddler/reframe/pulls/<n>/comments --jq '.[] | "\(.path):\(.line)\n\(.body)\n"'`

  Apply each comment or dismiss it with a reason. Ask Zeke about any that are ambiguous.
- [ ] **Step 4:** Post a PR comment saying what ran, what was fixed and what was deferred. Tell Zeke it's ready, and **don't merge** without his say-so.
