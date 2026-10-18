# spectra6-cam: porting reFrame to Zeke's build

Fork of [kaloyaan/reframe](https://github.com/kaloyaan/reframe) at `e86aebc`.
Tracking: [Epaper-Screens#3](https://github.com/zekefiddler/Epaper-Screens/issues/3).
Status: design, 2026-10-18.

## Intent

Zeke is building reFrame's camera: an e-paper camera where you press the shutter
and the photo appears on the panel about 20 s later, and stays there with the
power off. His hardware is slightly different from upstream, and he wants two
things reFrame doesn't do: **browsing old photos on the device** (a next button
and a slideshow), and a **landscape/portrait switch**. Keep reFrame's look,
capture flow and dashboard. Change only what the hardware and those features
need.

## The build (as built, verified 2026-10-18)

| | reFrame upstream | spectra6-cam |
|---|---|---|
| Pi | Zero 2 W | Zero 2 W, hostname `spectra6-cam`, user **`youmeandbirdie`** (sudo asks for a password) |
| Panel | Waveshare 4" HAT+ (E) | same, mounted on the GPIO header, SPI on, test pattern OK |
| Camera | Module 3 (autofocus, HDR) | **Module v2, imx219**, fixed focus, no HDR |
| Power and button | PiSugar 3, button read over I2C 0x57, RTC | **Geekworm X306** UPS (no I2C, no RTC). **Two GPIO buttons:** shutter **GPIO5**, next **GPIO6**, both to GND |
| Network | Wi-Fi | Wi-Fi, **Tailscale**, and a **USB gadget** (dwc2/g_ether in cmdline, NM `netplan-usb0` at 10.55.2.1/16, `usb-gadget-reconnect.service`). None of these may be disturbed. |
| OS | Pi OS Lite | Pi OS Lite Trixie, fully upgraded. Timezone America/New_York |

## Goals

1. **GPIO buttons.** Short press of GPIO5 = shutter, as upstream (ignored while the
   display is busy). GPIO6 = next. No long-press actions in v1.
2. **Next / slideshow.**
   - Each next press shows the next older photo from `photos/`, wrapping from the
     oldest to the newest.
   - The shutter takes a photo and resets the position to the newest.
   - Optional auto-advance (`display.slideshow_interval_seconds`, 0 = off, the
     default), set from the dashboard.
   - Uses the existing dithered PNG when it exists, otherwise re-dithers with the
     *saved* settings (not the defaults; see "Upstream fixes").
3. **Orientation switch** (`display.orientation`: `landscape` | `portrait`) in the
   dashboard.
   - **The panel always gets the same pixel mapping as landscape.** The camera and
     the panel are fixed together, so the panel image stays correct in device
     coordinates whichever way the device is held. Never rotate the panel buffer
     for portrait.
   - Portrait changes only:
     - the **saved and gallery copies** (the original JPEG, the dithered PNG and
       the thumbnail), which are rotated by `display.portrait_rotation` (90 or 270)
       so they're upright on a computer;
     - the **on-panel text screens** (QR and the empty roll), which are laid out to
       read upright.
   - Record the rotation used for each photo, so "show on panel" and next/slideshow
     can turn a portrait-saved PNG back to device coordinates. Display by id
     currently squashes any PNG that isn't 600×400, so fix that too.
   - **`camera.rotate_180`** (picamera2 `Transform(hflip=1, vflip=1)`, default off):
     the case isn't designed yet, and the camera may end up mounted upside down
     relative to the panel. Check it once the case exists.
4. **Empty-roll screen.** With no photos, the panel shows *Take your first picture
   and wait for it to appear on the screen.*
5. **Camera v2.**
   - Remove the HDR helper call (it adds about 3 s to boot) and the AfMode and
     autofocus settle.
   - Replace the settle with a short AE/AWB settle, sized so the first frame isn't
     dark.
   - Drop `autofocus_mode` from the defaults and the UI. Keep the 1200×800 still
     size.
6. **No PiSugar.**
   - Remove the I2C button path and smbus2.
   - The battery reading stays "Unavailable".
   - Leave out the PiSugar installer, the RTC units and the forced UTC timezone.
7. **Default look: less contrast, more colour** (Zeke).
   - Upstream has no contrast control, so add **`processing.contrast`** (an
     `ImageEnhance.Contrast` step in both dither paths), with a dashboard slider.
     The default is below 1.0.
   - Raise the colour defaults (the saturation blend and the colour factor).
   - Tune by eye on the panel.
8. **Installer works for any user.** Replace the hardcoded `cam` and
   `/home/cam/reframe` (install.sh, units, sudoers, `scripts/reframe-python`,
   `reframe-apply-update`) with the installing user and the repo path.

## Non-goals (v1)

- Knobs or an ADC, battery level, and safe shutdown on the X306. All v2.
- Upstreaming. Write the changes so they *could* go upstream (hardware choices as
  settings or adapters rather than deletions where it's cheap), but don't block on
  it.

## Anti-goals

- **Breaking the Pi's networking.** Before running install.sh, diff `config.txt`
  and `cmdline.txt` before and after, and keep `modules-load=dwc2,g_ether` and the
  `g_ether.*` args. Tailscale and the USB link must work after the install. Run any
  step that might break this over USB or Wi-Fi so there is a way back.
- **Filling the roll with junk.** Upstream takes a startup photo on every service
  start, and a crash loop under `Restart=always` makes that a photo flood.
- **Refusing a refresh for the 180 s panel rule** (Zeke: "refresh anyway").
  Upstream already refreshes on every press; keep it.

## Claude's calls (not confirmed by Zeke)

- **No startup photo.** At service start the panel keeps what it shows, which is the
  last photo, except for the empty-roll screen. Upstream's power button doubles as
  the shutter, so a startup photo makes sense there. Here, power doesn't come from
  the shutter.
- **`auto_timeout_enabled` defaults to false.** A halt on the X306 needs a power
  cycle to recover, and the timeout's `sudo shutdown` needs passwordless sudo,
  which Zeke's user doesn't have.
- **The QR-on-Wi-Fi-change screen defaults to off.** It would interrupt a
  slideshow, and the addresses are already known (`spectra6-cam.local`, Tailscale
  100.99.67.104, USB 10.55.2.1).
- **Keep upstream's no-login dashboard** on port 80. It's only reachable on home
  Wi-Fi, the tailnet and USB.
- **Install by cloning the fork on the Pi** (`apt install git` first) into
  `~/reframe`, so the dashboard's "update" pulls from the fork.
- **installer system tweaks:** keep upstream's `disable-bt`, audio off, the service
  disables (NetworkManager owns networking here) and stripping `console=serial0`.
  Skip `do_i2c` and the UTC timezone.

## Upstream fixes found while reading (fix in the fork, offer upstream later)

- `POST /api/display/{photo_id}` (reframe.py:1691) is registered before
  `/api/display/clear` (:1842), so "clear" probably resolves as a photo id.
  Confirm, then reorder.
- `display_photo_by_id` re-dithers with the *default* settings when the PNG is
  missing (:1299). It should use the saved settings.
- The Ctrl-C handler references `epd4in0e`, which was never imported (:2073), so it
  raises NameError.
- `display_photo_by_id`, `display_dashboard_qr` and `clear_display` refresh
  synchronously without setting `_display_busy`, so the shutter isn't blocked
  correctly during those. Next and slideshow should use `display_buffer_async`.

## Working on the device

- Claude has key SSH to the Pi: `ssh spectra6-cam` (Tailscale) and
  `ssh spectra6-cam-usb` (USB). Claude can deploy code, restart user-level
  processes and read logs.
- **Zeke runs every sudo step** (`ssh -t …`), because his user's sudo asks for a
  password. Batch sudo steps into as few commands as possible.
- **Zeke is the eyes and the fingers:** button presses, judging the panel, picking
  90/270 and the tone defaults. Plan on-device checks as short checklists at set
  points, not a stream of requests.
- Also remove `timedatectl set-timezone UTC` from `scripts/reframe-apply-update`,
  not just from install.sh.

## Testing

- Upstream has no tests. Add one small `test_spectra6.py` for the new logic:
  - next-index wrap and reset on shutter
  - orientation: landscape → 600×400, portrait → 400×600, direction knob
  - empty-roll detection
  - settings defaults and validation for the new keys
- On-device checks:
  - shutter → panel shows the photo
  - next steps back and wraps
  - slideshow ticks
  - the orientation switch flips the next photo
  - empty roll shows the message
  - the dashboard downloads an original
  - the network (Wi-Fi, Tailscale, USB) still works after install and a reboot

## Reference

`docs/hardware-porting.md` (upstream) lists the adapter surfaces to keep stable.
The line references above are against `e86aebc`.
