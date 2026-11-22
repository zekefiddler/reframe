"""Throwaway spike: stack photos into a 384 px wide photobooth strip for a thermal printer."""
import sys
from PIL import Image, ImageOps

WIDTH, MARGIN, GAP = 384, 16, 16
FRAME_W = WIDTH - 2 * MARGIN
FRAME_H = FRAME_W * 2 // 3          # the camera's photos are 3:2


def tone(photo):
    grey = ImageOps.autocontrast(photo.convert("L"), cutoff=1)
    return grey.point(lambda v: round(255 * (v / 255) ** 0.75))   # lift the midtones: thermal paper prints dark


def strip(paths):
    height = 2 * MARGIN + len(paths) * FRAME_H + (len(paths) - 1) * GAP
    sheet = Image.new("L", (WIDTH, height), 255)
    for i, path in enumerate(paths):
        frame = ImageOps.fit(tone(Image.open(path)), (FRAME_W, FRAME_H), Image.LANCZOS)
        sheet.paste(frame, (MARGIN, MARGIN + i * (FRAME_H + GAP)))
    return sheet.convert("1")        # Floyd-Steinberg dither to black and white dots


if __name__ == "__main__":
    out, paths = sys.argv[1], sys.argv[2:]
    s = strip(paths)
    s.save(out)
    print(out, s.size, "= %.0f x %.0f mm at 8 dots/mm" % (s.width / 8, s.height / 8))
