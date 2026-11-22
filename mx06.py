"""Print a 384-dot black-and-white image on an MX06 "cat printer" over Bluetooth LE.

The MX06 speaks the classic cat-printer protocol: `51 78` command frames written
to characteristic AE01, replies on AE02. The command bytes follow
rbaron/catprinter (MIT); the pause/resume flow control follows
NaitLee/Cat-Printer. See docs/superpowers/specs/2026-11-21-photo-strip-and-delete-design.md.
"""
import asyncio
import contextlib
import logging

WIDTH = 384
QUALITY = 0x32
FEED_LINES = 25
LATTICE_START = bytes.fromhex("aa551738445f5f5f44382c")
LATTICE_END = bytes.fromhex("aa55170000000000000017")

PAUSE = bytes.fromhex("5178ae0101001070ff")    # printer buffer full: stop sending
RESUME = bytes.fromhex("5178ae0101000000ff")   # room again
STATE_REPLY_PREFIX = bytes.fromhex("5178a3")


def _crc8(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def command(code, payload):
    """One frame: 51 78, code, 00, length (little-endian), payload, CRC-8 of payload, ff."""
    n = len(payload)
    return bytes([0x51, 0x78, code, 0x00, n & 0xFF, n >> 8]) + payload + bytes([_crc8(payload), 0xFF])


def encode_row(row):
    """One row of 384 pixels (1 = black) as one command: run lengths, or raw bits if that is shorter."""
    runs = []
    count, last = 0, None
    for value in list(row) + [None]:
        if value == last:
            count += 1
            continue
        while last is not None and count > 0:
            step = min(count, 0x7F)
            runs.append((last << 7) | step)
            count -= step
        count, last = 1, value
    if len(runs) <= WIDTH // 8:
        return command(0xBF, bytes(runs))
    raw = bytes(sum(1 << bit for bit in range(8) if row[i + bit]) for i in range(0, WIDTH, 8))
    return command(0xA2, raw)


def build_job(image, energy=0xFFFF):
    """The whole print job for a mode "1" image 384 px wide. Returns (data, row count)."""
    if image.width != WIDTH:
        raise ValueError(f"image must be {WIDTH} px wide, not {image.width}")
    image = image.convert("1")
    pixels = image.load()
    data = bytearray(
        command(0xA3, b"\x00")
        + command(0xA4, bytes([QUALITY]))
        + command(0xAF, bytes([energy >> 8, energy & 0xFF]))
        + command(0xBE, b"\x01")
        + command(0xA6, LATTICE_START)
    )
    for y in range(image.height):
        data += encode_row([1 if pixels[x, y] == 0 else 0 for x in range(WIDTH)])
    data += (
        command(0xBD, bytes([FEED_LINES]))
        + command(0xA1, b"\x30\x00") * 3
        + command(0xA6, LATTICE_END)
        + command(0xA3, b"\x00")
    )
    return bytes(data), image.height


# ── Sending over Bluetooth LE ─────────────────────────────────────────────────

NAME = "MX06"
TX = "0000ae01-0000-1000-8000-00805f9b34fb"   # commands, written without response
RX = "0000ae02-0000-1000-8000-00805f9b34fb"   # replies and pause/resume
SCAN_ATTEMPTS = 2   # single 10 s scans missed a nearby MX06 on the Pi 2 times in 5
PAUSE_TIMEOUT_SECONDS = 30


class PrinterNotFound(Exception):
    pass


class PrintTimedOut(Exception):
    pass


class PrinterLost(Exception):
    pass


class BluetoothUnavailable(Exception):
    pass


async def send_job(client, data, rows, on_progress=None, chunk_gap=0.02, closing_timeout=None,
                   pause_timeout=PAUSE_TIMEOUT_SECONDS):
    """Send a job to a connected printer, waiting whenever it says pause.

    Done is the printer's reply to the job's closing state query: the second
    state reply of the job. RESUME looks like "ready" but is not the end.
    """
    paused = asyncio.Event()
    finished = asyncio.Event()
    state_replies = 0

    def on_reply(_sender, payload):
        nonlocal state_replies
        payload = bytes(payload)
        if payload == PAUSE:
            paused.set()
        elif payload == RESUME:
            paused.clear()
        elif payload.startswith(STATE_REPLY_PREFIX):
            state_replies += 1
            if state_replies >= 2:
                finished.set()

    await client.start_notify(RX, on_reply)
    size = client.mtu_size - 3
    for start in range(0, len(data), size):
        waited = 0.0
        while paused.is_set():
            if waited >= pause_timeout:
                raise PrintTimedOut(f"the printer paused and did not resume within {pause_timeout:.0f} s")
            await asyncio.sleep(0.05)
            waited += 0.05
        try:
            await client.write_gatt_char(TX, data[start:start + size], response=False)
        except Exception as e:
            raise PrinterLost(str(e)) from e
        if on_progress:
            on_progress("sending", min(start + size, len(data)), len(data))
        await asyncio.sleep(chunk_gap)
    if on_progress:
        on_progress("finishing", len(data), len(data))
    limit = rows / 30 + 20 if closing_timeout is None else closing_timeout
    try:
        await asyncio.wait_for(finished.wait(), limit)
    except asyncio.TimeoutError:
        raise PrintTimedOut(f"no reply from the printer within {limit:.0f} s after sending") from None


async def _scan(name, timeout):
    from bleak import BleakScanner
    return await BleakScanner.find_device_by_filter(lambda d, ad: (d.name or "") == name, timeout=timeout)


@contextlib.asynccontextmanager
async def _connect(device):
    from bleak import BleakClient
    async with BleakClient(device) as client:
        backend = getattr(client, "_backend", None)
        if type(backend).__name__ == "BleakClientBlueZDBus":
            await backend._acquire_mtu()   # BlueZ reports an MTU of 23 until asked (found on the Pi)
        yield client


async def print_image(image, name=NAME, on_progress=None, scan_timeout=10.0, find_device=None, connect=None):
    """Find the printer by name, connect, and print a 384 px wide image."""
    data, rows = build_job(image)
    find_device = find_device or _scan
    connect = connect or _connect
    try:
        device = None
        for _ in range(SCAN_ATTEMPTS):
            if on_progress:
                on_progress("scanning", 0, len(data))
            device = await find_device(name, scan_timeout)
            if device is not None:
                break
        if device is None:
            raise PrinterNotFound(f"no printer named {name} found")
        printed = False
        async with connect(device) as client:
            await send_job(client, data, rows, on_progress)
            printed = True
    except (PrinterNotFound, PrintTimedOut, PrinterLost):
        raise
    except Exception as e:
        if printed:   # the strip is out; a failed disconnect afterwards does not matter
            logging.warning("Printer disconnect failed after a finished print: %s", e)
            return
        logging.exception("Bluetooth error while printing")
        raise BluetoothUnavailable(f"{type(e).__name__}: {e}") from e
