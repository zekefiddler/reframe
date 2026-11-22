"""Print a 384-dot black-and-white image on an MX06 "cat printer" over Bluetooth LE.

The MX06 speaks the classic cat-printer protocol: `51 78` command frames written
to characteristic AE01, replies on AE02. The command bytes follow
rbaron/catprinter (MIT); the pause/resume flow control follows
NaitLee/Cat-Printer. See docs/superpowers/specs/2026-11-21-photo-strip-and-delete-design.md.
"""
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
