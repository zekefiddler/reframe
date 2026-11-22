"""One-off: print a 384 px wide image on a classic-protocol cat printer (MX06 here).
Byte sequences follow rbaron/catprinter's cmds.py and ble.py (MIT), read 2026-11-21."""
import asyncio, sys
from bleak import BleakClient
from PIL import Image
U = lambda s: "0000%s-0000-1000-8000-00805f9b34fb" % s
READY = bytes.fromhex("5178ae0101000000ff")
def crc8(d):
    c = 0
    for b in d:
        c ^= b
        for _ in range(8): c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c
def cmd(code, payload):
    return bytes([0x51, 0x78, code, 0x00, len(payload) & 0xFF, len(payload) >> 8]) + payload + bytes([crc8(payload), 0xFF])
# self-check against constants in the reference
assert cmd(0xA4, b"\x32") == bytes.fromhex("5178a4000100329eff")
assert cmd(0xA1, b"\x30\x00") == bytes.fromhex("5178a10002003000f9ff")
LATTICE_START = cmd(0xA6, bytes.fromhex("aa551738445f5f5f44382c")); assert LATTICE_START[-2] == 0xA1
LATTICE_END = cmd(0xA6, bytes.fromhex("aa5517000000000000 0017".replace(" ", ""))); assert LATTICE_END[-2] == 0x11
def rle(row):
    out, n, last = [], 0, None
    def flush(n, v):
        while n > 0x7F: out.append(0x7F | (v << 7)); n -= 0x7F
        if n > 0: out.append((v << 7) | n)
    for v in row:
        if v == last: n += 1
        else:
            if last is not None: flush(n, last)
            n, last = 1, v
    flush(n, last)
    return bytes(out)
def row_cmd(row):
    enc = rle(row)
    if len(enc) > 48:
        raw = bytes(sum((1 << b) for b in range(8) if row[i + b]) for i in range(0, 384, 8))
        return cmd(0xA2, raw)
    return cmd(0xBF, enc)
def job(path, energy):
    img = Image.open(path).convert("1"); assert img.width == 384, img.size
    px = img.load()
    rows = [[1 if px[x, y] == 0 else 0 for x in range(384)] for y in range(img.height)]
    data = cmd(0xA3, b"\x00") + cmd(0xA4, b"\x32") + cmd(0xAF, bytes([energy >> 8, energy & 0xFF])) + cmd(0xBE, b"\x01") + LATTICE_START
    for r in rows: data += row_cmd(r)
    data += cmd(0xBD, bytes([25])) + cmd(0xA1, b"\x30\x00") * 3 + LATTICE_END + cmd(0xA3, b"\x00")
    return data, len(rows)
PAUSE = bytes.fromhex("5178ae0101001070ff")   # printer buffer full: stop sending
RESUME = bytes.fromhex("5178ae0101000000ff")  # room again
async def main(addr, path, energy):
    data, n = job(path, energy)
    async with BleakClient(addr) as c:
        st = {"paused": False, "states": 0, "pauses": 0}
        done = asyncio.Event(); seen = []
        def on(_c, d):
            d = bytes(d); seen.append(d.hex(" "))
            if d == PAUSE:
                st["paused"] = True; st["pauses"] += 1
            elif d == RESUME:
                st["paused"] = False
            elif d[:3] == bytes.fromhex("5178a3"):
                st["states"] += 1
                if st["states"] >= 2: done.set()      # reply to the state query that ends the job
        await c.start_notify(U("ae02"), on)
        size = c.mtu_size - 3
        print("connected; %d rows, %d bytes, chunks of %d" % (n, len(data), size))
        waited = 0.0
        for i in range(0, len(data), size):
            while st["paused"]:
                await asyncio.sleep(0.05); waited += 0.05
            await c.write_gatt_char(U("ae01"), data[i:i + size], response=False)
            await asyncio.sleep(0.02)
        print("all data sent; paused %d times for %.1f s in total" % (st["pauses"], waited))
        try:
            await asyncio.wait_for(done.wait(), n / 30 + 20); print("printer answered the closing state query (done)")
        except asyncio.TimeoutError:
            print("no closing reply within %.0f s" % (n / 30 + 20))
        print("last notifications:", seen[-4:])
asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3], 0) if len(sys.argv) > 3 else 0xFFFF))
