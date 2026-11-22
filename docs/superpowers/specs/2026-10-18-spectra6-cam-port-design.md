# spectra6-cam: porting reFrame to Zeke's build

Fork of [kaloyaan/reframe](https://github.com/kaloyaan/reframe) at `e86aebc`.
Tracking: [Epaper-Screens#3](https://github.com/zekefiddler/Epaper-Screens/issues/3).
Status: design, 2026-10-18. **Amended 2026-11-21: the power board and buttons
changed; see the amendment at the end, which overrides the sections it names.**

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
| Panel | Waveshare 4" HAT+ (E) | same, but **off the header, on its 9-wire cable** (same pins: RST17, DC25, CS8, BUSY24, PWR18), so the header is free for buttons; test pattern OK (2026-10-21) |
| Camera | Module 3 (autofocus, HDR) | same, **Module 3, imx708** (swapped in for the v2 on 2026-10-21; test still OK) |
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
5. **Camera Module 3, as upstream.** Keep the autofocus, the HDR helper and the
   1200×800 still size unchanged. (This section first targeted the v2; Zeke kept the
   Module 3 on 2026-10-21.)
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

## Amendment 2026-11-21: PiSugar 3, one button

The hardware moved back to upstream's. This overrides the "Power and button" row
of the build table, goals 1 and 6, and the first three of "Claude's calls".

**What changed on the bench (Zeke)**
- The Pi sits on a **PiSugar 3** (1200 mAh, the board that used to power
  bugoutbox). The Geekworm X306 is out.
- **The next button is dropped.** Older photos are reached from the dashboard or the
  slideshow. `next_photo()` and `/api/next` stay.
- The case is **upstream's, unmodified**, wired as the build guide shows: the case
  button goes to the PiSugar's power-button pad (pad 10, the unlabelled pad by the
  reset button, not the one marked "BTN") and BAT+.
- The 4" HAT goes back on the Pi's header, as the case needs.

**Zeke's calls**
- **No startup photo.** The panel shows the **dashboard QR when the camera joins
  Wi-Fi**, and that is the sign it has booted. `show_dashboard_qr_on_wifi_connect`
  now defaults to on.
- **The camera may power itself off.** One sudo rule lets the camera's user run
  exactly `/usr/sbin/shutdown -h now` without a password. The update helper still
  gets no rule.

**Claude's calls (not confirmed by Zeke)**
- **Shutter = the PiSugar power button over I2C** (0x57, register 0x02, bit 0), as
  upstream. A press shorter than 2 s takes a photo; a longer one is left to the
  PiSugar.
- **A missing bus or a silent PiSugar must not stop the camera.** The button reads
  as "not pressed", diagnostics say so, and the dashboard shutter keeps working.
  Upstream opens the bus at startup and dies without it.
- **`auto_timeout_enabled` defaults to on** (10 minutes), as upstream.
- **PiSugar setup lives in `scripts/setup-pisugar.sh`**, which install.sh calls. On
  the installed Pi only that script is run: re-running all of install.sh risks the
  network profiles again (see "Anti-goals").
- **PiSugar settings:** one press powers on (`anti_mistouch` off); a long press
  runs a clean shutdown (`soft_poweroff` on); `pisugar-poweroff` then cuts the
  battery. **Cost:** with `soft_poweroff` on, a hung Pi cannot be turned off from
  the button; it runs until the battery is empty or the PiSugar's reset button is
  pressed inside the case.
- **pisugar-server listens on 127.0.0.1 only**, all three ports. It has no login,
  and its web page can change power settings.
- **The PiSugar's clock is not used.** Photo times can be wrong on a boot away from
  Wi-Fi. This board's clock address was moved to 0x69 while it was on bugoutbox.

**Open**
- Away from Wi-Fi nothing appears at boot, so there is no "ready" sign there.

**On-device checks to add**
- one press powers on; the QR appears once Wi-Fi is up
- a short press takes a photo; a press during a refresh is ignored
- a 2 s hold shuts down cleanly and the PiSugar's LEDs go out
- the inactivity timeout shuts the camera down
- the dashboard shows the battery level
- `ss -ltn` shows 8421 to 8423 on 127.0.0.1 only

## Reference

`docs/hardware-porting.md` (upstream) lists the adapter surfaces to keep stable.
The line references above are against `e86aebc`.
