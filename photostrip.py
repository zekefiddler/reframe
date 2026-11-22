"""Photos to one black-and-white photobooth strip for a 384-dot thermal printer.

The look (borders, tone, dither) is the test strip Zeke approved on paper on
2026-11-21; see docs/superpowers/specs/2026-11-21-photo-strip-and-delete-design.md.
"""
from PIL import Image, ImageOps

STRIP_WIDTH = 384
MARGIN = 16
GAP = 16
FRAME_WIDTH = STRIP_WIDTH - 2 * MARGIN   # 352
GAMMA = 0.75                             # lifts the midtones: thermal paper prints dark


class UnreadablePhoto(Exception):
    def __init__(self, path):
        super().__init__(f"cannot read {path}")
        self.path = path


def _frame(path):
    try:
        with Image.open(path) as photo:
            grey = photo.convert("L")
    except Exception as e:
        raise UnreadablePhoto(path) from e
    grey = ImageOps.autocontrast(grey, cutoff=1)
    grey = grey.point(lambda v: round(255 * (v / 255) ** GAMMA))
    width, height = grey.size
    return grey.resize((FRAME_WIDTH, FRAME_WIDTH * height // width), Image.LANCZOS)


def make_strip(paths):
    """Stack the photos top to bottom, in the order given, as a mode "1" image."""
    frames = [_frame(path) for path in paths]
    height = 2 * MARGIN + sum(f.height for f in frames) + GAP * (len(frames) - 1)
    sheet = Image.new("L", (STRIP_WIDTH, height), 255)
    y = MARGIN
    for frame in frames:
        sheet.paste(frame, (MARGIN, y))
        y += frame.height + GAP
    return sheet.convert("1")   # Floyd-Steinberg dither
