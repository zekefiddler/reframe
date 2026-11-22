# Photo strips on the cat printer, and deleting chosen photos

Status: design, 2026-11-21. Not built. Builds on the spectra6-cam port
(`docs/superpowers/specs/2026-10-18-spectra6-cam-port-design.md`, PR #1).
Tracking: [Epaper-Screens#3](https://github.com/zekefiddler/Epaper-Screens/issues/3).

## Intent

Zeke wants the camera to print photobooth strips: pick photos in the dashboard
and have them come out of his thermal "cat printer" over Bluetooth as one
vertical strip. While picking photos he also wants to be able to delete the
ones he chooses, which the dashboard cannot do today (it only has "delete all
photos").

Why was not discussed beyond the idea itself. What is known: a test strip of
five photos printed from the Mac on 2026-11-21 and Zeke said it "looks great";
he first wanted exactly five photos and then called a variable count "a cool
idea".

## Goals

1. **Select mode in the gallery.** A "select" button above the gallery turns
   taps on photos into picks. Each picked photo shows its number, counting in
   the order tapped. Tapping a picked photo unpicks it and the numbers close
   up. Picks survive moving between gallery pages. "Cancel" leaves the mode and
   forgets the picks.

   ```
   [ select ]
   ┌──────┐ ┌──────┐ ┌──────┐
   │  ①   │ │      │ │  ②   │
   └──────┘ └──────┘ └──────┘
   ┌──────┐ ┌──────┐ ┌──────┐
   │      │ │  ③   │ │      │
   └──────┘ └──────┘ └──────┘
   3 selected   [ print strip ]   [ delete ]   [ cancel ]
   ```

2. **Print strip.** Prints the picked photos, 1 to 8 of them, top to bottom in
   the order tapped, on the MX06 printer.
   - The strip looks like the 2026-11-21 test strip (see "The print").
   - The page shows progress (looking for the printer, sending, done) and says
     plainly what went wrong when it fails.
   - The camera keeps taking photos while a strip prints.
   - One strip at a time. A second request while one is printing is refused
     with a message.
3. **Delete selected.** Deletes the picked photos after one confirmation that
   names how many. It removes what the camera service's existing delete
   removes: the original, the dithered copy and the rotation sidecar. "Delete
   all photos" stays as it is.
4. **Bluetooth on the Pi.** Bluetooth is switched on, the installer stops
   switching it off, and an already-installed camera can be set up without
   re-running the whole installer.

## Non-goals

- Printing by itself (a photobooth mode that prints every N photos) or from the
  camera's button. Both were offered and Zeke chose the dashboard.
- Other printer models. Zeke's catprinter skill covers a different printer
  (MXW01) with a different protocol; this is for the MX06 on his desk.
- Captions, dates, QR codes or borders with text on the strip.
- Controls for darkness, contrast or dithering style. The test strip's values
  are fixed.
- A print queue, reprints, or cancelling a strip part-way.
- A per-photo delete button on every card.

## Anti-goals

- **A printer or Bluetooth problem must never stop or delay the shutter.**
  No printing or Bluetooth code may run in the camera service (`reframe.py`) or
  wait on its operation lock. On 2026-11-21 one blocking call in that service
  froze the camera. (Deleting is different: see "Decided how".)
- **Deleting by accident.** No photo is deleted without the confirmation, and
  printing never changes or removes a photo.
- **A heavy stack.** The Pi has 415 MB of RAM and about 100 MB free. No OpenCV
  and no numpy for printing; one new pip dependency (`bleak`).
- **New paths to root.** No sudo rules and no root helpers for printing. The
  dashboard talks to BlueZ as the camera's ordinary user.
- **Wasted paper in silence.** A print that fails says so. Nothing retries
  automatically, because a retry reprints from the top.

## Constraints

| | |
|---|---|
| Printer | "MX06" (its Bluetooth name). 384 dots wide, 8 dots/mm, black or white only. One connection at a time, so the phone app must be closed. It must be on and in range. |
| Printer protocol | The "classic" cat printer protocol: `51 78` command frames written to characteristic AE01, replies on AE02. Not the MXW01 protocol. |
| Flow control | The printer notifies `51 78 ae 01 01 00 10 70 ff` when its buffer is full and `51 78 ae 01 01 00 00 00 ff` when there is room. Data sent in between is dropped. Ignoring this printed one photo out of five. |
| End of job | The job ends with a state query (`51 78 a3`). The printer's reply to it, the second `a3` reply of the job, is the only reliable "finished". |
| Proven so far | Two throwaway scripts printed a full five-photo strip on this printer **from the Mac**: `2026-11-21-photo-strip-spike/make_strip.py` made the image and `print_classic_fc.py` sent it. Nothing has printed from the Pi. |
| The Pi | Zero 2 W, Pi OS Trixie, BlueZ 5.82 and the Bluetooth firmware are installed. The installer turned Bluetooth off (`dtoverlay=disable-bt`, `bluetooth.service` disabled) to speed up boot. `bleak` is not installed. |
| BlueZ quirk | rbaron/catprinter notes that BlueZ reports an MTU of 23 until `client._acquire_mtu()` is called. Chunks must fit the real MTU. |
| Access | Sudo on the Pi asks for a password, so Zeke runs every sudo step. There is no USB fallback: a change that needs a reboot is a risk to the only way in (Wi-Fi). |
| Dashboard | No login; anyone on the Wi-Fi can already take photos and delete all of them. Print and delete-selected have the same exposure. |

## Approaches

Where the printing runs:

1. **Inside the dashboard service (chosen).** The dashboard is an asyncio
   FastAPI app that already runs background jobs with a progress endpoint
   ("download all", "delete all"). `bleak` is asyncio-native, so a print is one
   more such job. The camera service is not touched.
2. **A helper program started for each print.** Better isolation and its
   memory is returned afterwards, but it needs its own channel to report
   progress and errors. More parts, and no problem yet that it solves.
3. **Inside the camera service.** Rejected: it is the process that must never
   block, and Bluetooth calls can hang.

The printer driver:

- **Our own short driver (chosen),** adapted from the script that printed the
  test strip: the command bytes follow rbaron/catprinter (MIT), the pause and
  resume handling follows NaitLee/Cat-Printer.
- Using rbaron/catprinter whole was rejected: it requires OpenCV and numpy.

Claude chose both; Zeke left the how to Claude.

## The print

The values that produced the strip Zeke approved:

- Strip width 384 px. White margin 16 px on every side and 16 px between
  photos.
- Each photo is scaled to 352 px wide, keeping its own shape. A landscape photo
  becomes 352 × 234; a portrait photo becomes taller. (The test only had
  landscape photos; keeping the shape for portrait is Claude's call.)
- Tone: greyscale, autocontrast with a 1% cutoff, then midtones lifted with
  gamma 0.75, because thermal paper prints dark.
- Floyd–Steinberg dither to black and white (Pillow's `convert("1")`).
- Printer settings: quality `0x32`, energy `0xFFFF`, then 25 lines of feed.
- Photos print as stored. A photo that is upside down in the gallery is upside
  down on the strip.

A five-photo strip is 48 × 158 mm and took 26 s, 17 s of it waiting on the
printer's pause signal.

**The two spike scripts are the reference for all of this.** Details that
exist only there: the tone steps run on each photo before it is scaled; scaling
is Lanczos; the job is sent in chunks of MTU minus 3 bytes, 20 ms apart,
written without response; the closing commands are feed 25, "set paper" three
times, lattice end, state query; the wait for the closing reply is
rows / 30 + 20 seconds. They are throwaway code kept for reference, not
product code: they connect by address (no scan by name) and have no MTU fix
for BlueZ.

## Decided how

**Zeke's calls**
- The print is started from the dashboard.
- The photos on the strip are chosen, not "the last five".
- Photos can be deleted from the dashboard.
- The number of photos on a strip can vary.
- **Strips are made from each photo's original JPEG,** not the six-colour
  dithered copy. On 2026-11-22 he compared three versions of two photos on
  paper (from the original; from the six-colour copy with the same steps; from
  the six-colour copy with its dots kept) and judged the original the best
  looking.

**Claude's calls (not confirmed one by one; Zeke saw the list and objected to
none)**
- 1 to 8 photos per strip, in the order tapped.
- One select mode serves both printing and deleting.
- Delete asks once, and removes original, dithered copy and sidecar.
- **Delete-selected calls the camera service's existing delete**
  (`DELETE /api/photos/{id}`), one photo at a time, the way "delete all"
  already does. There is one delete in the code, and it holds the operation
  lock for milliseconds. The cost: with the camera service stuck or restarting,
  deleting fails with a message until it is back.
- The printer is found by scanning for its name on every print (10 s limit).
  The name is a setting, `printer.name`, default `MX06`. No pairing and no
  stored address.
- Sending stops while the printer says "pause" and the job is finished only at
  the second `a3` reply, with a time limit that grows with the strip's length.
  On a time-out the page says the strip may be incomplete.
- **Bluetooth setup:** a script, `scripts/setup-printer.sh`, removes
  `dtoverlay=disable-bt` and enables `bluetooth.service`, in the style of
  `scripts/setup-pisugar.sh` (functions behind a main guard, refuses to run off
  a Pi). `install.sh` stops disabling Bluetooth and calls the script. On Zeke's
  Pi only the script is run.
- `bleak` goes in `requirements.txt`, so `reframe-apply-update` installs it.
- This work is a branch of its own, `feat/photo-strip`, on top of the port
  branch.

## Left to the builder

- Module layout, as long as it stays small and flat like the rest of the repo.
- The exact look of the select mode, within the dashboard's existing style.
- The wording of progress and error messages.
- Whether to add the camera's user to the `bluetooth` group and the dashboard
  unit's `SupplementaryGroups`: do it only if the first print from the Pi shows
  BlueZ refuses the user without it.
- Names of the new API routes. Suggested: `POST /api/photos/delete` with a list
  of ids, `POST /api/print-strip/start` with a list of ids, and
  `GET /api/print-strip/progress`.

## Order of work

1. **Prove a print from the Pi before building anything.** Zeke switches
   Bluetooth on (sudo and a reboot). The spike scripts are copied to the Pi and
   run by hand, changed only to scan for the printer by name and to apply the
   MTU fix, so that a failure points at the Pi and not at new code. If this fails, stop and bring it back to Zeke: the
   rest of the design assumes it works. Measure boot time before and after.
2. The printer driver and the strip maker, test-first, off the device.
3. The two API routes and the progress endpoint.
4. The select mode in the page.
5. The setup script, the installer change and the docs.
6. On-device checks with Zeke.

## Testing

Off the device, in `test_spectra6.py`:
- Command frames match known bytes (for example quality `51 78 a4 00 01 00 32
  9e ff`), and a row of pixels survives the run-length encoding.
- A strip of landscape photos has the expected size; order follows the list
  given; a portrait photo keeps its shape; the result is 384 px wide and pure
  black and white.
- The sender, against a fake printer: it sends nothing between "pause" and
  "resume"; it finishes at the second `a3` reply and not at "resume"; it
  reports a time-out when the reply never comes; it reports "printer not found"
  when the scan finds nothing.
- The routes: 0 or 9 photos are refused; an unknown id is refused; a second
  print while one runs is refused; delete-selected deletes exactly the ids
  given.
- The page's inline JavaScript passes `node --check`.

On the device, with Zeke:
- A one-photo strip and an eight-photo strip come out whole.
- A strip prints while a photo is taken, and the photo still appears on the
  panel.
- With the printer off, the page says it was not found within about 10 s.
- Delete-selected removes the chosen photos and nothing else; deleting the
  photo on the panel moves the panel to the newest remaining one.
- Boot time with Bluetooth on.

## Open

- **Printing from the Pi is unproven.** Step 1 exists to settle it.
- The printer's closing state reply carried a different status byte (`0x80`)
  from its opening one (`0x00`). Its meaning is not known. Out-of-paper and
  open-cover detection are therefore not designed; a strip sent to an empty
  printer may report success.
- How much Bluetooth adds to boot time. The installer turned it off for that
  reason.
