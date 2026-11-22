# Photo Strip and Delete-Selected Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pick photos in the dashboard gallery and print 1 to 8 of them as a photobooth strip on the MX06 thermal printer over Bluetooth, or delete them.

**Architecture:** Two small new modules, `photostrip.py` (photos to one black-and-white strip image) and `mx06.py` (strip image to printer commands, and a Bluetooth sender that obeys the printer's pause signal). The dashboard service runs a print as a background job with a progress endpoint, the way it already runs "delete all". The camera service (`reframe.py`) is not touched.

**Tech Stack:** Python 3.13 on the Pi (3.12 in the dev venv), Pillow, FastAPI, `bleak` (new), bash for setup. Tests are plain pytest in `test_spectra6.py`.

**Spec:** `docs/superpowers/specs/2026-11-21-photo-strip-and-delete-design.md`. The two spike scripts in `docs/superpowers/specs/2026-11-21-photo-strip-spike/` are the reference for the print's look and the printer protocol; read them before Tasks 2 to 4.

## Global Constraints

- No printing or Bluetooth code in `reframe.py`, and nothing that waits on its `_operation_lock`.
- No OpenCV and no numpy for printing. `bleak` is the only new pip dependency.
- No sudo rules and no root helpers for printing.
- Nothing retries a print automatically.
- A strip holds 1 to 8 photos, in the order given.
- Strip values, exactly: width 384 px; margin 16 px; gap 16 px; photo width 352 px keeping its shape; autocontrast cutoff 1; gamma 0.75; Floyd–Steinberg via `convert("1")`; quality `0x32`; energy `0xFFFF`; feed 25.
- The printer is found by its Bluetooth name on every print, 10 s scan limit. The name is the setting `printer.name`, default `MX06`.
- Photo ids arrive from the network. An id is used in a file path only after it matches `^[0-9]{1,10}$`.
- Tests run with `.superpowers/sdd/2026-10-20-spectra6-cam-port/venv/bin/python -m pytest -q` from the repo root. That venv has no `bleak`: import it inside functions, and test against fakes.
- Async code is tested with `asyncio.run(...)` inside ordinary test functions. No pytest plugins.
- Branch `feat/photo-strip`. It has no upstream on purpose: the first push is `git push -u origin feat/photo-strip`. The PR's base is `feat/spectra6-cam-port`.
- On the Pi, Zeke runs every sudo step. Never re-run `install.sh` there.
- End commit messages with the attribution lines the session gives.

## Review Focus

1. **A picked photo that cannot be read** (a 0-byte `00059.jpg` was on the roll on 2026-11-22): the print is refused before anything is sent, with a message naming the photo. Test in Tasks 2 and 5.
2. **The printer drops off mid-print** (switched off, out of range): the job ends as an error saying the strip may be incomplete, and the next print can start. Test in Tasks 4 and 5.
3. **Bluetooth is not available on the camera** (setup not run, `bleak` missing, BlueZ stopped): the page says so in plain words, not a traceback. Test in Task 5.
4. **A photo that vanished after it was picked** (deleted in another tab): print is refused naming it; delete reports it as failed and still deletes the rest. Test in Task 5.
5. **The gallery redraws while photos are picked** (it refreshes itself, and paging redraws it): the picks and their numbers survive. No off-device test can see this; it is an on-device check in Task 8, and Task 6 keeps the picks in a variable that `renderGallery()` reads.

---

### Task 1: Prove a print from the Pi (gate)

Nothing below is built until this passes. If it fails, stop and bring the evidence to Zeke.

**Files:** none in the repo. Scratch folder `~/strip-spike/` on the Pi.

**Interfaces:**
- Produces: three facts later tasks use. (a) Whether BlueZ accepts the camera's user without the `bluetooth` group. (b) Whether the BlueZ MTU fix is needed and the MTU seen. (c) Boot time with Bluetooth on (it was 4.8 s kernel + 58.3 s userspace with it off).

- [ ] **Step 1 (Zeke):** run on the Pi:
  `sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.before-bluetooth`
  `sudo sed -i 's/^dtoverlay=disable-bt$/#dtoverlay=disable-bt/' /boot/firmware/config.txt`
  `sudo systemctl enable bluetooth.service` then `sudo reboot`.
- [ ] **Step 2:** over `ssh spectra6-cam`, confirm `ls /sys/class/bluetooth` shows `hci0`, `systemctl is-active bluetooth` says `active`, and the camera services are active. Record `systemd-analyze`.
- [ ] **Step 3:** `python3 -m venv --system-site-packages ~/strip-spike/venv && ~/strip-spike/venv/bin/pip install bleak`. Copy the two spike scripts there. Change `print_classic_fc.py` in two ways only: find the printer with `BleakScanner.find_device_by_filter(lambda d, ad: (d.name or "") == "MX06", timeout=10.0)`, and after connecting call `await c._backend._acquire_mtu()` when the backend class is `BleakClientBlueZDBus`. Print the MTU.
- [ ] **Step 4:** make a one-photo strip from a photo on the roll and print it. Then a five-photo strip. Zeke confirms each came out whole.
- [ ] **Step 5:** if BlueZ refused the user, note the exact error and whether adding the user to `bluetooth` fixed it (that is a sudo step for Zeke).
- [ ] **Step 6:** write the three facts into the spec's "Open" section and commit.

### Task 2: The strip maker

**Files:**
- Create: `photostrip.py`
- Test: `test_spectra6.py`

**Interfaces:**
- Produces: `STRIP_WIDTH = 384`, `MARGIN = 16`, `GAP = 16`, `FRAME_WIDTH = 352`; `class UnreadablePhoto(Exception)` with a `.path` attribute; `make_strip(paths: list[str]) -> PIL.Image.Image` returning a mode `"1"` image 384 px wide.

- [ ] **Step 1: Write the failing tests**

```python
def photo_file(tmp_path, name, size, shade):
    p = tmp_path / name; Image.new("RGB", size, (shade,) * 3).save(p); return str(p)

def test_strip_of_landscape_photos_has_the_test_strips_size(tmp_path):
    import photostrip
    paths = [photo_file(tmp_path, f"{i}.jpg", (1200, 800), 128) for i in range(5)]
    strip = photostrip.make_strip(paths)
    assert strip.mode == "1" and strip.size == (384, 1266)      # the strip Zeke approved was 1266 rows

def test_strip_keeps_a_portrait_photos_shape(tmp_path):
    import photostrip
    strip = photostrip.make_strip([photo_file(tmp_path, "p.jpg", (800, 1200), 128)])
    assert strip.size == (384, 16 + 528 + 16)

def test_strip_follows_the_order_given_and_leaves_white_borders(tmp_path):
    import photostrip
    dark, light = photo_file(tmp_path, "d.png", (1200, 800), 0), photo_file(tmp_path, "l.png", (1200, 800), 255)
    strip = photostrip.make_strip([dark, light]).convert("L")
    assert strip.getpixel((192, 16 + 117)) == 0 and strip.getpixel((192, 16 + 234 + 16 + 117)) == 255
    assert strip.getpixel((8, 100)) == 255 and strip.getpixel((192, 16 + 234 + 8)) == 255   # margin and gap

def test_strip_refuses_a_photo_it_cannot_read(tmp_path):
    import photostrip
    empty = tmp_path / "00059.jpg"; empty.write_bytes(b"")
    with pytest.raises(photostrip.UnreadablePhoto) as e:
        photostrip.make_strip([photo_file(tmp_path, "ok.jpg", (1200, 800), 128), str(empty)])
    assert e.value.path == str(empty)
```

A flat grey photo has no contrast to stretch; `make_strip` must not divide by zero on it (the first two tests use one).

- [ ] **Step 2:** run `pytest -q -k strip_`; expect failures for a missing `photostrip` module.
- [ ] **Step 3: Implement `photostrip.py`.** Follow `make_strip.py` in the spike folder: tone each photo (greyscale, `ImageOps.autocontrast(cutoff=1)`, gamma 0.75) before scaling; scale to `FRAME_WIDTH` wide with Lanczos, height `FRAME_WIDTH * h // w` (234 for a 1200 × 800 photo, which is what makes five photos 1266 rows); paste on a white `"L"` sheet; `convert("1")` once at the end. Wrap opening and loading a photo so any failure raises `UnreadablePhoto(path)`.
- [ ] **Step 4:** run `pytest -q`; expect all green.
- [ ] **Step 5:** commit `feat: photostrip.py makes the thermal strip image`.

### Task 3: Printer commands

**Files:**
- Create: `mx06.py`
- Test: `test_spectra6.py`

**Interfaces:**
- Produces: `command(code: int, payload: bytes) -> bytes`; `encode_row(row: Sequence[int]) -> bytes` (384 values, 1 = black, returns one whole command); `build_job(image: PIL.Image.Image, energy: int = 0xFFFF) -> tuple[bytes, int]` returning the whole job and its row count; `PAUSE = bytes.fromhex("5178ae0101001070ff")`; `RESUME = bytes.fromhex("5178ae0101000000ff")`; `STATE_REPLY_PREFIX = bytes.fromhex("5178a3")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_printer_command_frames_match_known_bytes():
    import mx06
    assert mx06.command(0xA4, b"\x32") == bytes.fromhex("5178a4000100329eff")
    assert mx06.command(0xA1, b"\x30\x00") == bytes.fromhex("5178a10002003000f9ff")

def test_a_row_is_run_length_encoded_and_falls_back_to_raw_bits():
    import mx06
    assert mx06.encode_row([1] * 8 + [0] * 376) == mx06.command(0xBF, bytes([0x88, 0x7F, 0x7F, 0x7A]))
    assert mx06.encode_row([1, 0] * 192) == mx06.command(0xA2, bytes([0x55] * 48))    # too busy for run lengths

def test_a_job_opens_and_closes_the_way_the_printer_expects():
    import mx06
    image = Image.new("1", (384, 2), 1); image.putpixel((0, 0), 0)
    data, rows = mx06.build_job(image)
    opening = (mx06.command(0xA3, b"\x00") + mx06.command(0xA4, b"\x32") + mx06.command(0xAF, b"\xff\xff")
               + mx06.command(0xBE, b"\x01") + mx06.command(0xA6, bytes.fromhex("aa551738445f5f5f44382c")))
    closing = (mx06.command(0xBD, bytes([25])) + mx06.command(0xA1, b"\x30\x00") * 3
               + mx06.command(0xA6, bytes.fromhex("aa5517000000000000 0017".replace(" ", ""))) + mx06.command(0xA3, b"\x00"))
    assert rows == 2 and data.startswith(opening) and data.endswith(closing)
    assert data[len(opening):-len(closing)] == mx06.encode_row([1] + [0] * 383) + mx06.encode_row([0] * 384)

def test_a_job_refuses_an_image_of_the_wrong_width():
    import mx06
    with pytest.raises(ValueError): mx06.build_job(Image.new("1", (400, 2), 1))
```

- [ ] **Step 2:** run `pytest -q -k "printer_command or run_length or a_job"`; expect failures for a missing `mx06` module.
- [ ] **Step 3: Implement the three functions in `mx06.py`.** Frame: `51 78`, code, `00`, payload length low byte, high byte, payload, CRC-8 (polynomial `0x07`, initial 0) of the payload, `ff`. Run lengths: one byte per run, top bit is the pixel value, low 7 bits the length, runs longer than 127 split; if the encoding is longer than 48 bytes send the row raw as 48 bytes, least significant bit = leftmost pixel. In a mode `"1"` image a pixel value of 0 is black. Credit rbaron/catprinter (MIT) for the command bytes in the module docstring.
- [ ] **Step 4:** run `pytest -q`; expect all green.
- [ ] **Step 5:** commit `feat: mx06.py builds print jobs for the MX06`.

### Task 4: Sending a job, obeying pause

**Files:**
- Modify: `mx06.py`
- Test: `test_spectra6.py`

**Interfaces:**
- Consumes: `build_job`, `PAUSE`, `RESUME`, `STATE_REPLY_PREFIX` from Task 3.
- Produces: `class PrinterNotFound(Exception)`, `class PrintTimedOut(Exception)`, `class PrinterLost(Exception)`, `class BluetoothUnavailable(Exception)`; `async send_job(client, data: bytes, rows: int, on_progress=None, chunk_gap: float = 0.02, closing_timeout: float | None = None) -> None`; `async print_image(image, name: str = "MX06", on_progress=None, scan_timeout: float = 10.0, find_device=None, connect=None) -> None`. `on_progress(stage: str, sent: int, total: int)` with stages `"scanning"`, `"sending"`, `"finishing"` and no others. `find_device(name: str, timeout: float)` is a coroutine returning a device or `None`; `connect(device)` returns an async context manager that yields the client. `client` needs `mtu_size`, `async start_notify(uuid, callback)`, `async write_gatt_char(uuid, data, response=False)`.

- [ ] **Step 1: Write the failing tests.** One fake in the test file:

```python
class FakePrinter:
    """Stands in for a connected bleak client. `script` maps a write count to notifications fired after that write."""
    mtu_size = 103                                   # chunks of 100
    def __init__(self, script=None, fail_at=None): self.script, self.fail_at, self.writes, self.notify = script or {}, fail_at, [], None
    async def start_notify(self, uuid, callback): self.notify = callback
    async def write_gatt_char(self, uuid, data, response=False):
        if self.fail_at is not None and len(self.writes) >= self.fail_at: raise OSError("not connected")
        self.writes.append(bytes(data))
        for note in self.script.get(len(self.writes), []): self.notify(uuid, bytearray(note))
```

```python
STATE = bytes.fromhex("5178a3010300000200aaff")

def test_sender_waits_while_the_printer_says_pause():
    import mx06, asyncio
    p = FakePrinter({1: [STATE], 2: [mx06.PAUSE], 5: [STATE]})
    async def run():
        task = asyncio.create_task(mx06.send_job(p, bytes(500), rows=10, chunk_gap=0, closing_timeout=1))
        await asyncio.sleep(0.2); held = len(p.writes)
        p.notify(None, bytearray(mx06.RESUME)); await task
        return held
    assert asyncio.run(run()) == 2 and len(p.writes) == 5        # nothing sent between pause and resume

def test_sender_finishes_at_the_second_state_reply_not_at_resume():
    import mx06, asyncio
    p = FakePrinter({1: [STATE], 5: [mx06.RESUME]})                # every byte sent, but no closing reply
    with pytest.raises(mx06.PrintTimedOut): asyncio.run(mx06.send_job(p, bytes(500), rows=10, chunk_gap=0, closing_timeout=0.2))

def test_sender_reports_a_printer_that_drops_off():
    import mx06, asyncio
    with pytest.raises(mx06.PrinterLost): asyncio.run(mx06.send_job(FakePrinter({1: [STATE]}, fail_at=2), bytes(500), rows=10, chunk_gap=0, closing_timeout=1))

def test_print_image_says_when_the_printer_is_not_found():
    import mx06, asyncio
    async def nothing(name, timeout): return None
    with pytest.raises(mx06.PrinterNotFound): asyncio.run(mx06.print_image(Image.new("1", (384, 2), 1), find_device=nothing))

def test_print_image_reports_progress_to_the_end():
    import mx06, asyncio, contextlib
    p = FakePrinter({1: [STATE], 2: [STATE]}); stages = []
    async def found(name, timeout): return "device"
    @contextlib.asynccontextmanager
    async def connect(device): yield p
    asyncio.run(mx06.print_image(Image.new("1", (384, 2), 1), find_device=found, connect=connect,
                                 on_progress=lambda stage, sent, total: stages.append(stage)))
    assert stages[0] == "scanning" and "sending" in stages and stages[-1] == "finishing"
```

- [ ] **Step 2:** run `pytest -q -k "sender or print_image"`; expect failures for missing names.
- [ ] **Step 3: Implement.** `send_job`: subscribe to `0000ae02-0000-1000-8000-00805f9b34fb`; write to `0000ae01-…` without response in chunks of `mtu_size - 3`; before each chunk wait while paused; sleep `chunk_gap` after each; a write that raises becomes `PrinterLost`; after the last chunk wait for the second notification starting with `STATE_REPLY_PREFIX`, for `closing_timeout` seconds (default `rows / 30 + 20`), else `PrintTimedOut`. `print_image`: default `find_device` scans by name with bleak; default `connect` is `bleak.BleakClient` plus the MTU step Task 1 proved; a failed `import bleak` or a bleak/D-Bus error on scan or connect becomes `BluetoothUnavailable` carrying the original message.
- [ ] **Step 4:** run `pytest -q`; expect all green.
- [ ] **Step 5:** commit `feat: send print jobs over Bluetooth, pausing when the printer asks`.

### Task 5: Dashboard routes

**Files:**
- Modify: `dashboard.py` (new routes beside the delete-all routes near line 3935; state beside `delete_progress` near line 3687), `settings_defaults.py`, `requirements.txt`
- Test: `test_spectra6.py`

**Interfaces:**
- Consumes: `photostrip.make_strip`, `photostrip.UnreadablePhoto`, `mx06.print_image` and its four exceptions.
- Produces: `MAX_STRIP_PHOTOS = 8`; `print_progress = {"status": "idle", "message": "", "sent": 0, "total": 0}` with status one of `idle`, `scanning`, `sending`, `finishing`, `done`, `error`; `POST /api/print-strip/start` with body `{"photo_ids": [...]}`; `GET /api/print-strip/progress`; `POST /api/photos/delete` with body `{"photo_ids": [...]}` returning `{"deleted": [...], "failed": [{"id": ..., "error": ...}]}`; setting `printer.name` (default `"MX06"`, text, 1 to 32 characters).

- [ ] **Step 1: Write the failing tests.** Use `TestClient(dashboard.app)`, `monkeypatch.setattr(dashboard, "PHOTOS_PATH", str(tmp_path))` with real small JPEGs written there, and replace `dashboard.mx06.print_image` with a recording coroutine. Reset `dashboard.print_progress` to idle at the start of each. Tests and their assertions:
  - `test_print_strip_is_refused_for_no_photos_or_too_many`: `[]` and nine ids each give 400.
  - `test_print_strip_is_refused_for_an_id_that_is_not_a_number`: `["../settings"]` gives 400 and no file outside `PHOTOS_PATH` is opened.
  - `test_print_strip_names_a_photo_that_is_missing_or_unreadable`: an id with no file gives 400 with the id in `detail`; a 0-byte file gives 400 with the id in `detail`; `print_image` was not called.
  - `test_print_strip_prints_the_photos_in_the_order_given`: ids `["00002", "00001"]` give 200; after the background task, `print_image` got an image equal to `photostrip.make_strip` of those two paths in that order, and the printer name from settings; progress status is `done`.
  - `test_a_second_print_is_refused_while_one_runs`: with `print_progress["status"] = "sending"`, start gives 409.
  - `test_a_failed_print_says_why_and_frees_the_printer` (parametrised over `PrinterNotFound`, `PrinterLost`, `PrintTimedOut`, `BluetoothUnavailable`): progress ends as `error` with a message that contains, in turn, "not found", "may be incomplete", "may be incomplete", "Bluetooth"; a following start is accepted.
  - `test_delete_selected_deletes_only_the_ids_given`: with a fake `reframe_client.delete` recording paths, ids `["00001", "00003"]` produce exactly `/photos/00001` and `/photos/00003`, and the reply lists both under `deleted`.
  - `test_delete_selected_reports_a_photo_that_is_gone_and_deletes_the_rest`: the fake raises for `00002`; the reply has it under `failed` and the others under `deleted`.
  - `test_delete_selected_refuses_an_empty_list_and_bad_ids`: `[]` and `["a/b"]` give 400 and nothing is deleted.
  - `test_printer_name_setting`: `merge_defaults({})["printer"]["name"] == "MX06"`; `validate_settings` rejects `""` and a 33-character name.
- [ ] **Step 2:** run `pytest -q -k "print_strip or second_print or failed_print or delete_selected or printer_name"`; expect failures.
- [ ] **Step 3: Implement.** Add `import mx06` and `import photostrip` at the top of `dashboard.py` (the tests replace `dashboard.mx06.print_image`). Validate ids with `^[0-9]{1,10}$` before any path is built; a photo's path is `os.path.join(PHOTOS_PATH, f"{id}.jpg")`. Build the strip with `asyncio.to_thread(photostrip.make_strip, paths)` inside the start route, so an unreadable photo is a 400 before the job starts. Run the print with FastAPI `BackgroundTasks`, like `delete_photos_background`. Map `on_progress` stages to `print_progress`. Always leave `print_progress` at `done` or `error` when the job ends (use `try`/`finally`). Delete calls `reframe_client.delete(f"/photos/{id}")` one id at a time. Add `"printer": {"name": "MX06"}` to `DEFAULT_SETTINGS`, the check to `validate_settings`, and `bleak` to `requirements.txt`.
- [ ] **Step 4:** run `pytest -q`; expect all green.
- [ ] **Step 5:** commit `feat: dashboard routes to print a strip and delete chosen photos`.

### Task 6: Select mode in the page

**Files:**
- Modify: `dashboard.py` (page HTML near line 1585, CSS near line 1057, script: `renderGallery()` near line 2041 and `setupPhotoCardHandlers()` near line 2218)
- Test: `test_spectra6.py`

**Interfaces:**
- Consumes: the three routes from Task 5.
- Produces (page script): `let selectMode = false; let selectedIds = [];` `toggleSelectMode()`, `togglePick(photoId)`, `applySelection()`, `printStrip()`, `deleteSelected()`. Elements: button `#select-btn`, bar `#select-bar` holding `#select-count`, `#print-strip-btn`, `#delete-selected-btn`, `#select-cancel-btn`, and status line `#print-status`.

- [ ] **Step 1: Write the failing tests**

```python
def test_dashboard_has_select_mode_controls(dashboard):
    from fastapi.testclient import TestClient
    html = TestClient(dashboard.app).get("/").text
    for needle in ('id="select-btn"', 'id="select-bar"', 'id="print-strip-btn"', 'id="delete-selected-btn"',
                   'id="select-cancel-btn"', 'id="print-status"', "/api/print-strip/start", "/api/photos/delete"):
        assert needle in html

def test_dashboard_script_is_valid_javascript(dashboard, tmp_path):
    import re, shutil, subprocess
    from fastapi.testclient import TestClient
    if not shutil.which("node"): pytest.skip("node is not installed")
    html = TestClient(dashboard.app).get("/").text
    for i, script in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
        f = tmp_path / f"s{i}.js"; f.write_text(script)
        assert subprocess.run(["node", "--check", str(f)], capture_output=True, text=True).returncode == 0
```

- [ ] **Step 2:** run `pytest -q -k "select_mode or valid_javascript"`; the first fails, the second passes (it pins the page as it is).
- [ ] **Step 3: Implement.**
  - In select mode a tap on a card calls `togglePick` and nothing else (no view, no action buttons). Outside it, cards behave as today.
  - `selectedIds` is the single source of truth. `renderGallery()` ends by calling `applySelection()`, which marks every card whose id is in `selectedIds` with its 1-based position and updates `#select-count`. This is what keeps picks through paging and the gallery's own refresh.
  - `#print-strip-btn` is disabled unless 1 to 8 photos are picked; with more than 8 the bar says "a strip holds up to 8 photos".
  - `printStrip()` posts the ids, then polls `/api/print-strip/progress` every second and shows its message in `#print-status` until `done` or `error`. The picks stay after a print so the strip can be printed again.
  - `deleteSelected()` asks `confirm("Delete N photos? This cannot be undone.")` once, posts the ids, reports any failures, clears the picks and reloads the gallery.
  - Leaving select mode clears the picks.
- [ ] **Step 4:** run `pytest -q`; expect all green.
- [ ] **Step 5:** commit `feat: pick photos in the gallery to print as a strip or delete`.

### Task 7: Bluetooth setup, installer and docs

**Files:**
- Create: `scripts/setup-printer.sh`
- Modify: `install.sh` (line 100 and lines 184 to 185), `systemd/reframe-dashboard.service` (only if Task 1 found the group is needed), `docs/software-setup.md`, `README.md`
- Test: `test_spectra6.py`

**Interfaces:**
- Produces: in `scripts/setup-printer.sh`, a filter function `bluetooth_enabled_config` (stdin to stdout) and `main`. Same shape as `scripts/setup-pisugar.sh`: `set -euo pipefail`, everything in functions, `main` runs only when the file is executed, refuses to run as root or where `raspi-config` is missing.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("before,after", [
    ("dtparam=spi=on\ndtoverlay=disable-bt\ndtoverlay=dwc2\n", "dtparam=spi=on\n#dtoverlay=disable-bt\ndtoverlay=dwc2\n"),
    ("dtparam=spi=on\n#dtoverlay=disable-bt\n", "dtparam=spi=on\n#dtoverlay=disable-bt\n"),      # a second run changes nothing
    ("dtparam=spi=on\n", "dtparam=spi=on\n"),
])
def test_printer_setup_turns_bluetooth_back_on_in_the_boot_config(before, after):
    import subprocess
    done = subprocess.run(["bash", "-c", "source scripts/setup-printer.sh && bluetooth_enabled_config"],
                          input=before, capture_output=True, text=True)
    assert done.returncode == 0 and done.stdout == after
```

Also extend `test_only_passwordless_command_is_shutdown`: it already scans `scripts/*`, so it must still pass.

- [ ] **Step 2:** run `pytest -q -k printer_setup`; expect a failure because the script does not exist yet.
- [ ] **Step 3: Implement.** This test sources the script, so write every step inside a function from the first line, with `main` run only by the guard at the bottom. Code at the top level would run on the development machine (it happened once with `setup-pisugar.sh`). `main`: back up the boot config beside itself, write `bluetooth_enabled_config`'s output over it with `sudo install`, `sudo systemctl enable --now bluetooth.service`, add the user to `bluetooth` only if Task 1 found it necessary, and end by saying a reboot is needed when the config changed. In `install.sh`, delete the `ensure_exact_boot_config_line "dtoverlay=disable-bt"` line and the `bluetooth.service` and `hciuart.service` lines from the disable list, fix the summary line that mentions Bluetooth, and call the script the way `setup-pisugar.sh` is called (failure warns, does not abort).
- [ ] **Step 4: Docs.** `docs/software-setup.md`: a "Photo strips" section (which printer, switch it on and close the phone app, how to pick and print, what the messages mean, that an empty printer may report success, `bash scripts/setup-printer.sh` on an installed camera followed by `sudo /usr/local/sbin/reframe-apply-update` for `bleak`). `README.md`: one line under "Using the Camera".
- [ ] **Step 5:** run `pytest -q` and `bash -n scripts/setup-printer.sh install.sh`; expect all green.
- [ ] **Step 6:** commit `feat: Bluetooth setup for the printer; the installer leaves Bluetooth on`.

### Task 8: On the device, then the PR

**Files:** none new.

- [ ] **Step 1:** `git push -u origin feat/photo-strip`. On the Pi: `git fetch && git switch feat/photo-strip`. Zeke runs `sudo /usr/local/sbin/reframe-apply-update` and restarts or reboots.
- [ ] **Step 2 (Zeke, one checklist):**
  1. Pick one photo and print: the strip comes out whole.
  2. Pick eight, across two gallery pages, in a deliberate order: all eight print in that order.
  3. Start a strip and press the shutter while it prints: the photo still appears on the panel.
  4. Switch the printer off and print: the page says it was not found, within about 10 seconds.
  5. Pick three photos, wait for the gallery to refresh itself, change page and come back: the picks and numbers are still there.
  6. Delete two picked photos: those two are gone and nothing else. Delete the photo showing on the panel: the panel moves to the newest one left.
- [ ] **Step 3:** fix what the checklist turns up, test-first where it can be tested off the device.
- [ ] **Step 4:** open the PR with `gh pr create --repo zekefiddler/reframe --base feat/spectra6-cam-port --head feat/photo-strip`. The body says what changed, the on-device results, what is unverified, and `Refs zekefiddler/Epaper-Screens#3`.
- [ ] **Step 5:** request Copilot's review through the API (`gh api -X POST repos/zekefiddler/reframe/pulls/<n>/requested_reviewers -f 'reviewers[]=copilot-pull-request-reviewer[bot]'`), read the review body (a quota refusal is not a review), and triage every comment. Do not merge without Zeke.
